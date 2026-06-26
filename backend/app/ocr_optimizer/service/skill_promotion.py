"""P4 技能晋升 —— 采收（只读候选检测）。

反思每轮在 `ocr_module_iterations.skill_feedback` 里产出「该字段应该有什么技能」的建议，
但今天这些建议无人采收、每轮蒸发（库 `OcrSkill` 没接采收线）。本模块把同国家各 API 的
这些建议挖成「晋升候选」供管理员审阅。

**只读**：绝不写技能（优化器被硬禁写、晋升须管理员确认，见 ADR-001 P4）。

晋升门槛（2026-06-26 拍板，含越级细则）：**管理员确认是唯一硬门**；**跨租户覆盖 > 5**
作为「自动推荐」信号（`recommended`），管理员对低于阈值的候选也可**显式越级晋升**。
本模块只计算信号 + 标 `recommended`；真正写库是另一步管理员确认动作。
golden_set 不回归作为给管理员的**参考信息**展示，不作硬卡。

粒度：起步按 `(国家, 字段)` 确定性分组（不调 LLM）。后续可在字段内再按技能主题聚类（留待增强）。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional

# "> 5" → 跨租户严格超过 5 才自动够格
QUALIFY_MIN_TENANTS = 5

# 这些非空串不算真实反馈
_EMPTY_SENTINELS = {"", "[]", "{}", "null", "none"}


@dataclass
class PromotionCandidate:
    """一个 (国家, 字段) 维度的晋升候选。"""

    country: str
    field: str                  # 反思为之要技能的 module_key
    occurrence_count: int       # 提到该字段的迭代次数
    tenant_count: int           # 不同租户数（跨租户信号）
    api_count: int              # 不同 API 数
    sample_feedback: list[str]  # 最多 3 条原文摘录（去噪截断）

    @property
    def recommended(self) -> bool:
        """跨租户 > 5 → 系统「自动推荐」晋升（仅优先级信号；管理员可越级晋升低于阈值者）。"""
        return self.tenant_count > QUALIFY_MIN_TENANTS

    def to_dict(self) -> dict:
        return {
            "country": self.country,
            "field": self.field,
            "occurrence_count": self.occurrence_count,
            "tenant_count": self.tenant_count,
            "api_count": self.api_count,
            "recommended": self.recommended,
            "sample_feedback": self.sample_feedback,
        }


def _is_empty(feedback) -> bool:
    if feedback is None:
        return True
    return str(feedback).strip().lower() in _EMPTY_SENTINELS


def _truncate(s: str, n: int = 200) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + "…"


def extract_candidates_from_rows(
    rows: Iterable[tuple],
) -> list[PromotionCandidate]:
    """纯函数：把 (country, tenant_id, api_id, module_key, skill_feedback) 行聚成候选。

    确定性、无 DB、无 LLM —— 故可脱离 app 独立 import/单测。按 (国家, 字段) 分组，
    空反馈跳过，跨租户/跨 API 去重计数，原文最多留 3 条。结果按出现次数降序。
    """
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"occ": 0, "tenants": set(), "apis": set(), "samples": []}
    )
    for country, tenant_id, api_id, module_key, feedback in rows:
        if _is_empty(feedback):
            continue
        key = (country or "?", module_key or "?")
        slot = groups[key]
        slot["occ"] += 1
        slot["tenants"].add(tenant_id)
        slot["apis"].add(api_id)
        if len(slot["samples"]) < 3:
            slot["samples"].append(_truncate(feedback))

    out = [
        PromotionCandidate(
            country=country,
            field=fld,
            occurrence_count=s["occ"],
            tenant_count=len(s["tenants"]),
            api_count=len(s["apis"]),
            sample_feedback=s["samples"],
        )
        for (country, fld), s in groups.items()
    ]
    out.sort(key=lambda c: (-c.occurrence_count, c.country, c.field))
    return out


def find_promotion_candidates(
    db, country: Optional[str] = None
) -> list[PromotionCandidate]:
    """DB 绑定采收（SQLAlchemy）。惰性 import app 模型，保持本模块顶层无 app 依赖
    （以便纯提取器可独立 import）。`country` 给定时只看该国（按 config.source_country）。
    """
    from app.models.api_definition import ApiDefinition

    from ..models import (
        OcrModuleIteration,
        OcrOptimizationRound,
        OcrOptimizationRun,
    )

    rows_raw = (
        db.query(
            ApiDefinition.config,
            ApiDefinition.tenant_id,
            ApiDefinition.id,
            OcrModuleIteration.module_key,
            OcrModuleIteration.skill_feedback,
        )
        .join(
            OcrOptimizationRun,
            OcrOptimizationRun.api_definition_id == ApiDefinition.id,
        )
        .join(
            OcrOptimizationRound,
            OcrOptimizationRound.run_id == OcrOptimizationRun.id,
        )
        .join(
            OcrModuleIteration,
            OcrModuleIteration.round_id == OcrOptimizationRound.id,
        )
        .filter(OcrModuleIteration.skill_feedback.isnot(None))
        .all()
    )

    rows = []
    for cfg, tenant_id, api_id, module_key, feedback in rows_raw:
        # config 是 JSON 列（dict）；防御性兼容字符串
        src = None
        if cfg:
            data = cfg
            if isinstance(cfg, str):
                import json

                try:
                    data = json.loads(cfg)
                except Exception:
                    data = {}
            if isinstance(data, dict):
                src = data.get("source_country")
        if country and src != country:
            continue
        rows.append((src, tenant_id, api_id, module_key, feedback))

    return extract_candidates_from_rows(rows)


# ── 步骤③：LLM 起草晋升技能正文（管理员编辑确认后才写库） ──────────────────


def build_draft_prompt(
    country: str, field: str, sample_feedback: Iterable[str]
) -> tuple[str, str]:
    """确定性 prompt 构造（可单测，不调 LLM）。返回 (system, user)。"""
    asks = "\n".join(f"  - {s}" for s in (sample_feedback or []) if str(s).strip())
    if not asks:
        asks = "  （无具体反馈，按该字段通用经验起草）"
    system = (
        "你是票据识别「技能库」的策展助手。把『某字段在多轮迭代中反复需要的能力』提炼成"
        "一条可复用的识别规则技能，供管理员审阅后纳入全局技能库。只输出 JSON，"
        "键为 name / content / description：\n"
        "- name：简短中文技能名（如『日元金额取整』『发票号去特殊符』）。\n"
        "- content：可直接附加到该字段提示词的规则正文，具体、可执行；与国家相关时注明国别。\n"
        "- description：一句话说明该技能解决什么。\n"
        "忠于下方反思反馈的意图，不要编造字段不需要的能力。"
    )
    user = (
        f"国家：{country}\n字段：{field}\n\n"
        f"反思在多轮迭代中对该字段反复提出的能力诉求：\n{asks}\n\n"
        "请据此起草一条可复用技能（输出 JSON）。"
    )
    return system, user


def draft_skill(
    country: str,
    field: str,
    sample_feedback: Iterable[str],
    *,
    processor_spec: str = "qwen",
    model_name: Optional[str] = None,
) -> dict:
    """用境内合规文本模型（qwen-plus）起草一条技能草稿。**不写库**。

    返回 `{name, content, description}`，对模型输出做防御性兜底。
    """
    from app.core.config import get_settings

    from .llm_call import llm_text_completion

    system, user = build_draft_prompt(country, field, sample_feedback)
    model = model_name or getattr(get_settings(), "QWEN_TEXT_MODEL", None)
    raw = llm_text_completion(
        processor_spec=processor_spec,
        model_name=model,
        system_instruction=system,
        user_prompt=user,
        as_json=True,
    )
    data = raw if isinstance(raw, dict) else {}
    return {
        "name": (str(data.get("name") or f"{field} 识别技能")).strip(),
        "content": (str(data.get("content") or "")).strip(),
        "description": (
            str(data.get("description") or f"P4 晋升自 {country}/{field}")
        ).strip(),
    }
