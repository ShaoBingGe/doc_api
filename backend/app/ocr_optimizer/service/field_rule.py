"""
FieldRule — the structured, typed representation of "how to extract one field".

Prompt System v2 / Phase 2. This is the single intermediate representation that
reflection → fork/expand → optimizer → reconciler → composer all read and write,
replacing the free-text `fix_suggestion` blobs that each stage had to re-parse
(see docs/prompt-system-v2-plan.md §3.1, requirement 3).

Why a typed object instead of prose:
  - The composer can render it DETERMINISTICALLY into a uniform field skeleton
    (语义 / 取值锚点 / 格式 / 排歧 / 跨样本规则) — requirement 1.
  - Generalization is first-class (`generalization.holds_for_all`) so we can
    prefer rules that cover every sample over position-specific ones —
    requirement 2.
  - `provenance` records which round/diff each rule came from, which is what
    the Phase 4 reconciler needs to resolve cross-round contradictions in favor
    of the latest user intent — requirement 5.

The composer stays deterministic (no LLM): it only RENDERS a FieldRule. All
LLM reasoning that produces/edits a FieldRule lives upstream (reflection,
optimizer, reconciler), per CLAUDE.md §③.4.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Generalization(BaseModel):
    """A rule inferred across multiple samples, plus the evidence it rests on."""
    model_config = ConfigDict(extra="forbid")
    rule: str = ""
    evidence_per_sample: list[str] = Field(default_factory=list)
    holds_for_all: bool = False


class FieldRule(BaseModel):
    """Structured extraction rule for one field/module.

    All fields optional/defaulted so partial rules (e.g. a reflection that only
    refined the format) round-trip cleanly. `extra='forbid'` keeps producers
    honest — mirrors module_optimizer's strict output.
    """
    model_config = ConfigDict(extra="forbid")

    semantic: str = ""                       # business meaning + aliases
    anchors: list[str] = Field(default_factory=list)        # RELATIVE anchors (neighbouring labels/regions), not coordinates
    format_rule: str = ""                    # type / unit / null / normalization
    disambiguation: list[str] = Field(default_factory=list)  # easily-confused fields + how to tell apart
    generalization: Generalization | None = None             # cross-sample inferred rule
    provenance: list[str] = Field(default_factory=list)      # e.g. "round 2: diff edit billFromName"

    # ── Rendering (deterministic — used by composer) ──────────────────────────

    def render_skeleton(self) -> str:
        """Render this rule as the uniform per-field body. Only non-empty
        sections are emitted so a sparse rule stays compact. Returns "" when
        the rule carries nothing renderable (caller then falls back to the raw
        ocr_prompt)."""
        lines: list[str] = []
        if self.semantic.strip():
            lines.append(f"- 语义：{self.semantic.strip()}")
        if self.anchors:
            anchors = "；".join(a.strip() for a in self.anchors if a.strip())
            if anchors:
                lines.append(f"- 取值锚点（相对位置/邻近标签，勿用绝对坐标）：{anchors}")
        if self.format_rule.strip():
            lines.append(f"- 格式：{self.format_rule.strip()}")
        if self.disambiguation:
            dis = "；".join(d.strip() for d in self.disambiguation if d.strip())
            if dis:
                lines.append(f"- 排歧：{dis}")
        g = self.generalization
        if g and g.rule.strip():
            tag = "（已覆盖全部样本）" if g.holds_for_all else "（待更多样本验证）"
            lines.append(f"- 跨样本规则{tag}：{g.rule.strip()}")
        return "\n".join(lines)

    def is_renderable(self) -> bool:
        return bool(self.render_skeleton().strip())

    # ── Persistence helpers (stored inside OcrModule.ocr_suggestions) ─────────

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=False)

    @classmethod
    def from_dict(cls, data: Any) -> "FieldRule | None":
        if not isinstance(data, dict):
            return None
        try:
            return cls.model_validate(data)
        except Exception:
            return None


# Reserved key under OcrModule.ocr_suggestions that holds the serialized
# FieldRule. Reusing the existing JSON column avoids an Alembic migration; the
# legacy suggestion keys (semantics/position/...) remain alongside it.
FIELD_RULE_KEY = "_field_rule"


def field_rule_of(module: Any) -> "FieldRule | None":
    """Return the FieldRule a module carries, or None.

    Looks first at an in-memory `field_rule` attribute (set by the upstream
    pipeline within a single request), then at the serialized copy persisted
    under OcrModule.ocr_suggestions[FIELD_RULE_KEY].
    """
    inline = getattr(module, "field_rule", None)
    if isinstance(inline, FieldRule):
        return inline
    if isinstance(inline, dict):
        fr = FieldRule.from_dict(inline)
        if fr:
            return fr
    sug = getattr(module, "ocr_suggestions", None)
    if isinstance(sug, dict) and isinstance(sug.get(FIELD_RULE_KEY), dict):
        return FieldRule.from_dict(sug[FIELD_RULE_KEY])
    return None


def from_module(module: Any) -> "FieldRule | None":
    """Best-effort adapter: build a FieldRule from a module's EXISTING fields
    (description + the legacy ocr_suggestions keys). Used to bootstrap a
    structured rule from modules authored before Phase 2. Returns None if there
    is nothing structured to lift (caller keeps the raw ocr_prompt).

    Legacy ocr_suggestions shape (see module_optimizer):
        {semantics, position, most_common_feature, extra_features}
    """
    sug = getattr(module, "ocr_suggestions", None)
    desc = (getattr(module, "description", "") or "").strip()
    if not isinstance(sug, dict):
        sug = {}

    def _as_list(v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    semantic = (str(sug.get("semantics") or "").strip() or desc)
    anchors = _as_list(sug.get("position"))
    fmt = str(sug.get("most_common_feature") or "").strip()
    disamb = _as_list(sug.get("extra_features"))

    fr = FieldRule(
        semantic=semantic,
        anchors=anchors,
        format_rule=fmt,
        disambiguation=disamb,
    )
    return fr if fr.is_renderable() else None
