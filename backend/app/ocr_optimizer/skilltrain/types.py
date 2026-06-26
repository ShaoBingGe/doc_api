"""Typed data model for the ReflACT skill-optimization loop (ADR-001).

Mirrors SkillOpt's atomic types, adapted to OCR field extraction:
  - RolloutScore   = one sample's per-field hard/soft result (= a "rollout")
  - FieldEdit      = a bounded typed edit on a field's skill/prompt
  - GateResult     = the held-out validation acceptance decision

All pure dataclasses; no DB, no LLM, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from statistics import mean
from typing import Iterable, Literal

EditOp = Literal["append", "replace", "delete"]
SourceType = Literal["failure", "success"]

# Kind discrimination (EmbodiSkill): a wrong/missing rule vs a one-off slip.
SKILL_DEFECT = "SKILL_DEFECT"        # rule is wrong/missing → edit the body
EXECUTION_LAPSE = "EXECUTION_LAPSE"  # rule is fine, executor slipped → appendix only


# ── Rollout scoring ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FieldResult:
    """One field's outcome on one sample.

    `error` is a normalized error signature (used for support-counting and
    dedup); empty string when the field is correct.
    """
    field: str
    hard: bool
    soft: float
    error: str = ""


@dataclass
class RolloutScore:
    """One sample's scored extraction across fields — the OCR analogue of a
    SkillOpt rollout. `hard`/`soft` aggregate across this sample's fields."""
    sample_id: str
    fields: dict[str, FieldResult] = field(default_factory=dict)

    @property
    def hard(self) -> float:
        if not self.fields:
            return 0.0
        return mean(1.0 if r.hard else 0.0 for r in self.fields.values())

    @property
    def soft(self) -> float:
        if not self.fields:
            return 0.0
        return mean(r.soft for r in self.fields.values())


def aggregate(rollouts: Iterable[RolloutScore], metric: str = "soft") -> float:
    """Mean of a metric ('hard'|'soft') over a set of rollouts (a split score)."""
    vals = [getattr(r, metric) for r in rollouts]
    return mean(vals) if vals else 0.0


# ── Typed bounded edits ───────────────────────────────────────────────────────

@dataclass
class FieldEdit:
    """A single bounded edit to a field's skill/prompt body.

    `support_count` = how many samples support this edit (drives clip ranking +
    defect/lapse). `kind` = SKILL_DEFECT / EXECUTION_LAPSE.
    """
    op: EditOp
    target: str
    content: str = ""
    support_count: int = 1
    source_type: SourceType = "failure"
    kind: str = ""

    def signature(self) -> str:
        """Normalized identity for dedup + rejected-buffer (op|target|content,
        whitespace-collapsed, case-folded)."""
        norm = " ".join((self.content or "").split()).lower()
        return f"{self.op}|{self.target}|{norm}"

    def copy(self) -> "FieldEdit":
        return replace(self)


# ── Validation gate ───────────────────────────────────────────────────────────

class GateAction(str, Enum):
    accept = "accept"
    reject = "reject"


@dataclass
class GateResult:
    action: GateAction
    current: float
    candidate: float
    metric: str = "soft"
    best: float | None = None

    @property
    def accepted(self) -> bool:
        return self.action == GateAction.accept
