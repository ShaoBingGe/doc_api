"""把 JSON Schema 的字段 description 渲染成文本附录。

**为什么需要它**：`response_schema` 是 Gemini 的结构化输出通道 —— 免费
（不计入 prompt token）且强制。但 DashScope 的视觉模型**不支持**它，qwen
收到 `runtime_config["response_schema"]` 只能丢弃。

后果在模板瘦身后变得致命：一旦国家模板把字段级规则从 prompt 正文迁进
schema description（单一事实源，见 docs/plan-my-template-slimming.md），
主力 Gemini 熔断切到 qwen 备份时，**那些规则会整体消失** —— 同一份文档在
主备两条路上按两套规则识别。

这里提供第三条通道：不支持结构化输出的 provider，把 description 渲染成
文本附录拼进 instruction。单一事实源仍在 schema，两个 provider 各取所需
（qwen 付这笔 token，Gemini 不付）。

渲染只取**语义信息**（字段路径 · 类型 · enum · description），不复述
JSON Schema 的结构语法 —— 结构约束由 composer 的紧凑字段树负责，两者不重叠。
"""

from __future__ import annotations

from typing import Any

_HEADER = """

━━━━━━ 字段识别细则（逐字段，必须遵守）━━━━━━
下列是每个输出字段的取值规则：在哪找、找什么、什么格式、什么禁取。
**与上文正文规则冲突时以正文为准**；正文未涉及的以本表为准。
"""
_FOOTER = "━━━━━━ 字段识别细则结束 ━━━━━━\n"


def _walk(props: dict, prefix: str, out: list[str]) -> None:
    for name, spec in (props or {}).items():
        if not isinstance(spec, dict):
            continue
        path = f"{prefix}{name}"
        desc = (spec.get("description") or "").strip()
        enum = spec.get("enum")
        typ = str(spec.get("type") or "").lower()

        head = f"- `{path}`"
        if typ:
            head += f"（{typ}"
            if enum:
                head += "，仅限：" + " | ".join(str(e) for e in enum)
            head += "）"
        elif enum:
            head += "（仅限：" + " | ".join(str(e) for e in enum) + "）"
        if desc:
            # description 多行时缩进续行，保持与列表项的从属关系
            body = desc.replace("\n", "\n  ")
            head += f"：{body}"
        if desc or enum:
            out.append(head)

        # 明细数组的行字段
        items = spec.get("items")
        if isinstance(items, dict) and isinstance(items.get("properties"), dict):
            _walk(items["properties"], f"{path}[].", out)


def render_schema_descriptions(schema: Any) -> str:
    """→ 文本附录；schema 里没有任何 description 时返回空串（调用方据此跳过）。

    支持国家模板的两种根形状：`{type: ARRAY, items: {anyOf: [...]}}`
    与直接的 object schema。anyOf 分支全部展开（invoice/receipt 与 other
    两支的字段并集）。
    """
    if not isinstance(schema, dict):
        return ""

    branches: list[dict] = []

    def collect(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if isinstance(node.get("properties"), dict):
            branches.append(node["properties"])
        for key in ("items",):
            if key in node:
                collect(node[key])
        for alt in node.get("anyOf") or node.get("oneOf") or []:
            collect(alt)

    collect(schema)
    if not branches:
        return ""

    lines: list[str] = []
    seen: set[str] = set()
    for props in branches:
        buf: list[str] = []
        _walk(props, "", buf)
        for ln in buf:
            # anyOf 各分支常有同名字段（docType/page），去重保首见
            key = ln.split("：", 1)[0]
            if key in seen:
                continue
            seen.add(key)
            lines.append(ln)

    if not lines:
        return ""
    return _HEADER + "\n".join(lines) + "\n" + _FOOTER
