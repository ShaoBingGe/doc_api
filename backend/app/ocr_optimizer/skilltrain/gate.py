"""Held-out validation gate + rolling leave-one-out split (ADR-001 P1).

The single highest-leverage discipline: a candidate skill is accepted ONLY when
it strictly improves the held-out (val) score — analogous to validation-based
model selection. Default metric is soft (partial credit), per the decision for
few-sample iteration (rolling leave-one-out). Pure: scores are injected.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from .types import GateAction, GateResult, RolloutScore, aggregate


def score(rollouts: Iterable[RolloutScore], metric: str = "soft") -> float:
    """Aggregate val score for a candidate (convenience over types.aggregate)."""
    return aggregate(rollouts, metric)


def decide(
    current: float,
    candidate: float,
    *,
    best: float | None = None,
    metric: str = "soft",
    min_delta: float = 1e-6,
) -> GateResult:
    """Accept iff the candidate STRICTLY beats current by >= min_delta.

    Flat or worse → reject (the anti-overfitting guarantee). `best` is carried
    for logging / model-selection but does not change the accept rule.
    """
    action = GateAction.accept if (candidate - current) >= min_delta else GateAction.reject
    return GateResult(action=action, current=current, candidate=candidate, metric=metric, best=best)


def rolling_leave_one_out(
    sample_ids: Sequence[str],
    *,
    anchors: Sequence[str] = (),
    max_folds: int | None = None,
) -> list[tuple[list[str], list[str]]]:
    """Rolling leave-one-out folds for few-sample gating.

    Each NON-anchor sample serves as the singleton val set exactly once; anchors
    always stay in train (they are the customer's confirmed floor). Returns
    `[(train_ids, val_ids), ...]`. `max_folds` caps the number of folds.
    """
    anchor_set = set(anchors)
    candidates = [s for s in sample_ids if s not in anchor_set]
    if max_folds is not None:
        candidates = candidates[:max_folds]
    folds: list[tuple[list[str], list[str]]] = []
    for held in candidates:
        train = [s for s in sample_ids if s != held]
        folds.append((train, [held]))
    return folds
