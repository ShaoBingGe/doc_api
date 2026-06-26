"""Disciplined optimize-target selection (ADR-001 P1).

Narrows which modules a round optimizes, applying two disciplines from the
ReflACT loop to the production optimize step:

  - DEFECT vs LAPSE: only optimize modules whose error is SYSTEMATIC across the
    (train) samples — a field wrong on just one sample is a one-off execution
    lapse, not a skill defect, so churning its prompt over a single fluke is
    exactly the overfitting we want to avoid.
  - CLIP / learning-rate: among systematic-error modules, take only the top-L by
    severity (1 - accuracy), so a round makes a bounded step instead of rewriting
    every weak field at once.

Pure: operates on the round's per-module per-sample results. No DB / LLM.
"""
from __future__ import annotations

from .classify import classify
from .clip import decide_L
from .types import SKILL_DEFECT


def disciplined_targets(
    iterations,
    target_acc: dict[str, float],
    *,
    train_ids: set[str] | None = None,
    l_max: int = 5,
) -> set[str]:
    """Return the set of module_keys to optimize this round.

    `iterations`: OcrModuleIteration-like (module_key, aggregate_accuracy,
    per_sample_results=[{sample_doc_id, field_accuracy}]). `target_acc`: the
    per-module accuracy that defines "under target" (train accuracy when the
    held-out gate is on). `train_ids`: restrict error counting to these samples
    (None = all).
    """
    candidates: list[tuple[str, float]] = []
    for it in iterations:
        acc = target_acc.get(it.module_key, getattr(it, "aggregate_accuracy", 0.0) or 0.0)
        if acc >= 0.999:
            continue  # already on target
        per = getattr(it, "per_sample_results", None) or []
        rows = [p for p in per if (train_ids is None or p.get("sample_doc_id") in train_ids)]
        n = len(rows)
        err = sum(1 for p in rows if (p.get("field_accuracy") or 0.0) < 0.999)
        if classify(err, n) != SKILL_DEFECT:
            continue  # one-off lapse → don't churn the prompt over a fluke
        candidates.append((it.module_key, 1.0 - acc))

    if not candidates:
        return set()
    candidates.sort(key=lambda x: -x[1])
    L = decide_L(candidates[0][1], l_max=l_max)  # learning-rate by worst severity
    return {key for key, _sev in candidates[:L]}
