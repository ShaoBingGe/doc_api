"""Held-out train/val split for iteration rounds (ADR-001 P1).

Pure helpers used by run_orchestrator (behind the SKILL_HELDOUT_GATE flag) to
turn the existing monotonic version-selection into a held-out gate:

  - val_ids: reserve the TRAILING `frac` of samples as val (anchors — the
    confirmed/first samples — stay in train; the trailing noise samples become
    the held-out validation set, matching the 3-anchor + N-noise design).
  - split_accuracy: from the round's per-module per-sample results, compute the
    overall VAL accuracy (→ rnd.overall_accuracy → version selection) and the
    per-module TRAIN accuracy (→ which modules to optimize). No OCR, no DB.
"""
from __future__ import annotations

from statistics import mean
from typing import Sequence


def val_ids(sample_ids: Sequence, *, frac: float = 0.25, min_val: int = 1) -> list:
    """Trailing `frac` of sample_ids as the held-out val set (anchors lead → in
    train). Always at least `min_val`, never all of them (train keeps ≥1)."""
    n = len(sample_ids)
    if n <= 1:
        return []
    k = max(min_val, int(n * frac))
    k = min(k, n - 1)  # leave at least one train sample
    return list(sample_ids[-k:])


def split_accuracy(iterations, val_id_strs: set[str]) -> tuple[float, dict[str, float]]:
    """Returns (overall_val_accuracy, {module_key: train_accuracy}).

    `iterations` are OcrModuleIteration-like objects exposing `.module_key`,
    `.aggregate_accuracy`, and `.per_sample_results` (list of dicts with
    `sample_doc_id` + `field_accuracy`). A split with no samples on a side falls
    back to the module's all-sample aggregate (defensive — never crashes a round).
    """
    overall_val: list[float] = []
    target_train: dict[str, float] = {}
    for it in iterations:
        per = getattr(it, "per_sample_results", None) or []
        val_accs = [p["field_accuracy"] for p in per if p.get("sample_doc_id") in val_id_strs]
        train_accs = [p["field_accuracy"] for p in per if p.get("sample_doc_id") not in val_id_strs]
        agg = getattr(it, "aggregate_accuracy", 0.0) or 0.0
        v = mean(val_accs) if val_accs else agg
        t = mean(train_accs) if train_accs else agg
        overall_val.append(v)
        target_train[it.module_key] = t
    overall = round(mean(overall_val), 4) if overall_val else 0.0
    return overall, target_train
