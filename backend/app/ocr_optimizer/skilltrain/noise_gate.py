"""Pre-iteration noise-sample gate (ADR-001 / plan §1).

Before starting iteration, require the customer to have enough confirmed
samples: 3 anchors + N noise (default N=9 → 12 total) so the held-out gate has
real held-out variety. Pure threshold helpers.
"""
from __future__ import annotations

ANCHORS_DEFAULT = 3
NOISE_DEFAULT = 9


def required_total(*, anchors: int = ANCHORS_DEFAULT, noise: int = NOISE_DEFAULT) -> int:
    return anchors + noise


def is_ready(confirmed_count: int, *, anchors: int = ANCHORS_DEFAULT, noise: int = NOISE_DEFAULT) -> bool:
    return confirmed_count >= required_total(anchors=anchors, noise=noise)


def shortfall(confirmed_count: int, *, anchors: int = ANCHORS_DEFAULT, noise: int = NOISE_DEFAULT) -> int:
    """How many more confirmed samples are needed to start iteration (0 if ready)."""
    return max(0, required_total(anchors=anchors, noise=noise) - confirmed_count)
