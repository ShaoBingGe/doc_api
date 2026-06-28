"""
Per-module optimizer LLM call.

For each module in a round whose accuracy is < 1.0, build a focused prompt
containing:
  - the module's current state (description, suggestions, prompt)
  - the per-sample OCR/GT comparison
  - up to K previous iterations' history (suggestion + accuracy)

The LLM must return a JSON object with new description / suggestions /
prompt and the aggregate diff / optimization_suggestion.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .llm_failover import llm_text_completion_failover as llm_text_completion

logger = logging.getLogger(__name__)


# ── Pydantic schema: HARD-FORBID any skill-modifying fields ──────────────────
# See design §9.1 + §15.10. The LLM is told skills are read-only; any attempt
# to return new_skill_ids / new_skills / skills fields gets rejected by extra='forbid'.

class _AggregateDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    differences_description: str = ""
    differences_reason_analysis: str = ""


class _TypedEdit(BaseModel):
    """ADR-002 — one bounded edit on a field's rule section."""
    model_config = ConfigDict(extra="forbid")
    op: str                       # append | replace | delete
    target: str                   # the field's module_key
    content: str = ""             # rule text (empty for delete)
    source_type: str = "failure"  # failure | success
    kind: str = ""                # SKILL_DEFECT | EXECUTION_LAPSE | ""


class ModuleOptimizerOutput(BaseModel):
    """
    Strict schema for Module Optimizer LLM output.
    Notably DOES NOT include skill_ids / new_skill_ids / skills fields,
    and extra='forbid' rejects them at parse time.
    Optimizer may only comment on skills via skill_feedback (free text).
    """
    model_config = ConfigDict(extra="forbid")
    aggregate_diff: _AggregateDiff
    optimization_suggestion: str = ""
    new_ocr_suggestions: dict | None = None
    new_description: str | None = None
    new_ocr_prompt: str | None = None
    skill_feedback: str = ""
    # ADR-002 (typed-edit mode): bounded edits on the field's rule section.
    # Optional — absent in wholesale-rewrite mode, defaults to None.
    edits: list[_TypedEdit] | None = None


# Appended to SYSTEM_INSTRUCTION only when SKILL_TYPED_EDITS is on. Asks for
# bounded typed edits on the field's rule section instead of a full rewrite.
TYPED_EDIT_INSTRUCTION = (
    "\n\nTYPED-EDIT MODE (return an `edits` array; you may set new_ocr_prompt=null):\n"
    "Instead of rewriting the whole prompt, return `edits`: a SHORT list (≤4) of "
    "bounded edits to THIS field's rule section. Each edit = {op, target, content, "
    "source_type, kind}:\n"
    "- op: 'append' (add one rule line — PREFER THIS), 'replace' (swap the whole "
    "rule section — use sparingly), or 'delete' (remove it — rare).\n"
    "- target: this field's key (module_key).\n"
    "- content: ONE concise, cross-sample rule (pattern/alias/constraint); empty for delete.\n"
    "- source_type: 'failure' (fixing a miss) or 'success' (reinforcing).\n"
    "- kind: 'SKILL_DEFECT' if the rule itself is wrong/missing, or "
    "'EXECUTION_LAPSE' if the rule is fine but the model slipped once "
    "(when unsure, use 'EXECUTION_LAPSE'). \n"
    "Keep edits minimal and high-support (prefer rules supported by multiple samples)."
)


SYSTEM_INSTRUCTION = (
    "You are an OCR prompt optimization expert. You receive one module's "
    "OCR output, the ground truth, the iteration history, and (read-only) the "
    "list of skills currently attached to this module. You must return ONLY a "
    "single JSON object with EXACTLY these keys and nothing more: "
    "aggregate_diff (object with differences_description and "
    "differences_reason_analysis), optimization_suggestion (string), "
    "new_ocr_suggestions (object), new_description (string), new_ocr_prompt "
    "(string), skill_feedback (string). "
    "IMPORTANT: skills are read-only — do NOT include any 'skills', "
    "'skill_ids', 'new_skills', or similar fields in your output. If you think "
    "a skill is missing or unsuitable, describe it in the skill_feedback "
    "string only. Output no markdown, no commentary, only JSON.\n"
    "\n"
    "When writing new_ocr_prompt, follow these prompt-engineering rules:\n"
    "1. CROSS-SAMPLE INVARIANTS: derive rules that hold on EVERY sample "
    "(fixed prefix/suffix, character classes, length range, separator habits) "
    "— express them as patterns (e.g. `INV-\\d{6}`), never as one sample's "
    "literal value, and never as absolute coordinates (use neighbouring-label "
    "anchors instead).\n"
    "2. LABEL ALIASES: enumerate the field's on-document label variants — "
    "labels actually seen in the samples PLUS common international "
    "conventions (e.g. Invoice No. / Inv # / Tax Invoice No. / Bill No.).\n"
    "3. OUTPUT CONSTRAINTS: state the value's type and shape explicitly "
    "(regex/pattern for stable shapes, the full closed set for enum-like "
    "fields such as currency/tax-category/unit with 'must be one of ...').\n"
    "4. CONSOLIDATE, don't append: produce ONE coherent instruction set — "
    "merge overlapping hints, drop rules contradicted by the latest ground "
    "truth, keep the prompt compact."
)


def optimize_module(
    *,
    module: Any,
    iteration: Any,
    history: list[dict],
    processor_spec: str,
    model_name: str | None,
) -> dict | None:
    """
    Call the LLM to produce optimizations for one module's iteration.

    Returns a dict with keys:
        aggregate_diff, optimization_suggestion,
        new_ocr_suggestions, new_description, new_ocr_prompt
    Returns None if the LLM call fails (caller should leave the module unchanged).
    """
    user_prompt = _build_user_prompt(module, iteration, history)
    # ADR-002: in typed-edit mode, ask for bounded edits (flag-gated; OFF →
    # identical system prompt as before).
    from app.core.config import get_settings as _gs
    system = SYSTEM_INSTRUCTION
    if getattr(_gs(), "SKILL_TYPED_EDITS", False):
        system = SYSTEM_INSTRUCTION + TYPED_EDIT_INSTRUCTION
    try:
        result = llm_text_completion(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=system,
            user_prompt=user_prompt,
            as_json=True,
        )
    except Exception as exc:
        logger.warning("Module optimizer LLM failed for %s: %s", module.module_key, exc)
        return None

    if not isinstance(result, dict):
        logger.warning("Module optimizer returned non-dict: %r", result)
        return None

    # Strict Pydantic validation — extra='forbid' will reject any attempt to
    # inject skill_ids / new_skill_ids / new_skills fields. If LLM hallucinated
    # those, we'd rather fall back to "no change" than silently apply changes
    # that bypass the skill ownership rule.
    try:
        parsed = ModuleOptimizerOutput.model_validate(result)
    except ValidationError as exc:
        # Common case: LLM returned extra forbidden fields. Try a second pass
        # by stripping skill-related keys explicitly before failing.
        forbidden = {"skill_ids", "new_skill_ids", "skills", "new_skills"}
        if any(k in result for k in forbidden):
            logger.warning(
                "Module optimizer attempted to write forbidden skill fields "
                "(module=%s, keys=%s) — stripping and retrying parse",
                module.module_key,
                [k for k in forbidden if k in result],
            )
            cleaned = {k: v for k, v in result.items() if k not in forbidden}
            try:
                parsed = ModuleOptimizerOutput.model_validate(cleaned)
            except ValidationError as exc2:
                logger.warning(
                    "Module optimizer output invalid even after stripping: %s",
                    exc2,
                )
                return None
        else:
            logger.warning("Module optimizer output invalid: %s", exc)
            return None

    return {
        "aggregate_diff": parsed.aggregate_diff.model_dump(),
        "optimization_suggestion": parsed.optimization_suggestion,
        "new_ocr_suggestions": parsed.new_ocr_suggestions,
        "new_description": parsed.new_description,
        "new_ocr_prompt": parsed.new_ocr_prompt,
        "skill_feedback": parsed.skill_feedback,
        # ADR-002: typed edits (None in wholesale-rewrite mode)
        "edits": [e.model_dump() for e in parsed.edits] if parsed.edits else None,
    }


# ── Local self-verify (design v4 "局部验证") ─────────────────────────────────
#
# After optimize_module proposes a new prompt for a failing field, ask a
# second LLM call to act as a judge: given the original failure samples and
# the proposed fix, is the fix likely to solve the issue?
#
# Returns:
#   {"verdict": "accept" | "reject", "reasoning": str}
#
# Caller decides whether to apply the mutation. On LLM error we default to
# "accept" (fail-open) so a flaky LLM doesn't block progress.

VERIFIER_SYSTEM = (
    "你是一个 OCR prompt 修改的审查员。给定一个字段当前 prompt 提取失败的样本，"
    "以及一个 LLM 提议的新 prompt，请判断这个新 prompt 是否真的能解决这些样本上的"
    "失败。仅当你确信新 prompt 显著改进了取值准确度时，返回 verdict='accept'；"
    "否则返回 'reject'。务必返回纯 JSON，键为 verdict 和 reasoning。"
)


def verify_module_fix(
    *,
    module: Any,
    iteration: Any,
    proposed: dict,
    processor_spec: str,
    model_name: str | None,
) -> dict:
    """Self-verify a proposed module mutation against the failing samples.

    `proposed` is the dict returned by optimize_module (must include
    new_ocr_prompt or new_description).
    """
    new_prompt = proposed.get("new_ocr_prompt") or module.ocr_prompt or ""
    failing = [
        p for p in (iteration.per_sample_results or [])
        if not p.get("matched")
    ]
    if not failing:
        # No failures to verify against — accept by default
        return {"verdict": "accept", "reasoning": "no failing samples to test"}

    failing_block = json.dumps(failing[:3], ensure_ascii=False, indent=2)
    user_prompt = (
        f"# Field\n"
        f"- module_key: {module.module_key}\n"
        f"- display_name: {module.display_name}\n\n"
        f"# Current ocr_prompt (failed)\n```\n{module.ocr_prompt or ''}\n```\n\n"
        f"# Proposed new ocr_prompt\n```\n{new_prompt}\n```\n\n"
        f"# Failing samples (OCR slice vs ground truth)\n"
        f"```json\n{failing_block}\n```\n\n"
        "请判断新 prompt 是否会让这些样本提取正确。"
    )
    try:
        result = llm_text_completion(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=VERIFIER_SYSTEM,
            user_prompt=user_prompt,
            as_json=True,
        )
    except Exception as exc:
        logger.warning(
            "verify_module_fix LLM failed for %s: %s — accepting by default",
            module.module_key, exc,
        )
        return {"verdict": "accept", "reasoning": f"verifier error: {exc}"}

    if not isinstance(result, dict):
        return {"verdict": "accept", "reasoning": "non-dict response, defaulting accept"}
    verdict = str(result.get("verdict", "accept")).lower()
    if verdict not in ("accept", "reject"):
        verdict = "accept"
    return {
        "verdict": verdict,
        "reasoning": str(result.get("reasoning", ""))[:500],
    }


def _build_user_prompt(module: Any, iteration: Any, history: list[dict]) -> str:
    per_sample_table = _format_per_sample(iteration.per_sample_results)
    history_block = _format_history(history)
    skills_block = _format_skills(getattr(module, "skill_ids", None))

    return f"""# Module to optimize
- module_key: {module.module_key}
- display_name: {module.display_name}
- json_path: {module.json_path}
- schema_fragment:
{json.dumps(module.schema_fragment, ensure_ascii=False, indent=2)}

# Current description
{module.description}

# Current OCR suggestions
{json.dumps(module.ocr_suggestions, ensure_ascii=False, indent=2)}

# Current OCR prompt
{module.ocr_prompt}

# Attached skills (READ-ONLY — you cannot modify these)
{skills_block}

# This round's results vs ground truth (aggregate accuracy: {iteration.aggregate_accuracy})
{per_sample_table}

# Recent iteration history (oldest → newest)
{history_block}

# Task
1. Diagnose why OCR diverged from GT (write to aggregate_diff).
2. Produce a single optimization_suggestion.
3. Update ocr_suggestions (preserve the keys semantics/position/most_common_feature/extra_features).
4. Update description.
5. Lastly, write new_ocr_prompt — it must be derivable from the updated
   description + suggestions, and must instruct the OCR model to output
   data conforming to schema_fragment.
6. (Optional) fill skill_feedback as a single string describing whether
   the attached skills are sufficient or what kind of skill might help.
   DO NOT include any 'skills' / 'skill_ids' / 'new_skills' field — they
   will be stripped and your output may be discarded entirely.

Return only the JSON object as described in the system instruction.
"""


def _format_skills(skill_ids: list | None) -> str:
    """MVP placeholder — skills system is TODO (§17). Just show count."""
    if not skill_ids:
        return "(no skills attached)"
    return f"({len(skill_ids)} skill(s) attached — content not loaded in MVP)"


def _format_per_sample(per_sample: list) -> str:
    if not per_sample:
        return "(no samples)"
    lines = []
    for i, s in enumerate(per_sample, start=1):
        lines.append(
            f"## Sample {i} (doc {s.get('sample_doc_id', '?')}) "
            f"accuracy={s.get('field_accuracy', 0)}"
        )
        lines.append(f"OCR: {json.dumps(s.get('ocr_sliced'), ensure_ascii=False)}")
        lines.append(f"GT : {json.dumps(s.get('ground_truth'), ensure_ascii=False)}")
        if s.get("diff_detail"):
            lines.append(f"diff: {s['diff_detail']}")
        lines.append("")
    return "\n".join(lines)


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no prior history — this is round 1)"
    lines = []
    for h in history:
        lines.append(
            f"- round {h.get('round_num')}: accuracy={h.get('aggregate_accuracy')}, "
            f"suggestion={h.get('optimization_suggestion') or '(none)'}"
        )
    return "\n".join(lines)
