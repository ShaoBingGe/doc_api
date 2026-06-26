"""Minibatch edit aggregation (ADR-001 P1).

After reflecting a minibatch of samples, identical edits (same op|target|content)
are merged and their support counts summed — so a fix supported by 3 samples
outranks a one-off. Pure string/dict work.
"""
from __future__ import annotations

from typing import Iterable

from .types import FieldEdit


def aggregate_edits(edits: Iterable[FieldEdit]) -> list[FieldEdit]:
    """Merge edits sharing a signature; support_count = sum of the group's
    counts. Preserves first-seen order; keeps the strongest `kind` (a defect
    beats a lapse when both appear for the same signature)."""
    merged: dict[str, FieldEdit] = {}
    for e in edits:
        sig = e.signature()
        if sig in merged:
            m = merged[sig]
            m.support_count += e.support_count
            # SKILL_DEFECT dominates EXECUTION_LAPSE on conflict
            if e.kind == "SKILL_DEFECT":
                m.kind = "SKILL_DEFECT"
        else:
            merged[sig] = e.copy()
    return list(merged.values())
