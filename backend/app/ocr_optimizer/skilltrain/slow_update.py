"""Slow update —— epoch 级守护段（ADR-001 P3）。

SkillOpt 的 slow_update：比较相邻 epoch 同样本表现，把守护性指引写进技能文档的
**受保护段**（step 级编辑不可改）。本项目对应物：迭代收尾时，按每字段跨轮准确率轨迹，
确定性地产出守护指引；该段在 compose 时单独拼入（不存进 `OcrModule.ocr_prompt`，故逐轮
module 优化天然碰不到 → 受保护）。

纯函数、确定性、无 LLM、无 DB —— 与 skilltrain 其余机制同纪律。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

# 末值达此阈值视为「稳定达标」→ pin（钉住，勿大改）
PIN_TARGET = 0.99
# 轨迹极差达此值视为「波动」→ caution
VOLATILE_SWING = 0.34
# 相对峰值回落超此值视为「回退」→ caution
REGRESS_EPS = 0.001

GuardianKind = Literal["pin", "caution"]

GUARDIAN_BLOCK_HEADER = "# 守护指引（slow-update · 请勿改写本段规则）"


@dataclass(frozen=True)
class Guardian:
    field: str
    kind: GuardianKind
    note: str


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def compute_guardians(
    trajectories: Mapping[str, Sequence[float]],
    *,
    target: float = PIN_TARGET,
) -> list[Guardian]:
    """按每字段跨轮准确率轨迹产出守护指引（确定性）。

    - 末值 ≥ target          → pin     ：已稳定达标，保持现规则、勿大改。
    - 否则若回退或波动较大    → caution ：优先回到表现最佳的规则、勿反复改写。
    - 否则（稳步改进）        → 无守护。

    轨迹长度 < 2 的字段不评估（无从判断稳定性）。结果按字段名排序，便于稳定 diff。
    """
    out: list[Guardian] = []
    for fld in sorted(trajectories):
        traj = [float(x) for x in trajectories[fld]]
        if len(traj) < 2:
            continue
        final = traj[-1]
        peak = max(traj)
        swing = peak - min(traj)
        if final >= target:
            out.append(
                Guardian(
                    field=fld,
                    kind="pin",
                    note=f"字段「{fld}」已稳定达标（≥{_pct(target)}）：保持当前规则，后续轮次勿大改。",
                )
            )
        elif (peak - final) > REGRESS_EPS or swing >= VOLATILE_SWING:
            out.append(
                Guardian(
                    field=fld,
                    kind="caution",
                    note=(
                        f"字段「{fld}」识别在迭代中波动（峰值 {_pct(peak)} → 末 {_pct(final)}）："
                        "优先采用表现最佳的规则，勿反复改写。"
                    ),
                )
            )
    return out


def render_guardian_block(guardians: Sequence[Guardian]) -> str:
    """渲染受保护守护段；无守护时返回空串。"""
    if not guardians:
        return ""
    lines = [GUARDIAN_BLOCK_HEADER]
    lines.extend(f"- {g.note}" for g in guardians)
    return "\n".join(lines)
