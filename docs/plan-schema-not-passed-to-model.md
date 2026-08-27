# 设计与开发计划 · 生产链路未向模型传递 `response_schema`

> 状态：**待执行**（已确认缺陷，暂不动手）
> 类型：平台级架构修复
> 发现于：2026-08-27，template 7 prompt 迭代过程中

---

## 0. 缺陷

`extract_service._run_processor` 在 `prompt` 存在时，**只把 prompt 当 instruction 传给
processor，不传 `composed_schema`**：

```python
# backend/app/services/extract_service.py:243-255
if prompt:
    instruction = prompt          # ← schema 被完全忽略
elif schema:
    instruction = f"...Schema:\n{json.dumps(schema)}"   # 只有无 prompt 时才用
...
raw_text = processor.process_document(storage_path, instruction)
#                                     ↑ 没有第三个参数 runtime_config
```

而 `GeminiProcessor.process_document(file_path, instruction, runtime_config)` 的
`response_schema` 正是从 `runtime_config` 里取的。

**后果：所有国家模板的 `json_schema` 里每个字段的 `description`，在生产环境从未
到达过模型。** 它们只在下列场景生效：

| 场景 | schema 是否到达模型 |
|---|---|
| 生产提取（`/extract`、开放平台同步/异步） | ❌ **否** |
| 迭代评测 harness（`ocr_runner`） | ✅ 是（传了 response_schema） |
| `prompts/tpl7/evaluate.py` | ✅ 是 |

---

## 1. 这个缺陷造成过什么

### 1.1 直接后果：schema description 写了等于没写

国家模板的设计里，Part 2「字段识别规则」明确写着：

> 下方 JSON Schema 中每个字段的 `description` 已经包含了该字段的业务含义、
> MY 票面常见关键词与典型格式、取值位置锚点……**请严格按 schema 中每字段的
> description 提取。**

这段承诺在生产环境是空头支票。MY 模板里 `invoiceNumber` / `billFromBusinessRegistrationNumber`
等字段的详细取值规则都写在 description 里，模型一个字都看不到。

### 1.2 间接后果：评测结论系统性高估

2026-08 的 template 7 五轮迭代中，v2–v6 的离线评测全部传了 schema，
测出 8/8；同一份 prompt 上线后只有 6–7/8。**离线与线上的差距，一部分就来自
这个通道差异。**

（另一部分来自模型抖动——同一配置 4 轮 8/8 之后再跑 2 轮变成 0/2，
说明 seed 固定并不等于确定性，这是另一个独立问题。）

### 1.3 已被绕过的部分

tpl7 迭代最终把规则**全部写进 prompt 正文**（`MY_invoice_prompt.yaml` §1.0.5 /
§1.1 / §1.2 / §1.3 / §1.34），线上验证 7/7 × 3 轮。**这是绕过，不是修复**——
其他国家模板（JP 等）仍然把规则放在 schema description 里。

---

## 2. 为什么现在不动

1. **影响面是全平台**：改 `_run_processor` 会改变**所有** API 的模型调用输入，
   包括所有国家、所有客户、以及迭代优化回路。
2. **可能改变既有行为**：Gemini 收到 `response_schema` 后会强制结构约束，
   输出形状可能与现在不同（如缺失字段的表现、数组根 vs 对象根）。
   qwen 不支持 response_schema，行为又是另一套。
3. **必须先有回归基线**：按 CLAUDE.md 自检 5，改机器要先在黄金集上
   A/B 验证「严格分不下降」再合 PR。

---

## 3. 修复方案（待执行）

### 3.1 主改动

```python
# extract_service._run_processor
runtime_config = {"response_schema": schema} if schema else None
raw_text = processor.process_document(storage_path, instruction, runtime_config)
```

要点：

- **保留 prompt 作为 instruction**，schema 走 runtime_config —— 两者互补而非二选一。
- **qwen 侧要确认行为**：`QwenProcessor.process_document` 的第三参数当前如何处理？
  DashScope 不支持 response_schema，需要确认多传一个参数不会报错（或在
  processor 内忽略）。
- **加开关** `PASS_SCHEMA_TO_MODEL`（默认 False，灰度期打开），
  确保可一键回退。

### 3.2 验证清单

| 项 | 方法 |
|---|---|
| 全量单测 | `pytest -q`，不得新增失败 |
| 黄金集 A/B | `python -m app.ocr_optimizer.eval.run_golden_batch --country MY`，严格分不下降 |
| JP 模板回归 | JP 的规则重度依赖 schema description，**开关打开后应当变好**；若变差说明 schema 与 prompt 冲突 |
| tpl7 回归 | 25 页 claim + 6 页多票据样本，各 3 轮 |
| qwen 链路 | 阿里云 templateId=7 单独验证，确认不报错、不劣化 |
| 输出形状 | 确认 `composed_schema` 根形状（数组根 `$[*].x`）与实际输出一致，勿触发历史上的「假 0 分」bug |

### 3.3 修复后的收尾

- 把 tpl7 迭代中「为绕过而搬进 prompt 正文」的规则**重新评估**：
  §1.34（nameOfInvoice / unitPrice）本就属于字段级规则，schema 通了之后
  可以考虑搬回 description，让 Part 2 的设计承诺重新成立。
- 更新 `docs/prompt-system.md` 与国家模板 Part 2 的说明，写清
  「schema description 何时生效」。

---

## 4. 关联

- `prompts/tpl7/CHANGELOG.md` —— 缺陷的发现过程与证据
- CLAUDE.md §3.3「Prompt 四段平台契约」—— schema 段的定位
- CLAUDE.md 自检 5「改机器先在黄金集 A/B」
