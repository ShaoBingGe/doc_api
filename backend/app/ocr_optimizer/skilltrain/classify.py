"""SKILL_DEFECT vs EXECUTION_LAPSE classification (ADR-001 P1 / EmbodiSkill).

A field error is a SKILL_DEFECT (rule wrong/missing → edit the body) only when
it is SYSTEMATIC across the minibatch; a one-off / sub-threshold error is an
EXECUTION_LAPSE (rule fine, executor slipped → appendix reminder only, body
untouched). When the signal is unclear, default to EXECUTION_LAPSE — never
delete a valid rule over a one-off slip. Pure.
"""
from __future__ import annotations

from .types import EXECUTION_LAPSE, SKILL_DEFECT


def classify(
    error_count: int,
    n_samples: int,
    *,
    defect_frac: float = 0.34,
    min_defect_count: int = 2,
) -> str:
    """Return SKILL_DEFECT iff the error is systematic — it recurs on at least
    `min_defect_count` samples AND on >= `defect_frac` of the minibatch.
    Everything else (one-off, sub-threshold, degenerate input) → EXECUTION_LAPSE
    (protect the body)."""
    if n_samples <= 0 or error_count <= 0:
        return EXECUTION_LAPSE
    frac = error_count / n_samples
    if error_count >= min_defect_count and frac >= defect_frac:
        return SKILL_DEFECT
    return EXECUTION_LAPSE
