"""编辑意图分类器（deterministic · 反思前置）.

客户在工作区修正一个字段时，他的「编辑动作形态」本身就携带了根因信息：

  NORMALIZE   修正后的值是原值「删去若干字符」得到的（空格/破折号/货币符/
              前后缀字母…）→ 取值位置是对的，**输出规范**错了。反思应产出
              规范化规则（去哪些字符、何种模式），而不是改取值锚点。
  RETARGET    修正后的值与原值实质不同（不是删字符可得）→ 抓取**内容/范围**
              错了。反思应全文检索正确值出现的位置、分析邻近锚点并跨样本
              验证相关性。
  RENAME_ONLY 只改了字段名 → 命名/Schema 问题，值规则不动。
  TYPE_ONLY   只改了格式/类型声明 → 输出类型约束问题。
  CASE_ONLY   仅大小写差异 → 大小写规范。
  MIXED       同时含改名与改值等多重动作（按值的子分类继续处理）。

为什么用纯代码而不是 LLM：这一层是**证据提取**，字符级 diff 是确定性的；
把准确的证据（删了哪些字符、删除模式是前缀/后缀/中间）注入反思 prompt，
LLM 不必猜根因——猜测正是历史上反思建议泛化差的主因。

输出注入两处：
  - reflector：渲染为「编辑意图分析」块追加到反思 user prompt；
  - ReflectionResult.provenance：供 reconciler 在跨轮矛盾时判断最新意图。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


# 常见「被删除字符」的语义标注——证据块里直接告诉 LLM 删的是什么类别的字符。
_CHAR_LABELS = {
    " ": "空格",
    " ": "不间断空格",
    "-": "破折号/连字符",
    "–": "长破折号",
    ",": "逗号(千分位?)",
    ".": "句点",
    "/": "斜杠",
    ":": "冒号",
    "(": "左括号",
    ")": "右括号",
    "$": "货币符$",
    "¥": "货币符¥",
    "€": "货币符€",
    "£": "货币符£",
}


@dataclass
class EditIntent:
    """一次字段修正的结构化意图。"""
    intent: str                       # NORMALIZE / RETARGET / RENAME_ONLY / TYPE_ONLY / CASE_ONLY / MIXED / NONE
    name_changed: bool = False
    format_changed: bool = False
    value_changed: bool = False
    # NORMALIZE 专属证据
    removed_chars: list[str] = field(default_factory=list)   # 被删除的字符（去重）
    removed_prefix: str = ""          # 被整体删除的前缀（如 "W1 " / "RM "）
    removed_suffix: str = ""          # 被整体删除的后缀
    removal_pattern: str = ""         # strip_prefix / strip_suffix / strip_chars / strip_both
    # RETARGET 专属证据
    overlap_ratio: float = 0.0        # 原值与正确值的字符重叠率（低 → 完全抓错）
    notes: list[str] = field(default_factory=list)

    def render_block(self) -> str:
        """渲染为注入反思 prompt 的「编辑意图分析」块；NONE 时返回空串。"""
        if self.intent == "NONE":
            return ""
        lines = ["# 编辑意图分析（系统对客户修正动作的字符级判定，证据可信）"]
        zh = {
            "NORMALIZE": "格式规范化修正——取值位置正确，但输出需删除/规范某些字符",
            "RETARGET": "取值内容修正——当前抓取的内容或范围错误，需要重新定位",
            "RENAME_ONLY": "仅字段改名——值规则不需要变动",
            "TYPE_ONLY": "仅输出类型/格式声明调整",
            "CASE_ONLY": "仅大小写规范调整",
            "MIXED": "复合修正（改名 + 值/格式同时变化）",
        }
        lines.append(f"- 判定：**{self.intent}** — {zh.get(self.intent, '')}")
        if self.intent in ("NORMALIZE", "MIXED") and (
            self.removed_chars or self.removed_prefix or self.removed_suffix
        ):
            if self.removed_prefix:
                lines.append(f"- 客户删除了**前缀** `{self.removed_prefix}`")
            if self.removed_suffix:
                lines.append(f"- 客户删除了**后缀** `{self.removed_suffix}`")
            if self.removed_chars:
                labeled = [
                    f"`{c}`({_CHAR_LABELS[c]})" if c in _CHAR_LABELS else f"`{c}`"
                    for c in self.removed_chars
                ]
                lines.append(f"- 客户删除的字符：{ '、'.join(labeled) }")
            lines.append(
                "- 结论指引：请把上述删除动作总结为一条**输出规范化规则**"
                "（形如「输出时去掉 X / 仅保留数字与字母」），并验证它在全部样本上成立；"
                "**不要**因此改动取值锚点。"
            )
        if self.intent == "RETARGET":
            lines.append(
                f"- 原值与正确值的字符重叠率仅 {self.overlap_ratio:.0%} —— "
                "当前规则大概率抓到了**别的字段/别的位置**。"
            )
            lines.append(
                "- 结论指引：请结合下方「全文检索报告」与「跨样本对照」，"
                "找出正确值在票面上的**邻近标签锚点**，归纳一条覆盖全部样本的取值规则，"
                "并在「排歧」中写明与当前误抓内容的区分依据。"
            )
        for n in self.notes:
            lines.append(f"- {n}")
        return "\n".join(lines)


def classify(diff: dict) -> EditIntent:
    """对一条 FieldDiff 做字符级意图分类。永不抛异常（证据层必须可靠）。"""
    try:
        return _classify(diff)
    except Exception:  # noqa: BLE001 — 证据提取失败时退化为 NONE，不阻塞反思
        return EditIntent(intent="NONE")


def _classify(diff: dict) -> EditIntent:
    if diff.get("kind") != "edit":
        return EditIntent(intent="NONE")

    on = (diff.get("original_name") or "").strip()
    cn = (diff.get("corrected_name") or "").strip()
    of = (diff.get("original_format") or "").strip()
    cf = (diff.get("corrected_format") or "").strip()
    ov = "" if diff.get("original_value") is None else str(diff.get("original_value"))
    cv = "" if diff.get("corrected_value") is None else str(diff.get("corrected_value"))

    name_changed = bool(on and cn and on != cn)
    format_changed = bool(of and cf and of != cf)
    value_changed = ov.strip() != cv.strip()

    it = EditIntent(
        intent="NONE",
        name_changed=name_changed,
        format_changed=format_changed,
        value_changed=value_changed,
    )

    if not value_changed:
        if name_changed and format_changed:
            it.intent = "MIXED"
        elif name_changed:
            it.intent = "RENAME_ONLY"
        elif format_changed:
            it.intent = "TYPE_ONLY"
        return it

    # ── 值变化的子分类 ────────────────────────────────────────────────────
    o, c = ov.strip(), cv.strip()

    if o.lower() == c.lower() and o != c:
        it.intent = "CASE_ONLY"
        it.notes.append("原值与正确值仅大小写不同 → 产出大小写规范规则。")
        return it

    deletion = _deletion_evidence(o, c)
    if deletion is not None:
        it.intent = "MIXED" if name_changed else "NORMALIZE"
        it.removed_chars = deletion["removed_chars"]
        it.removed_prefix = deletion["removed_prefix"]
        it.removed_suffix = deletion["removed_suffix"]
        it.removal_pattern = deletion["pattern"]
        return it

    it.intent = "MIXED" if name_changed else "RETARGET"
    it.overlap_ratio = _overlap_ratio(o, c)
    return it


def _deletion_evidence(original: str, corrected: str) -> dict | None:
    """若 corrected 可由 original「只删字符」得到，返回删除证据；否则 None。

    覆盖三种高频形态（用户 1.1 场景）：
      1. 删整段前缀/后缀（"W1 529054" → "529054"；"600.00 RM" → "600.00"）
      2. 删散布字符（"6,000.00" → "6000.00"；"A-123-456" → "A123456"）
      3. 两者组合
    判定标准：corrected 是 original 的**子序列**（保持字符相对顺序）。
    """
    if not corrected or len(corrected) >= len(original):
        return None
    # 子序列检查 + 收集被跳过（=被删除）的字符
    removed: list[str] = []
    ci = 0
    for ch in original:
        if ci < len(corrected) and ch == corrected[ci]:
            ci += 1
        else:
            removed.append(ch)
    if ci != len(corrected):
        return None  # 不是纯删除（有改写）→ 交给 RETARGET

    # 前缀/后缀整段删除检测
    prefix = ""
    if original.endswith(corrected):
        prefix = original[: len(original) - len(corrected)]
    suffix = ""
    if original.startswith(corrected):
        suffix = original[len(corrected):]

    if prefix and suffix:
        pattern = "strip_both"
    elif prefix:
        pattern = "strip_prefix"
    elif suffix:
        pattern = "strip_suffix"
    else:
        pattern = "strip_chars"

    # 去重保序
    seen: set[str] = set()
    uniq = [c for c in removed if not (c in seen or seen.add(c))]
    return {
        "removed_chars": uniq,
        "removed_prefix": prefix,
        "removed_suffix": suffix,
        "pattern": pattern,
    }


def _overlap_ratio(a: str, b: str) -> float:
    """粗粒度字符重叠率（多重集合交集 / 较长串长度）——只用于证据展示。"""
    if not a or not b:
        return 0.0
    ca, cb = Counter(a), Counter(b)
    inter = sum((ca & cb).values())
    return inter / max(len(a), len(b))


# ── 全文检索（用户 1.2 场景）──────────────────────────────────────────────


def _norm_for_search(s: str) -> str:
    """检索用归一化：去空格/破折号/逗号、统一小写——容忍 OCR 输出与客户
    填写之间的格式差异。"""
    return re.sub(r"[\s\-,]", "", s or "").lower()


def search_value_in_outputs(
    corrected_value: str,
    sample_outputs: dict[str, dict],
) -> list[dict]:
    """在每个样本最新的 OCR 结构化输出里全文检索 corrected_value。

    Args:
        corrected_value: 客户填写的正确值
        sample_outputs: {sample_label: structured_data(dict|list)} —— 每个
            已审视样本最新一次 OCR 的完整 JSON 输出

    Returns:
        [{"sample": str, "field_path": str, "value": str,
          "match": "exact"|"normalized"|"substring"}]
        —— 正确值实际「藏在」哪些字段下。空列表 = 全文未检出
        （值可能在票面上但 OCR 没抓到任何字段里）。
    """
    cv = (corrected_value or "").strip()
    if not cv:
        return []
    cv_norm = _norm_for_search(cv)
    hits: list[dict] = []

    def _walk(node, path: str, sample: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else k, sample)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]", sample)
        elif node is not None:
            sv = str(node).strip()
            if not sv:
                return
            if sv == cv:
                hits.append({"sample": sample, "field_path": path, "value": sv, "match": "exact"})
            elif _norm_for_search(sv) == cv_norm:
                hits.append({"sample": sample, "field_path": path, "value": sv, "match": "normalized"})
            elif len(cv_norm) >= 4 and cv_norm in _norm_for_search(sv):
                hits.append({"sample": sample, "field_path": path, "value": sv, "match": "substring"})

    for sample, data in (sample_outputs or {}).items():
        _walk(data, "", sample)

    # exact > normalized > substring；每个样本最多保留 3 条，避免 prompt 膨胀
    order = {"exact": 0, "normalized": 1, "substring": 2}
    hits.sort(key=lambda h: (h["sample"], order.get(h["match"], 9)))
    out: list[dict] = []
    per_sample: Counter = Counter()
    for h in hits:
        if per_sample[h["sample"]] < 3:
            out.append(h)
            per_sample[h["sample"]] += 1
    return out


def render_retrieval_block(
    corrected_value: str,
    hits: list[dict],
    *,
    searched_samples: int,
) -> str:
    """渲染「全文检索报告」块。没有检索动作时返回空串。"""
    if not searched_samples:
        return ""
    lines = [
        "# 全文检索报告（系统在全部样本的 OCR 输出里检索客户期望值）",
        f"- 检索目标：`{corrected_value}`（共检索 {searched_samples} 个样本）",
    ]
    if not hits:
        lines.append(
            "- 结果：**全文未检出** —— 正确值不在当前 OCR 的任何字段里，"
            "说明票面上该内容没有被任何规则抓到。请从票面布局常识出发，"
            "推断它通常出现的区域与邻近标签，并在取值锚点中写明。"
        )
        return "\n".join(lines)
    lines.append("- 结果：正确值出现在以下字段（说明取值规则抓错了来源/范围）：")
    match_zh = {"exact": "完全一致", "normalized": "去格式后一致", "substring": "包含其中"}
    for h in hits[:9]:
        lines.append(
            f"  - 样本 `{h['sample']}` 的字段 `{h['field_path']}` = "
            f"{h['value']!r}（{match_zh.get(h['match'], h['match'])}）"
        )
    lines.append(
        "- 结论指引：上述字段路径揭示了正确值的真实来源。请据此修正取值锚点"
        "（指向该来源的邻近标签/区块），写明与误抓位置的排歧依据，"
        "并核对该规则在每个样本上都取到正确值。"
    )
    return "\n".join(lines)
