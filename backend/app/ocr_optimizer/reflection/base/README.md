# Reflection base — 公共基底 + 薄变体（Prompt System v2 Phase 5）

Skill-creator 的「公共基底 + 薄变体」落地：把所有反思 skill / 国家 agent **共享**的内容
集中到这里一份，各 skill 只写自己的**增量**（字段信息 + 客户修正 + 该字段的分析要点 + match 谓词）。

## 共享资产

| 文件 | 占位符 | 内容 | 谁用 |
|---|---|---|---|
| `doctrine.md` | `{base_doctrine}` | 泛化教义（跨样本归纳 + 相对锚点，禁绝对坐标） | 所有 edit / add skill + 国家 agent |
| `edit_output.md` | `{base_edit_output}` | edit 类的结构化输出 JSON（rationale/fix_suggestion/FieldRule 字段等） | edit 类 skill + 国家 edit agent |

`add` 类（new_field / 国家 add agent）生成的是**整条新模块**（module_key/ocr_prompt/schema_fragment…），
输出形状不同，自带输出段，只引用 `{base_doctrine}`。

## 注入机制

`base_assets.base_format_vars()` 把上述占位符读成字符串，**两个 loader**
（`skills_loader.Skill.render` / `country_agents_loader.CountryAgent.render`）在 `.format()` 时
一并注入。注入值里的 `{ }` 是 JSON 字面量，**不会被二次格式化**，安全。

## 写一个新 skill（薄变体）

```yaml
key: <唯一>
display_name: <UI 名>
version: <整数，prompt 改时 bump>
match: { diff_kind: edit, <谓词>: <bool> }
prompt: |
  你是 OCR prompt 设计专家，反思「<该 skill 的主题>」。

  ## 字段信息
  - module_key: {module_key}
  - 当前 OCR 提示词:
  ```
  {original_ocr_prompt}
  ```

  ## 客户修正
  - 原始: {original_value} → 正确: {corrected_value}

  ## 分析要点（该 skill 专属，1~5 条）
  1. …

  {base_doctrine}

  {base_edit_output}
```

改了 `doctrine.md` / `edit_output.md` = **一次改、全 skill 生效**（避免逐文件漂移）。
