"""Edit clipping + autonomous learning-rate (ADR-001 P1).

Analogous to gradient clipping: rank candidate edits by importance (support
count) and keep only the top-L → bounds the effective step size so a round
can't wildly rewrite the skill. `decide_L` scales L with under-target severity
(bigger step when further from target).
"""
from __future__ import annotations

from typing import Sequence

from .types import FieldEdit


def rank_and_select(edits: Sequence[FieldEdit], L: int) -> list[FieldEdit]:
    """Keep the top-L edits ranked by support_count (desc), tie-broken by target
    for determinism. L <= 0 → empty."""
    if L <= 0:
        return []
    ranked = sorted(edits, key=lambda e: (-e.support_count, e.target, e.signature()))
    return ranked[:L]


def decide_L(severity: float, *, l_min: int = 1, l_max: int = 5) -> int:
    """Autonomous learning-rate: map under-target severity ∈ [0,1] to an edit
    budget. severity = 1 - accuracy (0 = on target, 1 = totally wrong). More
    severe → larger L, clamped to [l_min, l_max]."""
    s = max(0.0, min(1.0, severity))
    return max(l_min, min(l_max, round(s * l_max)))
