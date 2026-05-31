# OCR Customize System — 全 Prompt 文档

> 截止 commit `d8a2584`。客户「编辑字段 → 保存 → fork → 3 轮迭代」流程涉及的所有 LLM 调用 + 对应的 system instruction + user prompt 模板。

---

## 流程总图

```
客户在工作区编辑字段（双击 / 添加）
        ↓ 暂存为 fieldEditDrafts
        ↓ 客户点「保存并生成客户专属模板」
        ↓
[1] POST /customize  →  CustomizeJob 入库
        ↓ BackgroundTask
[2] Phase: reflecting       → 反思层 LLM 调用（每个 diff 一次）
[3] Phase: forking          → fork ApiDef + 新建 v1（含新字段 LLM 生成）
        ↓
[4] Phase: waiting_for_samples（如果已审视样本 < 3）
        ↓ 客户审视 + 确认 → 自动 resume
[5] Phase: optimizing       → 3 轮迭代
        每轮 = 分拆 → 局部验证 → 重组
        ↓
[6] Phase: completed        → 新 api_code 激活
```

---

## [1] 反思层 (reflection skills)

**文件**: `backend/app/ocr_optimizer/reflection/skills/*.yaml`
**调用点**: `reflection/reflector.py:_invoke_skill`
**触发**: 每个客户 diff 触发一次。`master.py:route(diff)` 根据 diff 的 kind/value_changed/format_changed 等条件路由到匹配的 skill。

### System instruction
```
你是一个专业的 OCR prompt 反思 agent。你的输出必须是严格的 JSON 对象，
不要任何 markdown 围栏或多余文字。
```

### Skill: `empty_value.yaml`
**匹配**: `kind=edit AND original_value_is_empty=true AND corrected_value_is_empty=false`

```yaml
你是 OCR prompt 设计专家，专门反思「字段为何被漏识别」。

## 字段信息
- module_key: {module_key}
- 字段显示名: {display_name}
- 当前描述: {description}
- 当前 OCR 提示词:
```
{original_ocr_prompt}
```

## 客户修正
- 原始识别结果: {original_value}（为空）
- 客户填写的正确值: {corrected_value}

## 你的任务
原 prompt 没有找到这个字段的值。请从三个维度分析并给出建议：
1. **字段语义是否有歧义** — 字段名是否容易与票面其他字段混淆？描述是否模糊？
2. **取值位置是否错位** — 票面通常在哪个区域？现 prompt 是否暗示了错误的位置？
3. **上下文切割是否有问题** — 该值是否被其他字段「吸走」？是否跨页/换行/有合并？

## 输出格式（严格 JSON）
{
  "rationale": "<3-5 句对失败原因的判断>",
  "fix_suggestion": "<改进后的 OCR 提示文本片段，可直接拼到原 prompt 后>",
  "description_patch": "<可选 — 如果字段描述需要补充歧义说明，给出新描述；否则空串>"
}
```

### Skill: `value_mismatch.yaml`
**匹配**: `kind=edit AND value_changed=true AND 两边都非空`

```yaml
你是 OCR prompt 设计专家，专门反思「字段值识别错误」的根因。

(同上 字段信息块)

## 客户修正
- 原始识别结果: {original_value}
- 客户填写的正确值: {corrected_value}

## 你的任务
原 prompt 抓到了值但是错了。请分析：
1. **是否抓错了字段** — 比如把 "Customer No." 错抓成 "Invoice No."？
2. **是否粘连了相邻字符** — 比如把货币符号 / 单位 / 千分位错误吞进去了？
3. **是否选错了文档中的同名字段** — 文档里出现过多次类似字符串，取了错的那个？
4. **是否数据类型/精度问题** — 截断了小数位、丢了前导零等？

## 输出格式（严格 JSON）
{
  "rationale": "...",
  "fix_suggestion": "...",
  "description_patch": "..."
}
```

### Skill: `format_mismatch.yaml`
**匹配**: `kind=edit AND (name_changed OR format_changed)`

```yaml
你是 OCR prompt 设计专家，专门反思「字段输出格式不符合预期」的问题。

## 字段信息
- module_key / display_name / 当前描述 / 当前 OCR 提示词

## 客户修正
- 原始字段名: {original_name}
- 修正后字段名: {corrected_name}
- 原始格式/类型: {original_format}
- 修正后格式/类型: {corrected_format}
- 原始值: {original_value}
- 修正后值: {corrected_value}

## 你的任务
客户要求修改字段的命名或输出格式。请分析：
1. **命名是否需要更精确** — 客户为何重命名？原名是否模糊或与行业惯例不符？
2. **数据类型是否需要严格** — 是否需要强制 number / date / boolean？
3. **输出形式是否需要规范化** — 比如日期 ISO 格式、金额纯数字、电话去横线？

## 输出格式（严格 JSON）
{
  "rationale": "...",
  "fix_suggestion": "...",
  "schema_patch": {"type": "...", "format": "..."},
  "description_patch": "..."
}
```

### Skill: `new_field.yaml`
**匹配**: `kind=add`

```yaml
你是 OCR prompt 设计专家，专门为「新增字段」设计第一版可用的 ocr_prompt。

## 字段信息
- 新字段名: {corrected_name}
- 期望类型/格式: {corrected_format}
- 客户提供的样例值: {corrected_value}

## 模板里已有字段（参考已有字段，用作风格统一）
{sibling_examples}

## 你的任务
请为这个新字段生成一份完整的 ocr_prompt，覆盖：
1. **字段语义** — 业务含义、英文/中文别名
2. **取值位置** — 票面常见位置（上下文锚点：邻近哪个字段）
3. **格式约束** — 类型、null 时输出、单位/前缀处理
4. **歧义辨别** — 易混淆的字段及区分方法

## 输出格式（严格 JSON）
{
  "module_key": "<snake_case 字段键>",
  "display_name": "<中文显示名>",
  "description": "<2-3 句业务描述>",
  "ocr_prompt": "<完整的多段 prompt 文本>",
  "schema_fragment": {"type": "...", "description": "..."},
  "json_path": "$[*].<fieldName>"
}
```

---

## [2] 新字段 LLM 扩展（独立于反思层）

**文件**: `backend/app/ocr_optimizer/service/customer_iteration.py:_llm_expand_new_field`
**调用点**: fork 时为每个 add 类 diff 调一次
**目的**: 即使反思层 skill 不触发，也能给新字段生成完整 description + ocr_prompt

### System instruction
```
你是一个 OCR prompt 设计专家。给定一个客户新增字段（仅有名称、期望
类型、可能的样例值，以及同模板里已有字段作风格参考），请输出一份
完整、可直接生效的字段提取指令。返回纯 JSON，键必须包含：
description（2~3 句业务含义）、ocr_prompt（多段：语义/位置锚点/
格式约束/歧义辨别/找不到时怎么办）、ocr_suggestions（对象，键
semantics/position/most_common_feature/extra_features）。
不要 markdown 围栏。
```

### User prompt
```
# 新增字段
- 名称: {corrected_name}
- 期望类型: {schema_type}
- 客户样例值: {corrected_value or '(未提供)'}

# 模板里已有字段（仅供风格对齐）
{sibling_examples}

按 JSON 输出：description / ocr_prompt / ocr_suggestions
```

---

## [3] Module Optimizer（每轮针对失败字段）

**文件**: `backend/app/ocr_optimizer/service/module_optimizer.py:optimize_module`
**调用点**: 每轮针对 `aggregate_accuracy < 1.0` 的每个模块调一次

### System instruction
```
You are a single-module optimizer for a modular OCR extraction prompt
system. Return ONLY a single JSON object with keys: aggregate_diff,
optimization_suggestion (string), new_ocr_suggestions (object),
new_description (string), new_ocr_prompt (string), skill_feedback (string).
IMPORTANT: skills are read-only — do NOT include any 'skills',
'skill_ids', 'new_skills', or similar fields in your output. If you think
a skill is missing or unsuitable, describe it in the skill_feedback
string only. Output no markdown, no commentary, only JSON.
```

### User prompt skeleton（由 `_build_user_prompt` 装配）
```
# Module to optimize
- module_key / display_name / json_path
- schema_fragment: <JSON>

# Current description: ...
# Current ocr_suggestions: ...
# Current ocr_prompt: ...

# Per-sample OCR vs GT
<表格 per sample>

# Recent history (last 3 rounds)
...

# Skills attached: ...
```

---

## [4] 局部验证 Verifier（design v4 新增）

**文件**: `backend/app/ocr_optimizer/service/module_optimizer.py:verify_module_fix`
**调用点**: 每个 module_optimizer 产出的修改方案都过一次

### System instruction
```
你是一个 OCR prompt 修改的审查员。给定一个字段当前 prompt 提取失败
的样本，以及一个 LLM 提议的新 prompt，请判断这个新 prompt 是否真的
能解决这些样本上的失败。仅当你确信新 prompt 显著改进了取值准确度
时，返回 verdict='accept'；否则返回 'reject'。务必返回纯 JSON，
键为 verdict 和 reasoning。
```

### User prompt skeleton
```
# Field
- module_key / display_name

# Current ocr_prompt (failed)
<old prompt>

# Proposed new ocr_prompt
<new prompt>

# Failing samples (OCR slice vs ground truth)
<最多 3 个失败样本的 JSON>

请判断新 prompt 是否会让这些样本提取正确。
```

**reject 时**：丢弃 module_optimizer 提议，模块的 ocr_prompt 保持上一轮原样。

---

## [5] Meta Optimizer（**客户迭代路径禁用**）

**文件**: `backend/app/ocr_optimizer/service/meta_optimizer.py:run_meta_optimization`
**当前状态**: customer_iteration `_run_three_rounds` 调用时传 `enable_meta=False` → **完全跳过**。

如果未来某条路径 enable_meta=True：
```
You are the meta-optimizer for a modular OCR prompt system. You see all
modules' current state and decide structural changes (add / remove /
rename / reorder). Return ONLY a single JSON object with keys:
add_modules, remove_module_keys, rename, rationale.
Empty lists mean no change.
```

**守护**: `run_orchestrator._run_one_round` 即便 enable_meta=True 也有两道保护：
1. accuracy ≥ 0.5 的模块不能删
2. 删除后投影模块数 < max(3, 半数) → 该轮所有 remove 失效

---

## [6] Composer（纯代码，无 LLM）

**文件**:
- `backend/app/ocr_optimizer/service/composer.py` — 装配 + 四段 GLOBAL_* 常量
- `backend/app/ocr_optimizer/assets/global_output_contract.yaml` — Part 3 平台资产
- `backend/app/ocr_optimizer/service/output_contract.py` — 资产 loader（lru_cache 启动期加载）

**调用点**: 每轮末尾 + fork 创建 v1 时 + preset_init 也会附加 Part 3 到 v1 prompt
**签名（design v7）**:
```python
def assemble_prompt(modules: Iterable, *, country_global: str | None) -> str
```
`country_global` 是 **keyword-only 必传**。来源：`OcrPromptVersion.country_global_text` 列。
fork / round 通过 `new_version.country_global_text = src_version.country_global_text` 继承。
**Part 3（GLOBAL_OUTPUT_CONTRACT_DETAILS）** 不是参数：composer 在每次 assemble 时从平台资产重新注入，因此 Part 3 永远是平台最新版，不可被任何调用方覆盖。

把所有模块组装成完整 composed_prompt + composed_schema。结构（design v7）：

```
GLOBAL_PREAMBLE
"你是一名严谨的文档信息抽取专家。请阅读这张文档（图片或 PDF），并严格按下方指定的 JSON Schema 输出一份合法的 JSON。
# 通用约束
1. 仅输出 JSON，不要任何 markdown、解释或多余文字。
2. 字段缺失时输出 null，不要捏造。
3. 日期统一格式为 YYYY-MM-DD；数字去掉千分位与货币符号。"

country_global_text  ← Part 1 国家事实 + Part 2 字段识别要点（template_loader 在 # Part 3 处截断）
<v.country_global_text>     例如 MY 的「# Part 1 国家全局说明 / # Part 2 字段规则」

GLOBAL_SCHEMA_REFERENCE
"# 整体输出 Schema
返回的 JSON 必须符合下列 Schema：
```json
<composed_schema>
```"

GLOBAL_OUTPUT_CONTRACT_DETAILS  ← design v7: Part 3 平台输出契约（从 global_output_contract.yaml 加载）
"# Part 3 · 输出契约与装配规则（平台统一）
## 3.1 顶层结构（ARRAY of anyOf invoice/receipt/other）
## 3.2 数值字段统一规范（去千分位/去货币符号/保留精度）
## 3.3 税额装配（detailOfTaxSummary 数组 + 总税额一致性 + ADJUSTMENT 平账）
## 3.4 行项目装配（qty×price 校验 < 1% + PO/SO/DO 头/行二选一）
## 3.5 跨页装配（合并为单元素 + page 数组）
## 3.6 Credit Note 装配（originalInvoiceReferences 必填）
## 3.7 缺失字段处理（找不到不输出）
## 3.8 字段输出顺序（预留）"

PER-MODULE BODIES (不再含 global_rules — design v6 已下放)
"# 模块识别指令
## 1. {display_name_1}
{module_1.ocr_prompt}

## 2. {display_name_2}
{module_2.ocr_prompt}
..."

GLOBAL_SELF_CHECK
"# 输出前自检
1. JSON 合法、可被 json.loads 解析。
2. 每个识别模块的字段都在最终 JSON 中存在（没有的填 null）。
3. 没有任何字段是 markdown 或自然语言描述。"
```

---

## 已知 Bug 与修复（编号对应这次反思）

| 编号 | 描述 | 修复 commit |
|---|---|---|
| Meta 把所有字段删光 → schema 变 `{"properties": {}}` | 老 meta_optimizer 太激进 | `588e869` 给 meta 加守护（≥0.5 的不能删 + 投影模块数 < 半数则撤回全部 remove） |
| 守护没生效，新 fork 起步只剩 3 个模块 | customer_iteration 没传 `enable_meta=False` | `588e869` 已修；后续我加了源版本智能选择（取模块最多的） |
| c1 的 active 被旧 bug 毁了 → 后续 fork 级联失血 | 历史 active = 1 个模块 | 一次性把 c1 active 回滚到 v1（32 模块） |
| 新字段在不同样本里值不应一样 | drafts 全局共享 | 待修：drafts 改为 per-doc 嵌套 |
| 字段编辑标注切换文档丢失 | 同上 | 同上 |
