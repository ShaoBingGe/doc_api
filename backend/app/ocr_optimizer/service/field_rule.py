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

    semantic: str = ""                       # business meaning
    aliases: list[str] = Field(default_factory=list)         # 标签别名：样本实证 + 行业惯例写法（Invoice No. / Inv # / 发票号码…）
    anchors: list[str] = Field(default_factory=list)        # RELATIVE anchors (neighbouring labels/regions), not coordinates
    format_rule: str = ""                    # type / unit / null / normalization
    value_pattern: str = ""                  # 值的正则/模式描述（如 ^[A-Z]{2,3}-\d{6}$ / YYYY-MM-DD）
    enum_values: list[str] = Field(default_factory=list)     # 值为有限集合时的全部合法取值（币种/税种/单位…）
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
        if self.aliases:
            al = " / ".join(a.strip() for a in self.aliases if a.strip())
            if al:
                lines.append(f"- 标签别名（票面可能写作）：{al}")
        if self.anchors:
            anchors = "；".join(a.strip() for a in self.anchors if a.strip())
            if anchors:
                lines.append(f"- 取值锚点（相对位置/邻近标签，勿用绝对坐标）：{anchors}")
        if self.format_rule.strip():
            lines.append(f"- 格式：{self.format_rule.strip()}")
        if self.value_pattern.strip():
            lines.append(f"- 值模式：`{self.value_pattern.strip()}`")
        if self.enum_values:
            ev = " | ".join(str(e).strip() for e in self.enum_values if str(e).strip())
            if ev:
                # 建议式而非排他硬约束：枚举来自少量样本归纳，把它渲染成
                # 「只能输出其中之一」会在新票面出现集合外合法值（如新币种）
                # 时逼模型选错。以票面实际内容为准。
                lines.append(f"- 常见取值（样本实证；票面出现其他值时以票面为准）：{ev}")
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


def merge_field_rules(old: "FieldRule | None", new: "FieldRule | None") -> "FieldRule | None":
    """合并两条 FieldRule（累积不覆盖，§⑤.3）：列表字段去重累积、标量字段
    最新非空优先、generalization 优先 holds_for_all=True 的那条。"""
    if old is None:
        return new
    if new is None:
        return old

    def _dedup(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            k = (x or "").strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    gen = new.generalization or old.generalization
    if (old.generalization and old.generalization.holds_for_all
            and not (new.generalization and new.generalization.holds_for_all)):
        gen = old.generalization
    return FieldRule(
        semantic=new.semantic or old.semantic,
        aliases=_dedup(old.aliases + new.aliases),
        anchors=_dedup(old.anchors + new.anchors),
        format_rule=new.format_rule or old.format_rule,
        value_pattern=new.value_pattern or old.value_pattern,
        enum_values=_dedup(old.enum_values + new.enum_values),
        disambiguation=_dedup(old.disambiguation + new.disambiguation),
        generalization=gen,
        provenance=_dedup(old.provenance + new.provenance),
    )


# 绝对坐标/固定行列号模式（批次6，§3.5 泛化守护的代码执行层）。
# 历史上「禁用绝对坐标」只是 prompt 层口头约定——一边把 bbox 喂给反思 LLM
# 一边劝它别用坐标，产出「第 3 行」「左上角 (120, 45)」这类换版式即失效的
# 规则无人拦截，且判官在同批同版式样本上验证恰好检不出来。
import re as _re_mod

_COORDINATE_RE = _re_mod.compile(
    r"第\s*\d+\s*[行列页]"          # 第 3 行 / 第2列 / 第1页
    r"|\(\s*\d{2,}\s*,\s*\d{2,}\s*\)"  # (120, 45) 像素坐标对
    r"|[xy]\s*[=＝]\s*\d+"          # x=120 / y = 45
    r"|bbox"                        # bbox 引用
    r"|像素",
    _re_mod.IGNORECASE,
)


def has_absolute_coordinates(text: str | None) -> bool:
    """True 当文本含绝对坐标/固定行列号——换一张版式即失效的规则形态。"""
    return bool(text) and bool(_COORDINATE_RE.search(text))


def sanitize_field_rule(
    fr: "FieldRule | None",
    *,
    observed_values: list[str] | None = None,
    sample_count: int | None = None,
) -> "FieldRule | None":
    """落库前的硬校验（批次5）——LLM 产出的结构化规则不做校验就渲染进
    prompt 会把幻觉变成硬约束：

      - value_pattern 必须能 re.compile；给了 observed_values 时必须匹配
        **全部**观测值（客户确认的正确值），否则整个 pattern 丢弃——
        从单一开票方样本归纳的过窄正则（^INV-\\d{6}$）换一个开票方即失效；
      - enum_values：任一观测值不在枚举内 → 枚举丢弃（它会把合法值判非法）；
      - generalization.holds_for_all：证据条数 < min(样本数, 2) → 降为 False
        （LLM 自报「覆盖全部样本」不可信，凭证据说话）。

    永不抛异常；返回清洗后的 FieldRule（或 None 当无可渲染内容）。
    """
    if fr is None:
        return None
    import re as _re

    observed = [str(v).strip() for v in (observed_values or []) if str(v or "").strip()]

    vp = (fr.value_pattern or "").strip()
    if vp:
        keep = True
        try:
            pat = _re.compile(vp)
        except _re.error:
            keep = False
        else:
            for v in observed:
                # search 而非 fullmatch：pattern 可能带 ^$ 也可能不带，
                # 观测值连 search 都不中就说明 pattern 与真实值形态矛盾。
                if not pat.search(v):
                    keep = False
                    break
        if not keep:
            fr = fr.model_copy(update={"value_pattern": ""})

    if fr.enum_values and observed:
        enum_norm = {str(e).strip().lower() for e in fr.enum_values}
        if any(v.lower() not in enum_norm for v in observed):
            fr = fr.model_copy(update={"enum_values": []})

    g = fr.generalization
    if g is not None and g.holds_for_all:
        need = min(sample_count, 2) if sample_count else 2
        if len(g.evidence_per_sample) < need:
            fr = fr.model_copy(update={
                "generalization": g.model_copy(update={"holds_for_all": False}),
            })

    # 绝对坐标锚点打回（§3.5：相对锚点 only）
    if fr.anchors:
        kept = [a for a in fr.anchors if not has_absolute_coordinates(a)]
        if len(kept) != len(fr.anchors):
            fr = fr.model_copy(update={"anchors": kept})

    return fr if fr.is_renderable() else None


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
