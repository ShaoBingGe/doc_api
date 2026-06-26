"""Meta-skill memory —— 优化器侧元记忆（ADR-001 P3）。

SkillOpt 的 meta_skill：不改技能正文，而是给优化器自身积累「怎么提/合/排 edit」的
元记忆，跨轮复用。本项目对应物：从历史「被接受 / 被拒」的 edit（gate 决策 + rejected
buffer）确定性聚合出各 op 的被拒率，渲染成一句给优化器的提案偏好提示（如「整段 replace
历史被拒率高 → 优先小步 append」）。

纯函数、确定性、无 LLM、无 DB。产物可序列化进 `OcrOptimizationRun.metrics`，既供优化器
（flag-gated 注入）也供 P5 可视化。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .types import FieldEdit

# op 总样本数达此值才纳入提示（避免一两个样本下结论）
MIN_OP_SAMPLES = 3
# 被拒率达此值才触发偏好提示
HIGH_REJECT_RATE = 0.5


def summarize_edit_outcomes(
    accepted: Iterable[FieldEdit],
    rejected: Iterable[FieldEdit],
) -> dict:
    """按 op 聚合接受/拒绝计数 + 被拒率（确定性、可序列化）。"""
    by_op: dict[str, dict] = defaultdict(lambda: {"accepted": 0, "rejected": 0})
    for e in accepted:
        by_op[e.op]["accepted"] += 1
    for e in rejected:
        by_op[e.op]["rejected"] += 1

    out_ops: dict[str, dict] = {}
    total_a = total_r = 0
    for op, c in by_op.items():
        a, r = c["accepted"], c["rejected"]
        total_a += a
        total_r += r
        tot = a + r
        out_ops[op] = {
            "accepted": a,
            "rejected": r,
            "reject_rate": round(r / tot, 3) if tot else 0.0,
        }
    return {
        "by_op": out_ops,
        "total_accepted": total_a,
        "total_rejected": total_r,
    }


def render_meta_hint(summary: dict) -> str:
    """从汇总渲染一句优化器提案偏好提示；无显著信号时返回空串。"""
    by_op = (summary or {}).get("by_op", {})
    worst_op = None
    worst_rate = 0.0
    for op, c in by_op.items():
        tot = c.get("accepted", 0) + c.get("rejected", 0)
        rate = c.get("reject_rate", 0.0)
        if tot >= MIN_OP_SAMPLES and rate >= HIGH_REJECT_RATE and rate > worst_rate:
            worst_op, worst_rate = op, rate
    if not worst_op:
        return ""
    pct = round(worst_rate * 100)
    alt = "append / insert（小步增补）" if worst_op == "replace" else "append（小步增补）"
    return (
        f"（元记忆）历史上 `{worst_op}` 类编辑被留出门拒绝率达 {pct}%："
        f"本轮优先用 {alt}，避免大步改写。"
    )
