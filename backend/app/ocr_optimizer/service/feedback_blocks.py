"""客户反馈块的**唯一**常量与共享判定。

「# 客户反馈补充」marker 此前在 composer / reconciler / module_optimizer
三处各定义一份、customer_iteration 里再硬编码一份——四处对"什么是反馈块"
的判定各自为政，任何一处改格式（如加轮次标注），折叠去重 / 矛盾门 /
保留性守护三套机制会静默漂移互咬（结构审查 F2）。

行级解析仍留在各消费方（语义确有差异：折叠要保留原始行、守护只看实质行、
矛盾门要剥列表符），但 marker 字符串与「实质行」判定必须同源。
"""

from __future__ import annotations

MARKER = "# 客户反馈补充"


def iter_substantive_lines(prompt: str | None) -> list[str]:
    """抽取 prompt 中全部反馈块的「实质内容行」：去块头、剥列表符、
    跳过空行与括号标注行（如「（第 2 轮）」「（已协调整合…）」）。"""
    if not prompt or MARKER not in prompt:
        return []
    lines: list[str] = []
    for seg in prompt.split(MARKER)[1:]:
        for raw in seg.splitlines():
            t = raw.strip().lstrip("-• ").strip()
            if t and not t.startswith("（") and not t.startswith("("):
                lines.append(t)
    return lines


def append_feedback(prompt: str | None, text: str) -> str:
    """把一段反馈以标准块形式追加到 prompt 尾部（§⑤.3 盲追加的唯一入口）。"""
    base = (prompt or "").rstrip()
    return f"{base}\n\n{MARKER}\n{text.strip()}"
