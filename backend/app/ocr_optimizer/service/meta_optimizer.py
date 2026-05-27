"""
Meta optimizer LLM call.

Runs ONCE per round after all per-module optimizers complete. Looks at the
big picture and decides structural changes:
  - add_modules        — new module specs (e.g. for unclaimed GT fields)
  - remove_module_keys — modules to delete (e.g. empty outputs)
  - rename             — module renames

Falls back to "no change" on LLM error.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import slicer
from .llm_call import llm_text_completion

logger = logging.getLogger(__name__)


SYSTEM_INSTRUCTION = (
    "You are the meta-optimizer for a modular OCR prompt system. "
    "You see all modules' current state and decide structural changes "
    "(add / remove / rename / reorder). Return ONLY a single JSON object "
    "with keys: add_modules (list of module specs), remove_module_keys "
    "(list of strings), rename (list of {old, new} objects), rationale "
    "(string). Empty lists mean no change."
)


def run_meta_optimization(
    *,
    api_def: Any,
    modules: list,
    iterations: list,
    ocr_outputs: dict,
    ground_truths: dict,
    processor_spec: str,
    model_name: str | None,
) -> dict:
    """
    Decide structural changes to module set.

    Returns:
        dict with keys add_modules, remove_module_keys, rename, rationale.
        On error returns empty lists with rationale="meta optimizer skipped".
    """
    # Compute "unclaimed" GT paths (in GT but not covered by any module.json_path)
    module_paths = [m.json_path for m in modules]
    unclaimed_paths: set[str] = set()
    for gt in ground_truths.values():
        for leaf in slicer.collect_leaf_paths(gt or {}):
            if not any(slicer.path_covers(mp, leaf) for mp in module_paths):
                unclaimed_paths.add(leaf)

    # Compute "empty" modules — modules whose OCR slice is empty on every sample
    empty_modules: list[str] = []
    for m, it in zip(modules, iterations):
        if all(_is_empty(s.get("ocr_sliced")) for s in (it.per_sample_results or [])):
            empty_modules.append(m.module_key)

    # Build the LLM input
    module_summaries = [
        {
            "module_key": m.module_key,
            "display_name": m.display_name,
            "json_path": m.json_path,
            "accuracy": it.aggregate_accuracy,
            "recent_suggestion": (it.optimization_suggestion or "")[:300],
        }
        for m, it in zip(modules, iterations)
    ]

    user_prompt = (
        f"# Document type\n{api_def.description or api_def.name}\n\n"
        f"# Current modules\n{json.dumps(module_summaries, ensure_ascii=False, indent=2)}\n\n"
        f"# Unclaimed GT field paths (in GT, no module covers them)\n"
        f"{json.dumps(sorted(unclaimed_paths), ensure_ascii=False)}\n\n"
        f"# Modules with empty output on every sample\n"
        f"{json.dumps(empty_modules, ensure_ascii=False)}\n\n"
        "Decide whether to add / remove / rename modules. New module specs "
        "must include: module_key, display_name, json_path, schema_fragment, "
        "description, ocr_suggestions, ocr_prompt, order_index. Return ONLY "
        "the JSON object."
    )

    # Skip LLM entirely if nothing structural to consider
    if not unclaimed_paths and not empty_modules:
        return _no_change("no unclaimed paths and no empty modules")

    try:
        result = llm_text_completion(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=SYSTEM_INSTRUCTION,
            user_prompt=user_prompt,
            as_json=True,
        )
    except Exception as exc:
        logger.warning("Meta optimizer LLM failed: %s", exc)
        return _no_change(f"meta optimizer failed: {exc}")

    if not isinstance(result, dict):
        return _no_change("meta optimizer returned non-dict")

    return {
        "add_modules": _safe_list(result.get("add_modules")),
        "remove_module_keys": _safe_list(result.get("remove_module_keys")),
        "rename": _safe_list(result.get("rename")),
        "rationale": result.get("rationale") or "",
        "unclaimed_paths": sorted(unclaimed_paths),
        "empty_modules": empty_modules,
    }


def _no_change(rationale: str) -> dict:
    return {
        "add_modules": [],
        "remove_module_keys": [],
        "rename": [],
        "rationale": rationale,
        "unclaimed_paths": [],
        "empty_modules": [],
    }


def _safe_list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, (str, list, dict)) and not v:
        return True
    return False
