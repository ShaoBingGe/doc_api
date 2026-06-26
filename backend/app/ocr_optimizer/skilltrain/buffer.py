"""Rejected-edit buffer (ADR-001 P1).

Remembers edits the gate rejected so reflect/clip never re-proposes them.
Serializable to/from a plain list for persistence on run state (e.g.
OcrOptimizationRun.metrics) across rounds. Pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .types import FieldEdit


@dataclass
class RejectedEditBuffer:
    _sigs: set[str] = field(default_factory=set)

    def add(self, edit: FieldEdit) -> None:
        self._sigs.add(edit.signature())

    def add_all(self, edits: Iterable[FieldEdit]) -> None:
        for e in edits:
            self.add(e)

    def contains(self, edit: FieldEdit) -> bool:
        return edit.signature() in self._sigs

    def filter(self, edits: Iterable[FieldEdit]) -> list[FieldEdit]:
        """Drop any candidate edit already rejected."""
        return [e for e in edits if not self.contains(e)]

    def __len__(self) -> int:
        return len(self._sigs)

    # ── persistence (run.metrics) ────────────────────────────────────────────
    def to_list(self) -> list[str]:
        return sorted(self._sigs)

    @classmethod
    def from_list(cls, sigs: Iterable[str] | None) -> "RejectedEditBuffer":
        return cls(_sigs=set(sigs or []))
