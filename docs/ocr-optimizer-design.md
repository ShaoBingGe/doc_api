# OCR Prompt 自动优化系统 — 架构设计文档

> 子系统名称：`ocr_optimizer`
> 替换对象：现有 `backend/app/optimizers/`（单体 prompt 优化器，整体废弃）
> 集成宿主：ApiAnything（现有 `Document` / `Annotation` / `ApiDefinition` / `Processor` 体系不变）

---

## 1. 设计哲学

四条原则贯穿全文，所有具体设计都从这里推导：

**① 模块化分而治之**
不对单体 prompt 做全量迭代。把一份 OCR Prompt 拆解成多个语义模块（如「店名识别」「日期识别」「商品明细识别」），每个模块独立反思、独立优化，最终拼接成完整 prompt 喂给 OCR 模型。

**② 全量 OCR、模块切片对齐**
即便优化粒度是模块，每一轮 OCR **必须用拼接好的完整 prompt 跑一次完整调用**（因为模块间会互相影响识别）。OCR 输出是一份完整 JSON；每个模块按预先声明的 `json_path` 从中切出属于自己的那部分，再与 Ground Truth 对齐做 diff。

**③ 双层 Optimizer**
- **Module Optimizer**：每个模块独立调一次 LLM，看自己的 diff，更新自己的 description / suggestions / prompt
- **Meta Optimizer**：全局只调一次 LLM，看所有模块的 diff、GT 中的孤儿字段（无人认领）、空输出模块，决定**增 / 删 / 拆 / 合**模块

**④ 完整迭代轨迹**
每一轮（Round）× 每一个模块（Module）的 OCR 输出、GT、diff、suggestion 全部在数据库留痕，永不覆盖。LLM 调用时只取最近 K 轮（默认 3），但用户/审计能完整回溯。

---

## 2. 子系统定位

`ocr_optimizer` 是 ApiAnything 内部的一个**离线优化子系统**，负责把"用户在标注平台产出的干净 Ground Truth"反向回路成"高质量、可泛化的 OCR Prompt"。

**输入**：
- 一个 `ApiDefinition`（含 `response_schema` 或预置模板）
- 至少 3 张已标注 ground truth 的 `Document`（Annotation 表中 `source=manual` 或 `is_corrected=True` 的字段集合）
- 一个起始 prompt 模板（可来自 [`initial_extraction.py`](../backend/app/services/initial_extraction.py) 的通用层次化 prompt，或用户上传的自定义模板）

**输出**：
- 一个新的 `OcrPromptVersion`（active 状态）
- 它的 `composed_prompt` 字段就是最终拼接好的 prompt 字符串，[`extract_service.py:97`](../backend/app/services/extract_service.py) 在生产调用时直接取这个字符串

**不做**：
- 自动触发（必须手动 `POST /api-definitions/{id}/ocr-optimize`）
- 实时优化（不是 SSE 流，是后台任务）
- 在线 A/B（一个 ApiDefinition 同一时间只有一个 active 版本）

---

## 3. 与现有系统的集成关系

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  现有 ApiAnything                                                            │
│                                                                              │
│  ApiDefinition ──┬── response_schema (JSON)                                  │
│                  ├── config.sample_document_ids: list[uuid]  ◄── 改造        │
│                  └── prompt_version_id  ◄── 改指向 OcrPromptVersion          │
│                                                                              │
│  Document ──── Annotation (is_corrected | source=manual = GT 数据源)         │
│                                                                              │
│  Processor (Gemini / OpenAI) ◄── 不动                                        │
│  extract_service.get_active_prompt() ◄── 改成读 OcrPromptVersion.composed   │
└─────────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ 写入 active 版本
                                  │
┌─────────────────────────────────────────────────────────────────────────────┐
│  ocr_optimizer 子系统 (新)                                                   │
│                                                                              │
│   POST /api-definitions/{id}/ocr-optimize                                    │
│            │                                                                 │
│            ▼                                                                 │
│   OcrOptimizationRun (一次手动触发 = 一次 Run)                                │
│            │                                                                 │
│            ▼                                                                 │
│   ┌──── Round 1 ──── Round 2 ──── ... ──── Round 5 ─────┐                   │
│   │  每轮:                                               │                    │
│   │    1. 用当前 OcrPromptVersion.composed_prompt 跑全量 OCR (N 张样本)      │
│   │    2. 按 module.json_path 切片 → 每模块拿到自己片段                       │
│   │    3. 每模块对比 GT → 写 OcrModuleIteration (diff, accuracy)              │
│   │    4. Per-module Optimizer (N 次 LLM 调用)                                │
│   │    5. Meta Optimizer (1 次 LLM 调用 — 增删模块)                           │
│   │    6. 应用变更，生成下一版本的 OcrPromptVersion (draft)                    │
│   └────────────────────────────────────────────────────┘                     │
│            │                                                                 │
│            ▼                                                                 │
│   最佳版本 activate → 写回 ApiDefinition.prompt_version_id                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**完全替换现有 `backend/app/optimizers/`**：删除 `model.py` / `service.py` / `router.py` / `__init__.py`，迁移 alembic 表（drop `prompt_versions` 旧表，新建本文档定义的所有表）。`models/__init__.py:14` 的 `from app.optimizers.model import PromptVersion` 改为新模型导入。

---

## 4. 项目结构

```
backend/app/ocr_optimizer/
├── __init__.py                  # 公共导出
├── models.py                    # 5 张 SQLAlchemy 表
├── schemas.py                   # Pydantic Request/Response
├── router.py                    # FastAPI 路由
├── service/
│   ├── __init__.py
│   ├── run_orchestrator.py      # Run / Round 调度，主入口 optimize()
│   ├── module_initializer.py    # 从 response_schema 自动拆分初始模块
│   ├── ocr_runner.py            # 一轮完整 OCR (多样本并行)
│   ├── slicer.py                # 按 json_path 切片 OCR 输出
│   ├── ground_truth.py          # 从 Annotation 表构造 GT JSON
│   ├── evaluator.py             # 模块/版本级 accuracy 计算
│   ├── module_optimizer.py      # Per-module LLM 调用
│   ├── meta_optimizer.py        # 全局 LLM 调用（增删模块）
│   ├── composer.py              # 模块 → composed_prompt + composed_schema
│   └── persistence.py           # SQL 写入 / 本地 JSON 镜像
├── storage/
│   ├── sql_backend.py           # 生产：SQLAlchemy
│   └── json_backend.py          # 开发：本地 JSON 文件
└── prompts/                     # LLM meta-prompt 模板
    ├── module_optimizer.txt
    └── meta_optimizer.txt

docs/ocr-optimizer-design.md     # 本文档
```

---

## 5. 数据模型（SQL — 生产权威）

6 张表（5 张核心 + 1 张 TODO 占位）。所有表使用 SQLAlchemy + Alembic migration。开发模式可以用 JSON 文件镜像（见 §11），但 SQL schema 是契约。

### 5.1 实体关系

```
ApiDefinition ──1:N── OcrPromptVersion
                          │
                          └──1:N── OcrModule ──N:M── OcrSkill (§18, TODO)

ApiDefinition ──1:N── OcrOptimizationRun  (status: running | paused_for_review | completed | failed | aborted)
                          │  ├── starting_version_id  ──► OcrPromptVersion
                          │  ├── current_round_num    ──► 指针，paused_for_review 时记录"下次 advance 起点"
                          │  └── resulting_version_id ──► OcrPromptVersion（finalize 时填）
                          │
                          └──1:N── OcrOptimizationRound
                                       │
                                       └──1:N── OcrModuleIteration
                                                      │
                                                      └── module_id ──► OcrModule
```

### 5.2 表 1 — `ocr_prompt_versions`

一个 `ApiDefinition` 的「一版完整模块组合的快照」。每一轮 optimize 会生成一个新版本（draft 状态），只有 Run 结束选出的最佳版本才会被 activate。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `api_definition_id` | UUID | FK → `api_definitions.id` ON DELETE CASCADE, INDEX | 所属 API |
| `version` | INT | NOT NULL | 每个 API 内自增（1, 2, 3, ...） |
| `parent_version_id` | UUID | FK → self, NULLABLE | 演化链上的父版本 |
| `status` | VARCHAR(16) | NOT NULL, default `'draft'` | `draft` / `active` / `archived` |
| `composed_prompt` | TEXT | NOT NULL | 所有模块拼接后的最终 prompt 字符串，**生产 OCR 调用就读这个字段**。**例外**：`origin='init'` 且来源是预设国家 yaml（见 §6.4）时直接存 yaml 原文（不走 composer），从 v2 起恢复 composer 拼接 |
| `composed_schema` | JSON | NULLABLE | 所有模块的 schema_fragment 合并后的最终 JSON Schema |
| `overall_accuracy` | FLOAT | NULLABLE | 在 Run 评估样本上的 accuracy (0-1) |
| `origin` | VARCHAR(16) | NOT NULL, default `'init'` | `'init'` / `'round'` / `'manual_edit'`。`manual_edit` 表示用户在 paused_for_review 状态下编辑 suggestions/description 派生的版本（§7.4） |
| `produced_by_run_id` | UUID | FK → `ocr_optimization_runs.id`, NULLABLE | 哪次 Run 生成的（初始版本为 NULL；manual_edit 版本指向用户当前 review 的 Run） |
| `produced_in_round` | INT | NULLABLE | 哪一轮生成的（manual_edit 版本指向 patch 的源轮） |
| `created_by` | UUID | NULLABLE | 用户 ID |
| `created_at` | TIMESTAMP | server_default now() | |
| `activated_at` | TIMESTAMP | NULLABLE | 进入 `active` 状态的时间 |
| `notes` | TEXT | NULLABLE | 备注/标签 |

唯一约束：`(api_definition_id, version)` UNIQUE。
部分索引：`WHERE status = 'active'` 上的 `(api_definition_id)` UNIQUE（同一个 API 同时只能有一个 active 版本）。

### 5.3 表 2 — `ocr_modules`

一个 `OcrPromptVersion` 下的某个模块的快照。**每次版本递增都会复制所有模块**（即使某模块未变），保证版本快照的不可变性。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `prompt_version_id` | UUID | FK → `ocr_prompt_versions.id` ON DELETE CASCADE, INDEX | |
| `module_key` | VARCHAR(64) | NOT NULL | 稳定标识符，跨版本相同。如 `shop_name`、`line_items` |
| `display_name` | VARCHAR(128) | NOT NULL | 人类可读名，如 "店名识别" |
| `description` | TEXT | NOT NULL | 模块功能描述。例：「商店名称识别，提取小票所属商铺，可包含地理位置信息……」 |
| `json_path` | VARCHAR(256) | NOT NULL | 模块输出在最终 JSON 中的位置。JSONPath 语法：`$.shop_name`、`$.items[*]`、`$.tax_summary` |
| `schema_fragment` | JSON | NOT NULL | 该模块负责的 JSON Schema 子树 |
| `ocr_suggestions` | JSON | NOT NULL, default `{}` | 字典形式特征提示。结构固定：`{"semantics": "...", "position": "...", "most_common_feature": "...", "extra_features": ["..."]}` |
| `ocr_prompt` | TEXT | NOT NULL | 该模块的 prompt 片段，会被 composer 拼进 composed_prompt |
| `skill_ids` | JSON | NOT NULL, default `[]` | 该模块引用的 `OcrSkill.id` 列表（§18，**TODO 占位字段**）。当前未消费，composer 不读取；optimizer 不允许修改 |
| `order_index` | INT | NOT NULL, default 0 | 拼接顺序 |
| `status` | VARCHAR(16) | NOT NULL, default `'active'` | `active` / `frozen`（未来扩展） |
| `module_accuracy` | FLOAT | NULLABLE | 最近一次评估的 accuracy |
| `created_at` | TIMESTAMP | server_default now() | |

唯一约束：`(prompt_version_id, module_key)` UNIQUE。
索引：`(module_key)` 便于跨版本追踪同一模块的演化。

### 5.4 表 3 — `ocr_optimization_runs`

一次手动触发的 optimize 调用。一个 Run 包含多个 Round。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `api_definition_id` | UUID | FK → `api_definitions.id`, INDEX | |
| `starting_version_id` | UUID | FK → `ocr_prompt_versions.id` | 起点版本（通常是当前 active） |
| `resulting_version_id` | UUID | FK → `ocr_prompt_versions.id`, NULLABLE | 用户在 finalize 时手动选定的激活版本（§7.6） |
| `status` | VARCHAR(16) | NOT NULL, default `'running'` | `running` / `paused_for_review` / `completed` / `failed` / `aborted`。**Run 不再自驱动多轮**：每跑完一轮立刻进入 `paused_for_review`，等待用户 advance 或 finalize |
| `max_rounds` | INT | NOT NULL, default 5 | 上限保护，达到时强制 finalize prompt |
| `target_accuracy` | FLOAT | NOT NULL, default 0.95 | 仅作前端提示阈值；不再用于自动停止 |
| `rounds_completed` | INT | NOT NULL, default 0 | |
| `current_round_num` | INT | NOT NULL, default 0 | 指针：paused 状态下"已完成的最大 round_num"。下次 advance 跑 `current_round_num + 1` |
| `sample_document_ids` | JSON | NOT NULL | `[uuid, uuid, ...]` 至少 3 张 |
| `llm_provider` | VARCHAR(32) | NOT NULL | 用哪个 LLM 做 optimizer（如 `gemini\|gemini-2.5-pro`） |
| `triggered_by` | UUID | NULLABLE | 用户 ID |
| `started_at` | TIMESTAMP | server_default now() | |
| `completed_at` | TIMESTAMP | NULLABLE | |
| `error_message` | TEXT | NULLABLE | |
| `metrics` | JSON | NULLABLE | `{"total_ocr_calls": N, "total_llm_calls": M, "tokens_used": K}` |

### 5.5 表 4 — `ocr_optimization_rounds`

Run 下的一轮。一轮 = 一次完整 OCR + 一次完整分析 + 一次模块更新。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `run_id` | UUID | FK → `ocr_optimization_runs.id` ON DELETE CASCADE, INDEX | |
| `round_num` | INT | NOT NULL | 1, 2, 3... |
| `prompt_version_id` | UUID | FK → `ocr_prompt_versions.id` | 本轮使用的（输入）版本 |
| `next_version_id` | UUID | FK → `ocr_prompt_versions.id`, NULLABLE | 本轮产出的（输出）版本 |
| `overall_accuracy` | FLOAT | NULLABLE | 本轮全部模块全部样本平均 accuracy |
| `per_sample_accuracy` | JSON | NULLABLE | `{sample_doc_id: accuracy}` |
| `ocr_raw_outputs` | JSON | NULLABLE | `{sample_doc_id: full_ocr_json}` — 完整 OCR 输出留痕 |
| `meta_decision` | JSON | NULLABLE | `{"add_modules": [...], "remove_module_keys": [...], "rename": [...], "rationale": "..."}` |
| `phase` | VARCHAR(24) | NOT NULL, default `'ocr_running'` | `ocr_running` / `analyzing` / `optimizing` / `composing` / `completed` / `failed` |
| `duration_ms` | INT | NULLABLE | |
| `created_at` | TIMESTAMP | server_default now() | |
| `completed_at` | TIMESTAMP | NULLABLE | |

唯一约束：`(run_id, round_num)` UNIQUE。

### 5.6 表 5 — `ocr_module_iterations`

**核心学习轨迹表**。Round × Module 的笛卡尔积，每个组合一行。这是 Module Optimizer 调 LLM 时的输入来源。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `round_id` | UUID | FK → `ocr_optimization_rounds.id` ON DELETE CASCADE, INDEX | |
| `module_id` | UUID | FK → `ocr_modules.id` | 本轮使用的模块快照 |
| `module_key` | VARCHAR(64) | NOT NULL, INDEX | 反规范化，便于跨轮跨版本追踪 |
| `per_sample_results` | JSON | NOT NULL | 见下方结构 |
| `aggregate_accuracy` | FLOAT | NOT NULL | 跨样本平均 accuracy |
| `aggregate_diff` | JSON | NULLABLE | 跨样本聚合后的 diff（LLM 生成）：`{"differences_description": "...", "differences_reason_analysis": "..."}` |
| `optimization_suggestion` | TEXT | NULLABLE | Module Optimizer 输出。`accuracy == 1.0` 时为 NULL（跳过 LLM 调用） |
| `new_description` | TEXT | NULLABLE | Module Optimizer 给出的新 description（待应用） |
| `new_ocr_suggestions` | JSON | NULLABLE | 新 suggestions |
| `new_ocr_prompt` | TEXT | NULLABLE | 新 ocr_prompt |
| `skill_feedback` | TEXT | NULLABLE | Optimizer 关于 skills 的反馈（如"现有 skill 不能覆盖表格抽取场景，建议人工添加"）。**这是 optimizer 唯一允许写入的 skill 相关字段**；不允许写 `new_skill_ids` 或 `new_skills` 等任何修改字段（§9.1 & §15） |
| `llm_call_metadata` | JSON | NULLABLE | `{"tokens_in": N, "tokens_out": N, "model": "..."}` |
| `created_at` | TIMESTAMP | server_default now() | |

`per_sample_results` 结构（JSON）：
```json
[
  {
    "sample_doc_id": "uuid",
    "ocr_sliced": <按 json_path 从 OCR 输出切出的片段>,
    "ground_truth": <从 Annotation 表构造的该模块 GT 片段>,
    "matched": true | false,
    "field_accuracy": 0.0-1.0,
    "diff_detail": "字段值不同点说明（短文本）"
  }
]
```

唯一约束：`(round_id, module_key)` UNIQUE。

---

## 6. 模块初始拆分策略

### 6.1 自动拆分规则（默认）

输入：`ApiDefinition.response_schema`（顶层是 `type: object`）

规则：
1. 遍历 `response_schema.properties` 顶层 key
2. **数组类字段**：每个数组单独成一个模块（如 `items[]` → `line_items` 模块）
3. **对象类字段**：整个对象成一个模块（如 `buyer.{name,tax_id,...}` → `buyer` 模块）
4. **标量类字段**：按业务语义分组，由初始化器的内置规则合并（避免一字段一模块的碎片化）：
   - `*_number` / `*_code` / `*_id` 类 → 合并为 `identifiers` 模块
   - `*_date` / `*_time` / `date` / `time` → 合并为 `temporal` 模块
   - `subtotal` / `tax` / `total` / `*_amount` / `currency` → 合并为 `totals` 模块
   - 其他单字段保留为独立模块

### 6.2 示例：基于现有 `templates.py` 的拆分

**Receipt 模板** ([`template_service.py:208`](../backend/app/services/template_service.py))，原 schema 字段：`store_name, store_address, date, time, items[], subtotal, tax, total, payment_method, currency`

拆为 5 个模块：

| module_key | display_name | json_path | 包含字段 |
|------------|--------------|-----------|---------|
| `store_identity` | 店铺身份识别 | `$.store_name`, `$.store_address` | store_name, store_address |
| `temporal` | 交易时间识别 | `$.date`, `$.time` | date, time |
| `line_items` | 商品明细识别 | `$.items[*]` | items 数组 |
| `totals` | 金额汇总识别 | `$.subtotal`, `$.tax`, `$.total`, `$.currency` | 4 个金额字段 |
| `payment` | 支付方式识别 | `$.payment_method` | payment_method |

**中国增值税发票** ([`template_service.py:21`](../backend/app/services/template_service.py))，原 schema：`invoice_code, invoice_number, invoice_date, buyer{}, seller{}, items[], total_amount, total_tax, total_with_tax`

拆为 6 个模块：

| module_key | display_name | json_path |
|------------|--------------|-----------|
| `invoice_identifiers` | 发票编号识别 | `$.invoice_code`, `$.invoice_number` |
| `invoice_date` | 开票日期识别 | `$.invoice_date` |
| `buyer` | 购买方识别 | `$.buyer` |
| `seller` | 销售方识别 | `$.seller` |
| `line_items` | 货物明细识别 | `$.items[*]` |
| `totals` | 价税合计识别 | `$.total_amount`, `$.total_tax`, `$.total_with_tax` |

### 6.3 模块初始 prompt 与 description 生成

对每个新生成的模块，由 `module_initializer.py` 调用 LLM 生成首版 `description` 和 `ocr_prompt`，输入：
- 模块负责的 schema_fragment
- 模块的 display_name + json_path
- 整体业务上下文（API 的 description）

生成模板（伪代码）：
```
You are designing an OCR prompt module for a document type "{api_def.description}".
This module is responsible for extracting fields at JSON path "{json_path}".
Schema fragment:
{schema_fragment}

Output a JSON:
{
  "description": "<1-2 sentence functional description>",
  "ocr_suggestions": {
    "semantics": "<typical semantic patterns>",
    "position": "<where on document this usually appears>",
    "most_common_feature": "<visual/typographic signal>"
  },
  "ocr_prompt": "<the actual prompt fragment, in the same language as schema, that instructs the OCR model to extract this module's fields>"
}
```

LLM 失败时使用 fallback：description = `f"提取 {json_path} 的字段"`，ocr_prompt = `f"识别文档中的 {display_name}，输出为 {schema_fragment}"`。

### 6.4 从预设国家 yaml 拆解（New API 入口的初始化路径）

适用场景：用户在 `/workspace/new` 顶部「选国家」chip 选中一个有 yaml 的国家（如 MY）。此路径**与 §6.1 完全不同**：不读 `ApiDefinition.response_schema`，而是读仓库根目录的 `<COUNTRY>_invoice_prompt.yaml`。yaml 文件**不允许被代码修改**，是模板的唯一来源。

**入口端点**：`POST /api/v1/api-definitions/from-country-template` body `{country: "MY"}`（详见 §12）。

**核心步骤**（在一个事务内完成）：

```
init_from_country_template(country):
    1. 加载 <COUNTRY>_invoice_prompt.yaml（仓库根目录）
       - 校验 yaml 含 prompt_template.prompt_format 和 prompt_template.json_schema
       - 替换占位符：{tax_categories_text} → "请使用文档中出现的原名"
    2. 创建占位 ApiDefinition:
         status='pending_first_doc'
         name=f"{country}_invoice_{hex6}"
         api_code=f"{country.lower()}-invoice-{hex6}"
         response_schema=yaml.json_schema 原文
         config={"source_country": country, "preset_yaml": "<COUNTRY>_invoice_prompt.yaml"}
    3. 创建 OcrPromptVersion v1:
         status='active', origin='init', version='1'
         composed_prompt = yaml.prompt_format 原文（已替换占位符）  ← §5.2 例外
         composed_schema = yaml.json_schema 原文
    4. 拆解 30 个 OcrModule（顺序按 order_index）:
         order_index=0: 特殊 module global_rules（见下方）
         order_index=1..26: 26 个标量字段
         order_index=27..29: 3 个数组字段（line_items / tax_summary / original_invoice_references）
    5. ApiDefinition.prompt_version_id = v1.id
    6. 返回 ApiDefinition + Version + Modules
```

#### 6.4.1 拆解规则（仅取 yaml.json_schema.items.anyOf[0]，即 invoice/receipt 分支）

> yaml 的 `anyOf` 第二分支（other）只有 docType+page；不参与拆解。`doc_type` module 的 enum 保持 `[invoice, receipt]`，与 yaml 分支 1 一致。

**特殊 module — `global_rules`**：
```
module_key      : "global_rules"
display_name    : "全局规则与约束"
json_path       : "$"
schema_fragment : {}  ← 空字典，不贡献 schema
order_index     : 0
description     : "整张文档级别的提取规则集合：日期、数字格式、税种简称、跨页合并等"
ocr_suggestions : { semantics: "全局规则不针对单个字段", position: "适用于所有字段", most_common_feature: "—", extra_features: [] }
ocr_prompt      : <yaml prompt_format 中"提取规则"/"日期处理规则"/"必填字段"/"缺失信息"/"税种简称映射"/"必须全面提取"/"加项减项税种"等全部全局段落原文>
```

> **保护标记**（与 LLM 优化器隔离）：global_rules module 在 module_optimizer 输入中 **不参与** per-field diff 计算（accuracy 永远 N/A）；meta_optimizer 的 prompt 中明确告知"`module_key='global_rules'` 不可删、不可改名、不可作为孤儿字段所属"。代码层强制保护见 §15。

**26 个标量 module**（每个 module 一份字段；命名为 yaml field 的 snake_case）：

| order | module_key | display_name | json_path | enum/notes |
|---|---|---|---|---|
| 1 | doc_type | 票据大类识别 | `$[*].docType` | enum: [invoice, receipt] |
| 2 | invoice_type | 发票子类型识别 | `$[*].invoiceType` | enum: [Commercial Invoice, Proforma Invoice, Credit Note, Tax Invoice] |
| 3 | name_of_invoice | 票面标题识别 | `$[*].nameOfInvoice` | |
| 4 | invoice_number | 发票号码识别 | `$[*].invoiceNumber` | 含 MY 规则 |
| 5 | invoice_code | 发票代码/序列号识别 | `$[*].invoiceCode` | |
| 6 | invoice_date | 发票日期识别 | `$[*].invoiceDate` | |
| 7 | due_date | 付款截止日期识别 | `$[*].dueDate` | |
| 8 | purchase_order_number | 采购订单号识别 | `$[*].purchaseOrderNumber` | 含 MY 规则 |
| 9 | sales_order_number | 销售订单号识别 | `$[*].salesOrderNumber` | |
| 10 | delivery_order_number | 发货单号识别 | `$[*].deliveryOrderNumber` | 含 MY 规则 |
| 11 | currency | 币种识别 | `$[*].currency` | ISO 4217 |
| 12 | total_net_amount | 不含税总净额识别 | `$[*].totalNetAmount` | |
| 13 | total_amount | 含税总金额识别 | `$[*].totalAmount` | |
| 14 | total_tax_amount | 总税额识别 | `$[*].totalTaxAmount` | |
| 15 | bill_to_name | 收票方名称识别 | `$[*].billToName` | |
| 16 | bill_to_composite | 收票方完整地址识别 | `$[*].billToComposite` | |
| 17 | bill_to_country | 收票方国家识别 | `$[*].billToCountry` | |
| 18 | bill_to_country_code | 收票方国家代码识别 | `$[*].billToCountryCode` | ISO 3166-1 alpha-2 |
| 19 | bill_to_tax_id | 收票方税号识别 | `$[*].billToTaxIdentificationNumber` | 含 MY 规则 |
| 20 | bill_from_name | 开票方名称识别 | `$[*].billFromName` | |
| 21 | bill_from_composite | 开票方完整地址识别 | `$[*].billFromComposite` | |
| 22 | bill_from_country | 开票方国家识别 | `$[*].billFromCountry` | |
| 23 | bill_from_country_code | 开票方国家代码识别 | `$[*].billFromCountryCode` | |
| 24 | bill_from_tax_id | 开票方税号识别 | `$[*].billFromTaxIdentificationNumber` | |
| 25 | bill_from_business_reg_no | 开票方商业登记号识别 | `$[*].billFromBusinessRegistrationNumber` | 含 MY 规则 |
| 26 | page | 页码识别 | `$[*].page` | array of NUMBER |

每个标量 module 的 `ocr_prompt` 模板：

```
你负责从文档中识别「{display_name}」字段。

输出位置（json_path）：{json_path}
该字段类型：{schema_fragment.type}{enum 时附 "（枚举：{enum 列表}）"}

# 识别规则
{yaml 该字段的 description 原文}

# 输出要求
找不到时输出 null。{type 为 NUMBER 时附加 "金额一律输出纯数字，遵循 global_rules 中的千分位与小数点规则。"}
```

`schema_fragment` 直接取 yaml 该字段定义的 dict（type 大小写**保留原样**——大写是 Gemini SDK 方言；composer 在 §10 的 `assemble_schema` 中**不再校验** type 字段大小写，仅做合并）。

**3 个数组 module**：

| order | module_key | display_name | json_path | schema_fragment 来源 |
|---|---|---|---|---|
| 27 | line_items | 商品/服务明细识别 | `$[*].detailOfGoodsOrServices[*]` | yaml 该数组的 items schema |
| 28 | tax_summary | 税金汇总识别 | `$[*].detailOfTaxSummary[*]` | 同上 |
| 29 | original_invoice_references | 原始发票引用识别（Credit Note 专用） | `$[*].originalInvoiceReferences[*]` | 同上 |

数组 module 的 `ocr_prompt` 在标量模板基础上额外加一段"`# 输出形式：JSON 数组，每行一个对象，含字段 [列名列表]`"。

#### 6.4.2 与 §6.1 自动拆分的关系

- §6.1（response_schema 自动拆分 + 标量分组）：用于**用户自定义 schema** 的 API 初始化路径，保留不变
- §6.4（country yaml 拆分 + 每字段独立）：用于**预设国家模板**入口，每字段一 module，**禁用** §6.1 的 identifiers/temporal/totals 分组

两条路径在 `init_version` 之外的所有后续行为（advance、manual_patch、finalize、composer-from-v2、optimizer）完全相同。

---

## 7. 优化流程（核心算法）

### 7.1 主流程（人工逐轮触发）

**核心变更**：Run 不再自驱动循环到完成；每跑完一轮立刻挂起 (`paused_for_review`)，等用户在前端 review 后显式 advance 或 finalize。这一节描述启动 Run + 跑第一轮 + 挂起。

```
start_optimization(api_definition_id):
    1. 加载 ApiDefinition
       - 校验 config.sample_document_ids 至少 3 张
       - 加载每张 sample 的 GT (从 Annotation 表组装)
    2. 加载当前 active OcrPromptVersion (或首次时初始化模块)
    3. 创建 OcrOptimizationRun(
         status='running', starting_version_id=current.id,
         current_round_num=0, max_rounds=5
       )
    4. run_one_round(run, round_num=1)
    5. Run.status='paused_for_review', Run.current_round_num=1
    6. 返回 Run + 第 1 轮所有数据给前端
```

后续推进由 §7.5 `advance_round` 控制；用户 finalize 由 §7.6 控制。**Run 永远不会在后端自动跑超过 1 轮**。`max_rounds` 仅作硬上限：当 `current_round_num >= max_rounds` 时禁止 advance，强制用户 finalize。

### 7.2 单轮流程 `run_one_round`

```
run_one_round(run, round_num):
    current_version = (round_num == 1) ? run.starting_version : prev_round.next_version
    round = OcrOptimizationRound(run_id, round_num, prompt_version_id=current_version.id,
                                  phase='ocr_running')

    # ── Step 1: 完整 OCR（所有样本并行）────────────────────────
    ocr_outputs = {}
    for sample_id in run.sample_document_ids:
        full_json = processor.process_document(
            file_path=sample.storage_path,
            instruction=current_version.composed_prompt,
            runtime_config={'response_schema': current_version.composed_schema}
        )
        ocr_outputs[sample_id] = json.loads(full_json)
    round.ocr_raw_outputs = ocr_outputs
    round.phase = 'analyzing'

    # ── Step 2: 切片 + 评估 ──────────────────────────────────
    iterations = []
    for module in current_version.modules:
        per_sample = []
        for sample_id, full_output in ocr_outputs.items():
            sliced = slicer.extract(full_output, module.json_path)
            gt = ground_truth.extract(sample_id, module.json_path)
            matched, field_acc, diff_detail = evaluator.compare(sliced, gt, module.schema_fragment)
            per_sample.append({
                'sample_doc_id': sample_id, 'ocr_sliced': sliced,
                'ground_truth': gt, 'matched': matched,
                'field_accuracy': field_acc, 'diff_detail': diff_detail
            })
        iter_record = OcrModuleIteration(
            round_id=round.id, module_id=module.id, module_key=module.module_key,
            per_sample_results=per_sample,
            aggregate_accuracy=mean(p['field_accuracy'] for p in per_sample)
        )
        iterations.append(iter_record)
    round.overall_accuracy = mean(i.aggregate_accuracy for i in iterations)
    round.phase = 'optimizing'

    # ── Step 3: Per-module Optimizer (并行)───────────────────
    for iter_record in iterations:
        if iter_record.aggregate_accuracy >= 1.0:
            continue   # 跳过 LLM 调用，但仍保留记录
        history = load_recent_iterations(module_key=iter_record.module_key, k=3)
        result = module_optimizer.run(
            module=module, current_iter=iter_record, history=history
        )
        # LLM 输出：aggregate_diff, optimization_suggestion, new_description,
        #          new_ocr_suggestions, new_ocr_prompt
        iter_record.aggregate_diff = result.diff
        iter_record.optimization_suggestion = result.suggestion
        iter_record.new_description = result.new_description
        iter_record.new_ocr_suggestions = result.new_ocr_suggestions
        iter_record.new_ocr_prompt = result.new_ocr_prompt

    # ── Step 4: Meta Optimizer (1 次 LLM)─────────────────────
    meta_input = {
        'modules': [{key, json_path, accuracy, diff_summary} for each iteration],
        'unclaimed_gt_fields': find_unclaimed_gt_paths(ocr_outputs, current_version.modules),
        'empty_modules': [m for m in modules if m.output is empty across all samples]
    }
    meta_decision = meta_optimizer.run(meta_input)
    # 输出：{add_modules: [{module_key, display_name, json_path, schema_fragment, ...}],
    #       remove_module_keys: [...], rename: [{old, new}], rationale}
    round.meta_decision = meta_decision

    # ── Step 5: Compose 下一版本 ──────────────────────────────
    new_modules = apply_changes(current_version.modules, iterations, meta_decision)
    next_version = OcrPromptVersion(
        api_definition_id=run.api_definition_id,
        version=current_version.version + 1,
        parent_version_id=current_version.id,
        status='draft',
        produced_by_run_id=run.id, produced_in_round=round_num,
        composed_prompt=composer.assemble_prompt(new_modules),
        composed_schema=composer.assemble_schema(new_modules),
    )
    save(next_version, new_modules)
    round.next_version_id = next_version.id
    round.phase = 'completed'
    return round
```

### 7.3 顺序保证：description → suggestions → prompt

按你的原始描述，per-module 更新顺序：先改 suggestions，再改 description，最后才改 ocr_prompt（让 prompt 是 description + suggestions 的产物，而非凭空生成）。

Module Optimizer 的 LLM 调用分两段（同一次会话）：
1. 输入：current module + iteration diff + history → 输出 `{aggregate_diff, optimization_suggestion, new_ocr_suggestions, new_description, skill_feedback}`
2. 输入：1 的结果 + schema_fragment → 输出 `new_ocr_prompt`

这样保证 prompt 永远是从 suggestions + description 派生，不会脱节。

**强制：**
- `new_skill_ids` / `new_skills` 等任何 skill 修改字段**不在 LLM 输出 Pydantic schema 中**（§9.1）
- apply 阶段硬编码 `new_module.skill_ids = current_module.skill_ids.copy()`
- LLM 仅能通过 `skill_feedback` 字符串字段表达对 skill 的意见

### 7.4 Manual Patch — 人工编辑生成派生版本

当 Run 处于 `paused_for_review` 时，用户在前端可编辑任意模块的 `ocr_suggestions` 和 `description`，然后点「保存 patch」。此时：

```
manual_patch(api_def_id, source_version_id, edits=[
    { module_key, description?, ocr_suggestions? }, ...
]):
    1. 校验 source_version_id 属于本 API，且对应 Run 处于 paused_for_review
    2. 加载 source_version 及其所有 modules
    3. 复制成新版本 v_new:
         - version = source.version + 0.1 的 decimal 取下个整数后缀 (见说明)
         - status='draft', origin='manual_edit'
         - parent_version_id = source.id
         - produced_by_run_id = 当前 Run.id
         - produced_in_round = Run.current_round_num
    4. 复制每个 OcrModule → 新模块快照:
         - 用户提供的 edits 覆盖对应字段（description / ocr_suggestions）
         - skill_ids 强制 = source_module.skill_ids.copy()（人工 patch 也不改 skills，需走 §18 专用入口）
         - ocr_prompt 保持不变（patch 阶段不重生成；将在下一轮 OCR 跑时由 composer 拼接，或在用户下次"再 patch"时手动更新）
    5. composer.assemble_prompt / assemble_schema → 写入 v_new
    6. 返回 v_new（status='draft'）
```

**版本号方案**（避免与 round 产物冲突）：
- Round 产物 version 为整数：1, 2, 3...
- manual_edit 产物 version 为浮点：`f"{parent.version}.{次序}"`（如 v2 → v2.1 → v2.2）
- DB 字段 `version` 改为 `String(16)` 或 `Numeric`，UI 直接显示字符串

> **注意**：manual_edit 版本只是 draft 候选，**不会自动 activate**。它仅作为下一轮 advance 的"起点版本"（§7.5）或 finalize 的"候选项之一"（§7.6）。

### 7.5 Advance Round — 推进到下一轮

```
advance_round(run_id, use_version_id?):
    1. 校验 Run.status == 'paused_for_review'
    2. 校验 Run.current_round_num < Run.max_rounds（否则报错"已达上限，请 finalize"）
    3. starting_version = use_version_id ? load(use_version_id) :
                          load(latest_round.next_version_id)
       - use_version_id 通常是 §7.4 manual_edit 产物 v_n.1
       - 不传则用最近一轮的输出版本 v_(n+1)
    4. Run.status='running'
    5. run_one_round(run, round_num=Run.current_round_num + 1, starting_version=starting_version)
    6. Run.status='paused_for_review', Run.current_round_num += 1
    7. 返回新一轮数据
```

### 7.6 Finalize — 结束 Run 并激活版本

```
finalize_run(run_id, version_id):
    1. 校验 Run.status == 'paused_for_review' 或 'running'（running 时拒绝，需等当前轮跑完）
    2. 校验 version_id 属于本 Run 的演化链:
         - 任意 Round.next_version_id, 或
         - 任意 origin='manual_edit' 且 produced_by_run_id == run_id 的版本
    3. 现有 active 版本 → status='archived'
    4. target_version → status='active', activated_at=now()
    5. ApiDefinition.prompt_version_id = target_version.id（兼容老字段）
    6. Run.status='completed', Run.resulting_version_id=version_id, Run.completed_at=now()
```

用户可主动 abort（不选任何版本激活）：

```
abort_run(run_id):
    Run.status='aborted'，不修改任何 ApiDefinition / OcrPromptVersion 状态
```

---

## 8. 评估算法 (evaluator)

复用现有 [`backend/app/optimizers/service.py:412`](../backend/app/optimizers/service.py) `_values_match` 的容差规则（迁移到 `ocr_optimizer/service/evaluator.py`）：

- 字符串：`strip()` + 大小写不敏感
- 数字：去千分位、去货币符号，绝对差 < 0.01 视为相等
- 日期：归一化为 `YYYY-MM-DD` 再字符串相等
- 数组：按业务 key 排序后逐元素比较；嵌套对象递归

模块级 accuracy 计算：
- 标量字段：matched ? 1.0 : 0.0
- 对象字段：子字段 accuracy 平均
- 数组字段：按最长公共子序列对齐 → 对齐元素 accuracy 平均，未对齐元素计 0

整体 accuracy = 所有模块所有样本的字段级 accuracy 平均。

---

## 9. LLM Agents

### 9.1 Module Optimizer

**职责**：看自己模块的 diff + 最近 K 轮历史 + GT，产出新的 description / suggestions / prompt。

**Prompt 模板** (`prompts/module_optimizer.txt`)：
```
你是一名 OCR Prompt 优化专家。你正在优化一个名为「{display_name}」的 OCR 识别模块。

# 模块当前状态
- module_key: {module_key}
- json_path: {json_path}
- schema_fragment: {schema_fragment_json}
- 当前 description: {current_description}
- 当前 OCR suggestions: {current_suggestions_json}
- 当前 OCR prompt: {current_ocr_prompt}

# 本轮 OCR 结果 vs Ground Truth (跨 {n} 个样本)
{per_sample_comparison_table}

# 最近 {k} 轮的迭代历史（最新在最后）
{history_summary}

# 当前 Skills（READ-ONLY，禁止修改）
以下是本模块已挂载的 skills。**你不能在输出中包含任何修改 skill 的字段；若 skill 不足以解决问题，仅在 skill_feedback 字段以自然语言描述。**
{skills_summary}

# 你的任务
1. 分析为什么 OCR 输出与 GT 不一致（写到 aggregate_diff.differences_description 和 differences_reason_analysis）
2. 给出 optimization_suggestion（一段话，说明应该如何改进识别策略）
3. 产出 new_ocr_suggestions（在原 suggestions 基础上增/改条目，结构保持 {semantics, position, most_common_feature, extra_features}）
4. 产出 new_description（融合新发现的特征）
5. 最后才产出 new_ocr_prompt（从 new_description + new_ocr_suggestions 派生，必须能输出符合 schema_fragment 的 JSON）
6. **（可选）**填写 skill_feedback：若现有 skills 不足或不合适，说明你认为缺什么。**不要试图添加/删除/修改 skill，否则会被丢弃**。

仅返回严格 JSON，结构（**不允许多出任何字段**）：
{
  "aggregate_diff": {
    "differences_description": "...",
    "differences_reason_analysis": "..."
  },
  "optimization_suggestion": "...",
  "new_ocr_suggestions": {...},
  "new_description": "...",
  "new_ocr_prompt": "...",
  "skill_feedback": "..."   // 可空字符串
}
```

**Pydantic schema 强制（`module_optimizer.py`）**：

```python
class ModuleOptimizerOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')   # 多出字段直接 ValidationError
    aggregate_diff: AggregateDiff
    optimization_suggestion: str
    new_ocr_suggestions: dict
    new_description: str
    new_ocr_prompt: str
    skill_feedback: str = ""
    # 注意：故意不包含 new_skill_ids / new_skills / skills 等任何字段
```

apply 阶段（`run_orchestrator.py` 应用 iteration 到新模块快照时）：

```python
new_module = OcrModule(
    module_key=current.module_key,
    description=iter.new_description,
    ocr_suggestions=iter.new_ocr_suggestions,
    ocr_prompt=iter.new_ocr_prompt,
    skill_ids=list(current.skill_ids),       # ★ 硬编码 copy，永远从 current 来
    schema_fragment=current.schema_fragment,
    json_path=current.json_path,
    order_index=current.order_index,
    ...
)
```
**严禁** 从 LLM 输出读 `skill_ids` 来源。Code review 时这是 hard rule。

### 9.2 Meta Optimizer

**职责**：每轮跑完所有模块后调一次，决定增/删/改名/拆分模块。

**输入构造**：
- 当前所有模块的 `module_key`、`json_path`、本轮 accuracy、`optimization_suggestion` 摘要
- **孤儿 GT 字段**：扫所有样本 GT JSON 的所有 leaf path，找出**不在任何 module.json_path 覆盖范围内**的 path → 提示需要新模块
- **空输出模块**：本轮 OCR 在所有样本上对该模块的切片都为空/null → 提示模块定义有问题，可能需要删除或重构

**Prompt 模板** (`prompts/meta_optimizer.txt`)：
```
你是 OCR Prompt 系统的元优化器。你看到所有模块的本轮表现，需要决定模块组合的结构性变更。

# 文档类型
{api_def.description}

# 当前模块组合
| module_key | json_path | accuracy | 本轮 suggestion 摘要 |
{module_table}

# Ground Truth 中存在但无人认领的字段路径
{unclaimed_paths}

# 当前在所有样本上都输出空的模块
{empty_modules}

# 你的任务
判断是否需要：
1. 新增模块（孤儿字段太多或语义独立）
2. 删除模块（空输出且无 GT 对应字段）
3. 改名/拆分/合并（语义重叠或粒度不当）

仅返回严格 JSON：
{
  "add_modules": [
    {"module_key": "...", "display_name": "...", "json_path": "...",
     "schema_fragment": {...}, "description": "...", "ocr_suggestions": {...},
     "ocr_prompt": "...", "order_index": N}
  ],
  "remove_module_keys": ["..."],
  "rename": [{"old": "...", "new": "..."}],
  "rationale": "一句话理由"
}
```

返回 `add_modules: [], remove_module_keys: [], rename: []` 表示本轮结构不动。

---

## 10. Composer — 拼接最终 prompt 与 schema

### 10.1 composed_prompt 结构

```
{global_preamble}                       # 固定头：JSON 输出要求、不要 markdown 等
{global_output_contract}                # 整体 schema 简介

## 模块识别指令
### 1. {module[0].display_name}
{module[0].ocr_prompt}

### 2. {module[1].display_name}
{module[1].ocr_prompt}

...

{global_self_check}                     # 固定尾：自检清单
```

**global_preamble** / **global_output_contract** / **global_self_check** 是模板常量，不进入模块迭代。

> **§6.4 例外**：当 `OcrPromptVersion.origin == 'init'` 且来源是预设国家 yaml 时，`composed_prompt` **不**由 composer 生成，直接存 yaml 的 `prompt_format` 原文（已替换占位符）。`module_key='global_rules'` 那个特殊 module 的 `ocr_prompt` 内容即对应 yaml 全局段落的镜像，仅用于后续 v2 起 composer 重新拼接时还原全局规则；不参与 v1 的 OCR 调用。从 v2 起（advance_round 产物），composer 正常拼接所有 modules 包括 global_rules（order_index=0 保证它排在最前）。

### 10.2 composed_schema 结构

由各模块的 `schema_fragment` 按 `json_path` 合并：
- `$.shop_name` 的 fragment 放到顶层 `properties.shop_name`
- `$.items[*]` 的 fragment 放到 `properties.items.items`
- 冲突（多个模块声明同一 path）→ Run 失败，要求人工干预

---

## 11. 开发模式：本地 JSON 镜像

为了快速本地调试 prompt 优化逻辑（无需启动 DB），提供 JSON 文件后端。通过环境变量切换：

```bash
OCR_OPTIMIZER_BACKEND=sql      # 生产 (default)
OCR_OPTIMIZER_BACKEND=json     # 开发
OCR_OPTIMIZER_JSON_DIR=./data/ocr_optimizer
```

JSON 文件布局（每个文件对应一张 SQL 表的子集）：
```
data/ocr_optimizer/
├── {api_definition_id}/
│   ├── versions/
│   │   ├── v001.json           # OcrPromptVersion + 嵌套 modules[]
│   │   ├── v002.json
│   │   └── ...
│   └── runs/
│       ├── {run_id}.json       # OcrOptimizationRun + 嵌套 rounds[].iterations[]
│       └── ...
```

`v001.json` 示例：
```json
{
  "id": "...",
  "version": 1,
  "status": "active",
  "composed_prompt": "...",
  "composed_schema": {...},
  "overall_accuracy": null,
  "modules": [
    {
      "id": "...", "module_key": "shop_name", "display_name": "店名识别",
      "description": "...", "json_path": "$.store_name",
      "schema_fragment": {"type": "string"},
      "ocr_suggestions": {"semantics": "...", "position": "...", "most_common_feature": "..."},
      "ocr_prompt": "...", "order_index": 0
    }
  ]
}
```

`storage/sql_backend.py` 和 `storage/json_backend.py` 实现同一接口（`save_version` / `load_version` / `save_run` / ...），上层 service 不感知后端差异。

JSON 后端**只用于本地开发**，CI 和生产必须用 SQL。

---

## 12. API 端点

挂在现有 `/api/v1` 下：

| 方法 | 端点 | 说明 |
|------|------|------|
| **POST** | `/api/v1/api-definitions/from-country-template` | **新**：从预设国家 yaml 一站式创建占位 ApiDefinition + OcrPromptVersion v1 + 30 modules。请求体 `{country: "MY"}`；返回 `{api_definition_id, version_id, redirect_url: "/workspace/api/<id>"}`。详见 §6.4 |
| POST | `/api-definitions/{id}/ocr-optimizer/init` | 从 response_schema 自动拆分初始模块（§6.1），创建首个 OcrPromptVersion (draft, origin='init')。请求体 `{sample_document_ids: [uuid,...]}` |
| GET | `/api-definitions/{id}/ocr-optimizer/versions` | 列出所有版本（含 modules 摘要 + origin 字段） |
| GET | `/api-definitions/{id}/ocr-optimizer/versions/{version_id}` | 单版本详情（含所有模块全文） |
| PATCH | `/api-definitions/{id}/ocr-optimizer/versions/{version_id}/activate` | 直接激活某版本（finalize 之外的旁路；通常仅用于回滚） |
| **POST** | `/api-definitions/{id}/ocr-optimizer/versions/{version_id}/manual-patch` | **新**：基于该版本派生 manual_edit 版本。请求体 `{edits: [{module_key, description?, ocr_suggestions?}, ...]}`。返回派生版本 (status='draft', origin='manual_edit')。详见 §7.4 |
| POST | `/api-definitions/{id}/ocr-optimizer/optimize` | **行为变更**：现在**只跑一轮（Round 1）**然后挂起。请求体 `{sample_document_ids?, max_rounds?}`。**同步返回** `{run_id, status:'paused_for_review', round: <Round1 详情>}` |
| **POST** | `/api-definitions/{id}/ocr-optimizer/runs/{run_id}/advance` | **新**：从 paused_for_review 推进到下一轮。请求体 `{use_version_id?: uuid}`（不传则用最近 Round 的 next_version）。同步返回新一轮数据 |
| **POST** | `/api-definitions/{id}/ocr-optimizer/runs/{run_id}/finalize` | **新**：结束 Run 并激活用户选定版本。请求体 `{version_id: uuid}`。详见 §7.6 |
| **POST** | `/api-definitions/{id}/ocr-optimizer/runs/{run_id}/abort` | **新**：放弃此次 Run（不修改 active 版本） |
| GET | `/api-definitions/{id}/ocr-optimizer/runs` | 列出该 API 的所有 Run |
| GET | `/api-definitions/{id}/ocr-optimizer/runs/{run_id}` | Run 详情（含 rounds 摘要 + status + current_round_num） |
| GET | `/api-definitions/{id}/ocr-optimizer/runs/{run_id}/rounds/{round_num}` | 单轮详情（含所有模块的 iteration） |
| GET | `/api-definitions/{id}/ocr-optimizer/runs/{run_id}/iterations` | Flat 列出本 Run 所有迭代 |

### 12.1 Skill endpoints（TODO — 占位，详见 §18）

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/ocr-skills` | 列出全局 + 当前 API 私有的 skills。MVP 返回空列表 |
| POST | `/ocr-skills` | 创建 skill。MVP 阶段返回 `501 Not Implemented` 或落到只读 mock |
| PATCH | `/ocr-skills/{id}` | 更新 skill。MVP 同上 |
| DELETE | `/ocr-skills/{id}` | 删除 skill。MVP 同上 |
| POST | `/api-definitions/{api_id}/ocr-optimizer/modules/{module_key}/skills` | 把已存在的 skill 挂到模块上（修改 OcrModule.skill_ids）。MVP 同上 |

前端按钮在 MVP 阶段一律弹 toast `Coming Soon`，不真正调上面任何端点。

### 12.2 上传已标注数据端点（新）

| 方法 | 端点 | 说明 |
|------|------|------|
| **POST** | `/api/v1/documents/upload-with-annotations` | multipart/form-data：`file`（图片/PDF/XLSX）+ `annotations`（JSON 文件）。后端**单事务**创建 Document + 解析 JSON → 写入 Annotation 行（source='manual', is_corrected=True） |

JSON 格式（**包装元信息版**，详见 UI_DESIGN §14）：
```json
{
  "filename": "receipt_001.png",
  "annotations": [
    {"field_path": "store_name", "value": "..."},
    {"field_path": "items[0].price", "value": 12.50},
    {"field_path": "items[0].name", "value": "..."}
  ]
}
```
后端校验：
- `field_path` 必须命中 ApiDefinition.response_schema 的某个 leaf path
- 类型不匹配 → 拒绝整个请求（事务回滚），返回 400 + 字段列表

---

## 13. 迁移步骤（从现有代码切换）

1. **新建 alembic migration**：
   - DROP TABLE `prompt_versions`（现有 v1 单体）
   - CREATE TABLE × 5（本文档 §5）
2. **删除文件**：`backend/app/optimizers/` 整个目录
3. **新建目录**：`backend/app/ocr_optimizer/`（按 §4 结构）
4. **改 imports**：
   - `backend/app/models/__init__.py:14` — 删除 `from app.optimizers.model import PromptVersion`
   - `backend/app/api/v1/router.py:21` — 删除 `from app.optimizers.router import router as prompts_router`，新增 `from app.ocr_optimizer.router import router as ocr_optimizer_router`
   - `backend/app/services/extract_service.py:97` — `from app.optimizers import get_active_prompt` 改为 `from app.ocr_optimizer.service import get_active_composed_prompt`；签名相同（接收 `db, api_def_id` 返回字符串）
5. **数据迁移**：现有 `prompt_versions` 表如果有数据，先 export 一份 prompt_text，待新系统第一次 init 时作为「manual 模块」整体导入（一个 module 包含全部内容）；用户后续手动重构成多模块
6. **`ApiDefinition.config` 改造**：将 `sample_document_id` (单) 字段迁移成 `sample_document_ids` (list)。读取时双向兼容一段时间：`config.get("sample_document_ids") or [config["sample_document_id"]]`
7. **`api_definitions` 表加列**：`status VARCHAR(24) NOT NULL DEFAULT 'active'`，取值 `'active'` / `'pending_first_doc'`。alembic 迁移把存量行回填为 `'active'`。`'pending_first_doc'` 行在列表查询中默认过滤（详见 §16.1）
8. **删除文件**：`backend/app/services/initial_extraction.py` 整个删除；`backend/app/api/v1/documents.py` 中 `POST /{id}/initial-extract` 端点删除（被 §6.4 路径完全替代）
9. **新增文件**：`backend/app/ocr_optimizer/service/template_loader.py`（解析 yaml + 拆解 30 modules）、`backend/app/ocr_optimizer/service/preset_init.py`（编排 ApiDef + Version + Modules 创建）
10. **改 reprocess 行为**：`backend/app/services/document_service.py::reprocess_document` 当 `prompt=None` 时改为优先取 `ApiDefinition.prompt_version_id` 对应 `OcrPromptVersion.composed_prompt`；只有完全无 active version 时才用旧 fallback
11. **新增端点**：`POST /api/v1/api-definitions/from-country-template`，参数 `{country: "MY"}`。endpoint body 详见 §12

---

## 14. 终止条件 & 异常处理

**正常终止**：
- `overall_accuracy >= target_accuracy`
- `round_num == max_rounds`
- 连续 2 轮 accuracy 无提升（自第 3 轮起）

**异常终止**：
- OCR 调用失败（重试 2 次后仍失败）→ Run.status='failed', error_message 记录
- Module Optimizer LLM 返回非法 JSON → 该模块本轮保持不变，记录到 iteration.llm_call_metadata
- Meta Optimizer 失败 → 本轮不做结构变更，只应用模块内部变更
- composed_schema 冲突（两个模块声明同一 json_path）→ Run.status='failed'

**人工 abort**：`PATCH /runs/{id}` body `{action: "abort"}` → Run.status='aborted'，已完成的 rounds 保留。

---

## 15. 关键约束（必须遵守）

1. **每一轮必须跑完整 OCR**，不准跳过任何模块对应的样本（模块间互相影响）
2. **达到 100% 的模块跳过 Module Optimizer LLM 调用，但仍保留 iteration 记录**（aggregate_accuracy=1.0, optimization_suggestion=null）
3. **OcrPromptVersion 一旦 activate 不可修改**，要改只能产生新版本
4. **module_key 跨版本稳定**，便于追踪同一模块的演化链
5. **Annotation 是 GT 唯一来源**，优化器禁止读 ProcessingResult 的 structured_data 当 GT
6. **modules 数量上限 20**（防止 meta optimizer 无限增模块），超过则 Run.status='failed' 提示人工干预
    - **例外**：§6.4 country yaml 初始化路径**允许首版超过 20**（实测 MY 为 30）。这是用户预设输入，meta_optimizer 后续轮**不得新增** module 越过 20，但允许保持/缩减
7. **sample_document_ids 至少 3 张**，少于则拒绝触发
8. **生产部署必须 SQL backend**，JSON backend 仅本地开发
9. **Run 不自驱动多轮**：每跑完一轮立刻 `paused_for_review`，必须用户显式 advance / finalize / abort（§7）
10. **Optimizer 永远不允许修改 skill_ids 或新建 OcrSkill**。代码层强制：
    - `ModuleOptimizerOutput` Pydantic schema 不含任何 skill 修改字段且 `extra='forbid'`
    - `run_orchestrator` apply 阶段 `new.skill_ids = current.skill_ids.copy()` 硬编码
    - `manual_patch` 端点也不允许在 edits 中包含 skill_ids（schema 校验拒绝）
    - 修改 skill_ids 的唯一合法途径：§12.1 `/modules/{key}/skills` 端点（MVP TODO）
11. **manual_edit 版本不自动 activate**。它只是 paused_for_review 状态下的 draft，需通过 §7.6 finalize 显式选定才生效
12. **`global_rules` module 受保护**（仅 §6.4 路径产生）：
    - module_key 永远 = `"global_rules"`，跨版本不可改名
    - `module_optimizer` 不为它计算 diff（accuracy 字段记 NULL，跳过 LLM 调用）
    - `meta_optimizer.remove_module_keys` 不允许包含 `"global_rules"`；包含则被代码层 strip 并记 warning
    - composer 在拼接时把 `global_rules.ocr_prompt` 放在 `GLOBAL_PREAMBLE` **之后、其他 module 之前**（依 order_index=0 保证）
13. **国家 yaml 文件不可被代码修改**（`MY_invoice_prompt.yaml` 等仓库根目录文件）。只读加载；如需迭代该模板必须由用户直接编辑文件。任何 service 不允许调用 `open(path, 'w')` 写 yaml

---

## 16. 占位 ApiDefinition 生命周期（pending_first_doc）

§6.4 在用户选国家的那一刻就创建 `status='pending_first_doc'` 的 ApiDefinition。下面定义这个状态的完整生命周期。

### 16.1 状态转换

```
[用户在 /workspace/new 点 MY chip]
        │
        ▼
POST /api/v1/api-definitions/from-country-template {country:"MY"}
        │
        ▼
DB 写入: ApiDefinition(status='pending_first_doc', name="MY_invoice_<hex>",
                       api_code="my-invoice-<hex>", ...)
        + OcrPromptVersion v1 (active)
        + 30 个 OcrModule
        │
        ▼
[前端跳 /workspace/api/<id>]
        │
        ├─ 用户在 7 天内：
        │      上传文档 → sample_document_ids 写入 config，doc.api_definition_id 绑定
        │      编辑 GT → Annotation 行写入
        │      点「保存并生成 API」→ ApiDefinition.status='active'，
        │                            用户提交的 name/description/api_code 覆盖默认值
        │
        └─ 用户 7 天内无任何上述活动：
               下次有人 GET /api/v1/api-definitions（或定时任务跑）时
               lazy 检查 (now() - updated_at) >= 7d AND status='pending_first_doc'
               → DELETE CASCADE: ApiDefinition + Version + Modules + 关联 Document
```

### 16.2 字段定义

`api_definitions.status` 列：

| 取值 | 含义 |
|------|------|
| `'active'` | 正常 API（默认值；用户已保存生效） |
| `'pending_first_doc'` | §6.4 创建，等待用户上传/编辑/保存 |

`api_definitions.updated_at` 列：现有字段，用于过期判断。下述操作刷新该字段：
- 上传文档绑定到该 ApiDef
- 创建/修改 Annotation
- 用户在 workspace 内任何动作触发的 PATCH/PUT
- 不刷新：单纯 GET 查询

### 16.3 列表过滤

`GET /api/v1/api-definitions` 默认 `status='active'`。新增可选 query `?include_pending=true` 暴露占位 API（仅供 debug，前端 ApiList 不传）。

### 16.4 清理策略

**MVP**：lazy cleanup。每次 list 查询前执行一条 `DELETE FROM api_definitions WHERE status='pending_first_doc' AND updated_at < now() - interval '7 days'`。

**未来**：拆出 cron 任务/Celery beat 定时执行（与 list 解耦）。

### 16.5 用户切换国家的处理

§6.4 决策：**不允许切换国家**。前端国家 chip 在跳转后已不可见（用户已离开 /workspace/new 进入 /workspace/api/<id>）。如用户需要换国家，唯一方法：

1. 在 ApiList 页删掉这个占位 API（或等 7 天自动清）
2. 回首页重新点「定制新 API」→ 选新国家

代码层无需特殊"切换"逻辑。

---

## 17. 待后续讨论的开放点

下面这些是本文档没决定、需要你后续确认的点：

1. **Run 异步执行机制**：现有项目用 `SyncRunner`（同步）。Run 可能跑 5 轮 × N 样本 = 大量 OCR + LLM 调用，可能超 30s。是否需要引入 BackgroundTasks 或后续切 Celery？原型可以接受同步阻塞吗？
2. **样本选择是否分训练/验证集**：当前所有 sample 都参与 evaluator 计算 accuracy，存在用样本"过拟合"风险。是否要保留 1-2 张作为 hold-out？
3. **历史 K 的具体值**：默认 3 轮，但 round 1 没历史，round 2 只有 1 轮。是否需要"冷启动"模式（前 2 轮不传 history）？
4. **模块的 order_index 谁定**：初始化时按 schema 顺序，meta optimizer 增模块时 append 到末尾。是否需要让 meta optimizer 也可以调整顺序？
5. **多语言模板**：模块的 description / ocr_prompt 写中文还是英文？跟 ApiDefinition 的 language 字段绑定？
6. **module schema_fragment 的标准**：JSON Schema 草案版本（draft-07 vs 2020-12）？Gemini 已有 `_normalize_schema` 适配层（[`gemini_processor.py:100`](../backend/app/processors/gemini_processor.py)），composer 输出可以直接对接。

---

## 18. Skills 子系统（TODO — 占位设计）

> **状态**：仅 design + DB schema + 占位 endpoint + 前端按钮入口。**业务逻辑、composer 集成、LLM 反馈消费均不在 MVP 实现范围。** 所有具体功能点亮前，前端按钮一律 toast `Coming Soon`。

### 17.1 概念

一个 **Skill** 是一段可复用、被多个 OCR 模块引用的**纵切能力片段**，例如：
- "如何读表格" — 如何识别表格行/列结构、合并单元格、跨页表
- "如何读小票商品" — 商品行的多列对齐、汇总行剔除、单位归一化
- "如何读印章/签名" — 视觉对象 vs 文字识别

Skill 不属于某个具体模块的 prompt 一部分，而是被模块"挂载"。Composer 在拼接最终 prompt 时会把模块挂载的所有 skills 拼到该模块片段后面。

### 17.2 表 6 — `ocr_skills`（新表，TODO 状态）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `api_definition_id` | UUID | FK → `api_definitions.id`, NULLABLE, INDEX | NULL = 全局库；非空 = 仅该 API 私有。**跨 API 共享通过 NULL** |
| `name` | VARCHAR(128) | NOT NULL | 短名，UI 显示用，如 "如何读表格" |
| `description` | TEXT | NOT NULL | 较长描述，告诉用户这个 skill 解决什么 |
| `content` | TEXT | NOT NULL | 真正注入 prompt 的内容片段 |
| `status` | VARCHAR(16) | NOT NULL, default `'active'` | `active` / `archived` |
| `created_by` | UUID | NULLABLE | |
| `created_at` | TIMESTAMP | server_default now() | |
| `updated_at` | TIMESTAMP | NULLABLE | |

UNIQUE：`(api_definition_id, name)` —— 同一 API（或全局）内 name 不重复。

### 17.3 模块挂载方式

`OcrModule.skill_ids: JSON list of uuid`（§5.3 已加占位）：
```json
["skill-uuid-1", "skill-uuid-2"]
```
**跨版本行为**：复制模块到新 `OcrPromptVersion` 时，`skill_ids` 整列原样复制（不像 description/prompt 会被 LLM 重写）。

### 17.4 Composer 集成（TODO）

当前 `composer.assemble_prompt` 不读 `skill_ids`。未来实现时的逻辑：

```python
def assemble_prompt(modules: list[OcrModule], skill_map: dict[uuid, OcrSkill]) -> str:
    sections = []
    for m in sorted(modules, key=lambda x: x.order_index):
        block = f"### {m.order_index+1}. {m.display_name}\n{m.ocr_prompt}\n"
        # ↓ 未来：追加挂载的 skills
        skills = [skill_map[sid] for sid in m.skill_ids if sid in skill_map]
        if skills:
            block += "\n**适用技能（参考以下指引）：**\n"
            for s in skills:
                block += f"- **{s.name}**: {s.content}\n"
        sections.append(block)
    return GLOBAL_PREAMBLE + "\n\n".join(sections) + GLOBAL_SELF_CHECK
```

MVP 期间该函数签名保持 `(modules) -> str`，不需要 `skill_map`。

### 17.5 Optimizer 反馈链路

Optimizer **唯一能做的事**就是在 `OcrModuleIteration.skill_feedback` 写一段话，类似：

> "现有 skill `如何读小票商品` 不能处理多页延续的小票（第二页只有商品行没有店头）。建议人工添加一个 skill 处理跨页拼接。"

这段反馈：
- **不会**被任何代码自动消费
- 只在 UI 上展示给用户作为参考（§UI_DESIGN §13）
- 用户可以选择手动添加 skill 来响应（MVP 后实现）

### 17.6 端点占位

详见 §12.1。MVP 期间所有 skill 端点返回：
```json
{ "detail": "Skills are coming soon. This endpoint is a placeholder." }
```
HTTP 状态 `501 Not Implemented`（或 200 + 空数组，对 GET 类）。

### 17.7 强制：Optimizer 与 Skill 的关系（再次重申）

| 操作 | Optimizer | 用户（UI） |
|------|-----------|-----------|
| 读 `OcrModule.skill_ids` | ✅ 在 prompt 上下文里被告知 | ✅ |
| 写 `OcrModule.skill_ids` | ❌ 永远禁止 | ✅（MVP 后） |
| 创建 `OcrSkill` | ❌ 永远禁止 | ✅（MVP 后） |
| 修改 `OcrSkill.content` | ❌ 永远禁止 | ✅（MVP 后） |
| 在 `Iteration.skill_feedback` 留言 | ✅ 仅此 | — |

代码层保证手段已在 §9 + §15.10 详细列明。
