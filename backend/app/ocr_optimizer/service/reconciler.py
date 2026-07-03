"""
Cross-round prompt reconciler (Prompt System v2 — Phase 4 / requirement 5).

A field's ocr_prompt accumulates "# 客户反馈补充" blocks across rounds (§⑤.3:
multiple skills/diffs append, never overwrite). Over several rounds those blocks
can CONTRADICT each other (round 1 "取括号内的值", round 3 "取括号外") — and a
contradictory prompt degrades extraction. This reconciler collapses the
accumulated prompt + the latest suggestions into ONE coherent, non-contradictory
field prompt, **prioritizing the latest customer intent** on conflict.

Design constraints:
  - Runs UPSTREAM of the composer (composer stays pure / no LLM, CLAUDE.md §③.4).
  - Produces a coherent ocr_prompt (keeps the rich recognition hints) — it does
    NOT switch to the lossy FieldRule skeleton; the FieldRule is an INPUT.
  - fail-open: any LLM error → returns None, caller keeps the blind-append
    behaviour (§⑤.3) so progress is never blocked.
  - Only worth invoking when the prompt already carries accumulated feedback
    (`has_accumulated_feedback`) — first-time edits have nothing to reconcile.
"""

from __future__ import annotations

import logging
from typing import Any

from .llm_failover import llm_text_completion_failover

logger = logging.getLogger(__name__)

_FEEDBACK_MARKER = "# 客户反馈补充"

_RECONCILER_SYSTEM = (
    "你是 OCR 字段 prompt 的「协调器 / 统筹器」。给你一个字段当前的 prompt（可能在多轮迭代里"
    "累积了相互矛盾或重复的指令）、本轮新的修订建议（可能为空——为空时本次是纯统筹整合）、"
    "该字段的结构化规则，以及最新的客户意图。"
    "请产出**一份单一、自洽、无矛盾**的字段 prompt：\n"
    "1. 当指令冲突时，**一律以「最新客户意图」为准**，删除与之矛盾的旧指令；\n"
    "2. 保留仍然有用的识别要点（语义、标签别名、相对锚点、格式/值模式/枚举、排歧），"
    "但去重、合并——同义指令并成一条，多条格式规则统一为一条完整规范；\n"
    "3. 统筹后的 prompt 应按「语义 → 别名 → 锚点 → 格式约束 → 排歧」组织，"
    "不保留「第 N 轮反馈」之类的过程性标注；\n"
    "4. 不要发明客户没要求的新规则。**完整保留有效规则优先于篇幅**——只删"
    "纯重复与被最新意图取代的指令，不要为了简洁牺牲仍然有效的识别要点。\n"
    "只返回严格 JSON：{\"coherent_prompt\": <字符串>, \"dropped\": [<被删除/被取代的旧规则>], "
    "\"rationale\": <一句话说明>}。不要 markdown 围栏。"
)


def has_accumulated_feedback(prompt: str | None) -> bool:
    """True when the prompt already carries >= 1 accumulated feedback block, i.e.
    a later round could introduce a contradiction worth reconciling."""
    return bool(prompt) and prompt.count(_FEEDBACK_MARKER) >= 1


# 模块体「膨胀」阈值：反馈块 ≥3 或正文超长 → 即便本轮没有新建议，也值得
# 做一次纯统筹整合（把堆叠的历史反馈收敛为单一规则集）。
# 批次6：600 字符对「语义+别名+锚点+格式+排歧」的正常字段 prompt 太低，
# 会让健康 prompt 每轮被 LLM 有损重写（不可复现漂移）——放宽到 1500。
BLOAT_FEEDBACK_BLOCKS = 3
BLOAT_PROMPT_CHARS = 1500


def is_bloated(prompt: str | None) -> bool:
    """True 当模块 prompt 已经因反馈堆叠而膨胀，应触发统筹整合。"""
    if not prompt:
        return False
    return (
        prompt.count(_FEEDBACK_MARKER) >= BLOAT_FEEDBACK_BLOCKS
        or len(prompt) > BLOAT_PROMPT_CHARS
    )


# ── 矛盾检测门（批次6，纯代码零 LLM）─────────────────────────────────────────
# 红线⑤：「仅当跨轮矛盾时才调 reconciler」。历史实现没有任何矛盾判定——
# 第二轮起逢新建议必触发 LLM 全量重写，仍然有效的规则被有损压缩，prompt
# 每轮不可复现地漂移。这里用确定性启发式做门：
#   - 显式对立指令对（取括号内 vs 括号外、保留 vs 去掉…）；
#   - 同一「规范主题」（前缀/后缀/小数/千分位/大小写/括号/日期格式）在旧
#     反馈与新建议中都出现但文本不同 → 疑似矛盾。
# 过触发的代价是多一次 LLM 协调（旧行为）；欠触发的代价是确定性盲追加
# （composer 折叠去重，零信息损失）——两侧都安全。

_OPPOSITE_PAIRS = [
    ("括号内", "括号外"),
    ("保留前缀", "去掉前缀"), ("保留前缀", "删除前缀"),
    ("保留后缀", "去掉后缀"), ("保留后缀", "删除后缀"),
    ("保留千分位", "去掉千分位"), ("保留千分位", "去千分位"),
    ("大写", "小写"),
]
_TOPIC_TOKENS = ["前缀", "后缀", "小数", "千分位", "大小写", "括号",
                 "日期格式", "货币符", "单位", "正负号"]


def _feedback_lines(prompt: str) -> list[str]:
    """抽取 prompt 中全部反馈块的内容行（去块头/空行）。"""
    if not prompt or _FEEDBACK_MARKER not in prompt:
        return []
    lines: list[str] = []
    for seg in prompt.split(_FEEDBACK_MARKER)[1:]:
        for raw in seg.splitlines():
            t = raw.strip().lstrip("-• ").strip()
            if t and not t.startswith("（") and not t.startswith("("):
                lines.append(t)
    return lines


def has_contradiction(current_prompt: str | None, new_suggestions: list[str]) -> bool:
    """判定「本轮新建议」与「已累积反馈」之间是否疑似矛盾。"""
    old_lines = _feedback_lines(current_prompt or "")
    new_lines = [s.strip() for s in (new_suggestions or []) if s and s.strip()]
    if not old_lines or not new_lines:
        return False
    old_text = "\n".join(old_lines)
    for s in new_lines:
        if s in old_text:
            continue  # 完全重复 → 折叠去重即可
        for a, b in _OPPOSITE_PAIRS:
            if (a in s and b in old_text) or (b in s and a in old_text):
                return True
        for topic in _TOPIC_TOKENS:
            if topic in s and topic in old_text:
                # 同主题但内容不同 → 疑似矛盾，交给 LLM 裁决
                if all(s not in ln and ln not in s for ln in old_lines if topic in ln):
                    return True
    return False


def reconcile_module_prompt(
    *,
    module_key: str,
    display_name: str | None,
    current_prompt: str,
    new_suggestions: list[str],
    field_rule: Any | None = None,
    latest_intent: dict | None = None,
    processor_spec: str = "gemini",
    model_name: str | None = None,
) -> str | None:
    """Merge current_prompt + new_suggestions into one coherent prompt, latest
    intent winning on conflict. Returns the coherent prompt string, or None on
    any failure (caller then falls back to blind-append)."""
    intent = latest_intent or {}
    fr_text = ""
    if field_rule is not None:
        try:
            fr_text = field_rule.render_skeleton()  # structured rule as guidance
        except Exception:  # noqa: BLE001
            fr_text = ""

    user_prompt = (
        f"# 字段\n- module_key: {module_key}\n- 显示名: {display_name or ''}\n\n"
        f"# 当前 prompt（可能含跨轮累积/矛盾）\n```\n{(current_prompt or '').strip()}\n```\n\n"
        f"# 本轮新修订建议\n"
        + ("\n".join(f"- {s}" for s in new_suggestions if s) or "（无）")
        + "\n\n# 结构化规则（参考）\n"
        + (fr_text or "（无）")
        + "\n\n# 最新客户意图（冲突时以此为准）\n"
        + (
            f"- 原值: {intent.get('original_value')}\n"
            f"- 正确值: {intent.get('corrected_value')}\n"
            f"- 原字段名: {intent.get('original_name')}\n"
            f"- 修正后字段名: {intent.get('corrected_name')}\n"
            f"- 格式: {intent.get('corrected_format')}\n"
        )
        + "\n请输出协调后的单一 prompt（严格 JSON）。"
    )
    try:
        result = llm_text_completion_failover(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=_RECONCILER_SYSTEM,
            user_prompt=user_prompt,
            as_json=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconcile_module_prompt LLM failed for %s: %s", module_key, exc)
        return None
    if not isinstance(result, dict):
        return None
    coherent = result.get("coherent_prompt")
    if isinstance(coherent, str) and coherent.strip():
        dropped = result.get("dropped") or []
        if dropped:
            logger.info("reconciler %s dropped %d stale/contradictory rule(s): %s",
                        module_key, len(dropped), str(dropped)[:200])
        out = coherent.strip()
        # 批次6：保留反馈 marker——协调成功后 marker 消失会让
        # has_accumulated_feedback 状态机振荡（下一轮误判「无累积」走盲追加，
        # 再下一轮又整合），两次相同输入产出不同 prompt 形态。协调后的 prompt
        # 本身就是反馈的整合结果，marker 记录这一事实。
        if _FEEDBACK_MARKER not in out:
            out += f"\n\n{_FEEDBACK_MARKER}（已协调整合，历史反馈已并入上文规则）"
        return out
    return None
