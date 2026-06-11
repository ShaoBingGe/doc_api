## 输出格式（严格 JSON，不要 markdown 围栏）
```json
{
  "rationale": "<3-5 句根因判断>",
  "fix_suggestion": "<可直接拼到 prompt 的改进文本片段>",
  "description_patch": "<可选；不需要时空串>",
  "schema_patch": {"type": "<STRING/NUMBER/INTEGER/...>", "format": "<可选 ISO 8601 等；仅格式/类型变更时填>"},
  "semantic": "<字段业务语义，一句话>",
  "aliases": ["<票面实际标签 1>", "<行业惯例别名 2>", "<常见其他写法 3>"],
  "anchors": ["<相对锚点 1>", "<相对锚点 2>"],
  "format_rule": "<取值/格式约束，如去千分位 / 日期统一 YYYY-MM-DD>",
  "value_pattern": "<可选；值的正则或模式描述，如 ^[A-Z]{2,3}-\\d{6}$；无稳定模式时空串>",
  "enum_values": ["<可选；值为有限集合时列出全部合法取值；非枚举字段给空数组>"],
  "disambiguation": ["<易混字段及区分依据>"],
  "generalization": {"rule": "<覆盖全部样本的取值规则>", "evidence_per_sample": ["<样本观测>"], "holds_for_all": true}
}
```
