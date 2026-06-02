"""
Golden-driven diff → reflect loop (req 3).

Closes the loop the golden gate opens: a STRICT golden check produces per-field
deviations; this module turns each deviation into a `diff` the existing
reflection machinery already understands (same shape customers' edits produce),
then runs `reflect_on_diffs` on them. The downstream optimize/compose steps are
the SAME ones the customer iteration uses (module_optimizer + composer), so a
golden failure drives prompt improvement exactly like a customer correction —
only the trigger (golden seed) and the comparator (exact match) differ.

Pieces:
  - load_golden(country)            read manifest + GT files for a country
  - golden_strict_check(...)        OCR golden source docs, strict-score, collect deviations
  - deviations_to_diffs(...)        deviations → reflection-ready diff dicts
  - reflect_on_golden(...)          run reflection on those diffs

The actual end-to-end run needs real OCR (Gemini); the pure transforms
(load_golden / deviations_to_diffs) and reflection wiring are unit-tested.
"""

from __future__ import annotations

import json
import os
import random
import uuid
from collections import Counter
from typing import Any

from .harness import (
    ModuleSpec,
    collect_deviations,
    evaluate_prompt,
)

_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def _golden_dir(country: str) -> str:
    return os.path.join(_EVAL_DIR, "golden_set", country.upper())


def load_golden(country: str) -> dict[str, dict]:
    """Return {source_doc_id: {gt, doc, api_code, fields}} from the committed
    golden manifest + GT files. GT is already list-wrapped + null-free."""
    base = _golden_dir(country)
    manifest_path = os.path.join(base, "manifest.json")
    if not os.path.isfile(manifest_path):
        return {}
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    out: dict[str, dict] = {}
    for item in manifest.get("items", []):
        gt_path = os.path.join(base, item["ground_truth"])
        if not os.path.isfile(gt_path):
            continue
        gt = json.load(open(gt_path, encoding="utf-8"))
        out[item["source_doc_id"]] = {
            "gt": gt,
            "doc": item.get("doc"),
            "api_code": item.get("source_api_code"),
            "fields": item.get("gt_fields"),
        }
    return out


# ── Comparable batches: same field set, random different seeds ────────────────
#
# Strict accuracy is only comparable across batches if every batch tests the
# SAME fields. Real invoices never share an exact field set, so we fix a CORE
# field set (top-level keys present in >= threshold of seeds) and draw each
# batch as a RANDOM sample of seeds that COVER that core, scoring only the core.
# → "随机 5 个同样结果的不同种子集合": identical result-set, rotating documents.

def _record(gt: Any) -> dict:
    if isinstance(gt, list) and gt and isinstance(gt[0], dict):
        return gt[0]
    return gt if isinstance(gt, dict) else {}


def _nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def _top_keys(gt: Any) -> set[str]:
    """Top-level field keys with a non-empty value in this seed's GT."""
    return {k for k, v in _record(gt).items() if _nonempty(v)}


def compute_core_fields(country: str, threshold: float = 0.8) -> list[str]:
    """Top-level fields present (non-empty) in >= threshold of the country's
    golden seeds. This is the stable "结果数据集" every batch is scored on."""
    g = load_golden(country)
    sets = [_top_keys(e["gt"]) for e in g.values()]
    n = len(sets) or 1
    cnt: Counter = Counter()
    for s in sets:
        cnt.update(s)
    return sorted(k for k, c in cnt.items() if c >= threshold * n)


def sample_batch(
    country: str,
    *,
    size: int = 5,
    threshold: float = 0.8,
    rng_seed: int | None = None,
) -> dict:
    """Draw a comparable batch: up to `size` (<=5) RANDOM seeds that all cover
    the core field set. Different seeds each call (pass rng_seed to reproduce).

    Returns {doc_ids, core_fields, pool_size, batch_size}.
    """
    size = min(size, 5)   # hard cap per the batch policy
    g = load_golden(country)
    core = set(compute_core_fields(country, threshold))
    pool = [did for did, e in g.items() if core <= _top_keys(e["gt"])]
    rng = random.Random(rng_seed)
    batch = rng.sample(pool, min(size, len(pool))) if pool else []
    return {
        "doc_ids": batch,
        "core_fields": sorted(core),
        "pool_size": len(pool),
        "batch_size": len(batch),
    }


def _leaf(json_path: str | None) -> str:
    if not json_path:
        return ""
    leaf = json_path.split(".")[-1]
    return leaf.replace("[*]", "").replace("[", "").replace("]", "").strip()


def _scalar(sliced: Any) -> Any:
    """Unwrap a single-element slice (['INV-1'] → 'INV-1') for the diff value;
    leave multi-element / nested values as-is."""
    if isinstance(sliced, list) and len(sliced) == 1:
        return sliced[0]
    return sliced


def deviations_to_diffs(
    deviations: list[dict],
    modules_by_key: dict[str, dict],
) -> list[dict]:
    """Convert strict golden deviations into reflection-ready diff dicts.

    A golden miss is an `edit`: the model's wrong output is `original_value`,
    the golden answer is `corrected_value` — which routes through the existing
    value_mismatch / empty_value skills just like a customer correction.
    """
    diffs: list[dict] = []
    for dev in deviations:
        mk = dev.get("module_key", "")
        info = modules_by_key.get(mk, {}) or {}
        fname = (info.get("display_name") or _leaf(info.get("json_path")) or mk)
        fmt = info.get("format") or "string"
        diffs.append({
            "kind": "edit",
            "module_key": mk,
            "original_name": fname,
            "corrected_name": fname,
            "original_value": _scalar(dev.get("got")),
            "corrected_value": _scalar(dev.get("expected")),
            "original_format": fmt,
            "corrected_format": fmt,
            "source": "golden",
            "doc_id": dev.get("doc_id"),
        })
    return diffs


def golden_strict_check(
    db,
    *,
    country: str,
    modules: list[ModuleSpec],
    composed_prompt: str,
    composed_schema: dict | None,
    processor_spec: str = "gemini",
    model_name: str | None = None,
    limit: int = 0,
):
    """OCR the golden source docs with `composed_prompt`, strict-score against
    the golden GT, and return (EvalReport, deviations). Needs real OCR.
    """
    golden = load_golden(country)
    items = list(golden.items())
    if limit > 0:
        items = items[:limit]
    sample_ids = [uuid.UUID(did) for did, _ in items]
    gts = {did: g["gt"] for did, g in items}
    report = evaluate_prompt(
        db,
        modules=modules,
        sample_doc_ids=sample_ids,
        composed_prompt=composed_prompt,
        composed_schema=composed_schema,
        ground_truths=gts,
        processor_spec=processor_spec,
        model_name=model_name,
        strict=True,
    )
    return report, collect_deviations(report)


def golden_strict_batch(
    db,
    *,
    country: str,
    modules: list[ModuleSpec],
    composed_prompt: str,
    composed_schema: dict | None,
    size: int = 5,
    threshold: float = 0.8,
    rng_seed: int | None = None,
    processor_spec: str = "gemini",
    model_name: str | None = None,
):
    """Run a strict check on a COMPARABLE batch (<=5 random seeds covering the
    core), scoring ONLY the core fields so the number is comparable across
    batches. Needs real OCR. Returns (report, deviations, batch_meta).
    """
    batch = sample_batch(country, size=size, threshold=threshold, rng_seed=rng_seed)
    core = set(batch["core_fields"])
    # restrict to modules whose field (json_path leaf) is in the core
    core_modules = [m for m in modules if _leaf(m.json_path) in core]
    golden = load_golden(country)
    sample_ids = [uuid.UUID(d) for d in batch["doc_ids"]]
    gts = {d: golden[d]["gt"] for d in batch["doc_ids"]}
    report = evaluate_prompt(
        db,
        modules=core_modules,
        sample_doc_ids=sample_ids,
        composed_prompt=composed_prompt,
        composed_schema=composed_schema,
        ground_truths=gts,
        processor_spec=processor_spec,
        model_name=model_name,
        strict=True,
    )
    return report, collect_deviations(report), batch


def reflect_on_golden(
    db,
    *,
    country: str,
    deviations: list[dict],
    modules_by_key: dict[str, dict],
    processor_spec: str = "gemini",
    model_name: str | None = None,
):
    """Run the reflection machinery on golden deviations. Returns the same
    {module_key: ReflectionResult} reflect_on_diffs produces (carrying
    fix_suggestions + a structured FieldRule), ready for module_optimizer /
    composer — the existing optimize→compose path."""
    from ..reflection.reflector import reflect_on_diffs

    diffs = deviations_to_diffs(deviations, modules_by_key)
    if not diffs:
        return {}
    return reflect_on_diffs(
        diffs,
        modules_by_key=modules_by_key,
        processor_spec=processor_spec,
        model_name=model_name,
        country=country,
    )
