"""
Top-level reflection entrypoint.

`reflect_on_diffs(diffs, modules_by_key, ...)` walks each diff, asks the
master router which skill(s) apply, and runs each skill's LLM call. The
parsed JSON outputs are aggregated per-module so the downstream optimizer
can read them as structured "reasoning" inputs.

Failure of an individual skill never aborts the batch — it logs and falls
through (the optimizer can still proceed using just the user diff itself).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..service.llm_failover import llm_text_completion_failover
from .country_agents_loader import CountryAgent, load_country_agents
from .master import route
from .skills_loader import Skill

logger = logging.getLogger(__name__)


@dataclass
class ReflectionResult:
    """Aggregated reflection output for one diff."""
    module_key: str
    kind: str  # "edit" | "add"
    diff: dict
    skill_outputs: list[dict] = field(default_factory=list)
    # Convenience aggregates derived from skill_outputs:
    fix_suggestions: list[str] = field(default_factory=list)
    description_patch: str | None = None
    rationale_summary: str = ""


_REFLECTION_SYSTEM = (
    "你是一个专业的 OCR prompt 反思 agent。你的输出必须是严格的 JSON 对象，"
    "不要任何 markdown 围栏或多余文字。"
)


def reflect_on_diffs(
    diffs: list[dict],
    *,
    modules_by_key: dict[str, dict] | None = None,
    processor_spec: str = "gemini",
    model_name: str | None = None,
    country: str | None = None,
) -> dict[str, ReflectionResult]:
    """
    Run reflection for each diff.

    Args:
        diffs: list of diff dicts (see skills_loader.Skill.matches docstring)
        modules_by_key: optional context for each existing module:
            {module_key: {"description": str, "ocr_prompt": str, "schema_fragment": dict}}
        processor_spec / model_name: passed through to llm_text_completion
        country: ISO country code (e.g. "MY"). When set, the country-specific
                 add_field/edit_field agent runs in ADDITION to global skills.

    Returns:
        {module_key (or temp key for adds): ReflectionResult}
    """
    modules_by_key = modules_by_key or {}
    results: dict[str, ReflectionResult] = {}

    # Country-scoped agents (one per kind). Loaded once per call.
    country_agents = load_country_agents(country) if country else {}

    for idx, diff in enumerate(diffs):
        key = diff.get("module_key") or f"_new_{idx}"
        skills = route(diff)
        result = ReflectionResult(module_key=key, kind=diff.get("kind", "edit"), diff=diff)

        ctx = _build_context(diff, modules_by_key)

        # ── Country agent FIRST (richer, country-specific signal) ───────
        # Its output uses the same schema as global skills (rationale,
        # fix_suggestion, description_patch); we append it to skill_outputs
        # under the agent's key so the downstream aggregation works as-is.
        agent = country_agents.get(diff.get("kind", "edit"))
        if agent:
            output = _invoke_country_agent(
                agent, diff, ctx,
                processor_spec=processor_spec, model_name=model_name,
            )
            if output:
                result.skill_outputs.append({"skill": agent.key, "output": output})
                if isinstance(output, dict):
                    if isinstance(output.get("fix_suggestion"), str) and output["fix_suggestion"].strip():
                        result.fix_suggestions.append(output["fix_suggestion"].strip())
                    if isinstance(output.get("description_patch"), str) and output["description_patch"].strip():
                        if not result.description_patch:
                            result.description_patch = output["description_patch"].strip()

        for skill in skills:
            output = _invoke_skill(
                skill, diff, ctx,
                processor_spec=processor_spec, model_name=model_name,
            )
            if output is None:
                continue
            result.skill_outputs.append({"skill": skill.key, "output": output})
            # Extract convenience fields
            if isinstance(output, dict):
                if isinstance(output.get("fix_suggestion"), str) and output["fix_suggestion"].strip():
                    result.fix_suggestions.append(output["fix_suggestion"].strip())
                if isinstance(output.get("description_patch"), str) and output["description_patch"].strip():
                    # First non-empty wins
                    if not result.description_patch:
                        result.description_patch = output["description_patch"].strip()

        # Summarize rationale
        rats = [
            o["output"].get("rationale") for o in result.skill_outputs
            if isinstance(o.get("output"), dict) and o["output"].get("rationale")
        ]
        result.rationale_summary = " | ".join(rats)

        results[key] = result

    return results


def _build_context(diff: dict, modules_by_key: dict[str, dict]) -> dict[str, Any]:
    """Pull description / ocr_prompt / siblings for the reflected module."""
    mk = diff.get("module_key")
    if mk and mk in modules_by_key:
        info = modules_by_key[mk]
        ctx = {
            "description": info.get("description") or "",
            "ocr_prompt": info.get("ocr_prompt") or "",
        }
    else:
        ctx = {"description": "", "ocr_prompt": ""}

    # For "add" diffs, include a few sibling examples to keep prompt style consistent
    if diff.get("kind") == "add" and modules_by_key:
        siblings: list[str] = []
        for k, info in list(modules_by_key.items())[:3]:
            siblings.append(
                f"- {k} ({info.get('display_name') or ''}): "
                f"{(info.get('description') or '')[:100]}"
            )
        ctx["sibling_examples"] = "\n".join(siblings)
    return ctx


def _invoke_skill(
    skill: Skill,
    diff: dict,
    ctx: dict,
    *,
    processor_spec: str,
    model_name: str | None,
) -> dict | None:
    """Render the skill prompt + call LLM. Returns parsed JSON or None on failure."""
    user_prompt = skill.render(diff, ctx)
    try:
        result = llm_text_completion_failover(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=_REFLECTION_SYSTEM,
            user_prompt=user_prompt,
            as_json=True,
        )
        if isinstance(result, dict):
            return result
        logger.warning("Skill %s returned non-dict: %r", skill.key, type(result))
        return None
    except Exception as exc:
        logger.exception("Skill %s LLM call failed: %s", skill.key, exc)
        return None


def _invoke_country_agent(
    agent: CountryAgent,
    diff: dict,
    ctx: dict,
    *,
    processor_spec: str,
    model_name: str | None,
) -> dict | None:
    """Same as _invoke_skill but uses the agent's OWN system_prompt instead
    of the global _REFLECTION_SYSTEM. Returns parsed JSON or None on error."""
    user_prompt = agent.render(diff, ctx)
    try:
        result = llm_text_completion_failover(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=agent.system_prompt or _REFLECTION_SYSTEM,
            user_prompt=user_prompt,
            as_json=True,
        )
        if isinstance(result, dict):
            return result
        logger.warning("Country agent %s returned non-dict: %r", agent.key, type(result))
        return None
    except Exception as exc:
        logger.exception("Country agent %s LLM call failed: %s", agent.key, exc)
        return None
