# 国家模板初始化（Country Template Init）端到端流程

> 关联文档：
> - 数据结构 / 拆解规则：[`ocr-optimizer-design.md §6.4`](./ocr-optimizer-design.md)
> - 占位 API 生命周期：[`ocr-optimizer-design.md §16`](./ocr-optimizer-design.md)
> - 前端 UI：[`UI_DESIGN.md §14`](./UI_DESIGN.md)
> - 起始点 yaml：[`MY_invoice_prompt.yaml`](../MY_invoice_prompt.yaml)（**不可修改**）

---

## 1. 设计目标

让用户在「定制新 API」入口选一个国家（如 MY），系统：

1. 立即生成一个挂着 MY 预设 prompt 的可用 ApiDefinition（占位状态）
2. 拿这个预设 prompt 跑用户上传的第一张文档，得到一份"初始字段"
3. 用户基于这份初始字段编辑 ground truth
4. 后续走通用的 ocr_optimizer 优化流程

> **核心约束**：
> - yaml 文件只读（用户编辑 yaml = 升级模板，不通过 UI）
> - 占位 ApiDef 不出现在 ApiList，避免污染列表
> - 不允许选完国家后换国家（不引入状态机）

---

## 2. 数据流时序图

```
用户                  前端                       后端                           DB
 │                     │                          │                            │
 │  点「定制新 API」  │                          │                            │
 ├────────────────────►│                          │                            │
 │                     │  navigate /workspace/new │                            │
 │                     │                          │                            │
 │                     │  GET /country-templates  │                            │
 │                     ├─────────────────────────►│                            │
 │                     │                          │ scan repo root *.yaml     │
 │                     │  [{MY,true}, {CN,false}] │                            │
 │                     │◄─────────────────────────┤                            │
 │  渲染国家 chips     │                          │                            │
 │◄────────────────────┤                          │                            │
 │                     │                          │                            │
 │  点 MY              │                          │                            │
 ├────────────────────►│                          │                            │
 │                     │  POST /from-country-template {country:"MY"}            │
 │                     ├─────────────────────────►│                            │
 │                     │                          │ 加载 MY_invoice_prompt.yaml │
 │                     │                          │ 替换 {tax_categories_text}  │
 │                     │                          │ 拆解 30 modules            │
 │                     │                          ├──BEGIN TX──────────────────►│
 │                     │                          │  ApiDefinition INSERT      │
 │                     │                          │  OcrPromptVersion INSERT  │
 │                     │                          │  OcrModule × 30 INSERT    │
 │                     │                          ├──COMMIT─────────────────────►│
 │                     │  {api_definition_id, redirect_url}                     │
 │                     │◄─────────────────────────┤                            │
 │                     │  navigate /workspace/api/<id>                          │
 │                     │                          │                            │
 │  上传文档            │                          │                            │
 ├────────────────────►│                          │                            │
 │                     │  POST /documents/upload?api_definition_id=<id>         │
 │                     ├─────────────────────────►│                            │
 │                     │                          │ 保存文件                   │
 │                     │                          │ doc.api_definition_id 绑定 │
 │                     │                          │ 自动 reprocess（prompt=None│
 │                     │                          │  → 取 active version 的    │
 │                     │                          │   composed_prompt）        │
 │                     │                          │ Gemini OCR                 │
 │                     │  doc + result            │                            │
 │                     │◄─────────────────────────┤                            │
 │  三栏 Workspace 展示│                          │                            │
 │  字段                │                          │                            │
 │◄────────────────────┤                          │                            │
 │                     │                          │                            │
 │  编辑 GT             │                          │                            │
 ├────────────────────►│  PUT /annotations/...    │                            │
 │                     ├─────────────────────────►│                            │
 │                     │                          │ Annotation UPDATE          │
 │                     │                          │ ApiDef.updated_at 刷新     │
 │                     │                          │                            │
 │  点保存             │                          │                            │
 ├────────────────────►│  PATCH /api-definitions/<id> {status:'active', name, …}│
 │                     ├─────────────────────────►│                            │
 │                     │                          │ ApiDef UPDATE              │
 │                     │  ok                      │                            │
 │                     │◄─────────────────────────┤                            │
 │  跳 ApiList         │                          │                            │
 │◄────────────────────┤                          │                            │
```

---

## 3. 后端模块清单

### 3.1 新增文件

| 路径 | 职责 |
|------|------|
| `backend/app/ocr_optimizer/service/template_loader.py` | 扫描根目录 `*_invoice_prompt.yaml`、解析、占位符替换、字段拆解 |
| `backend/app/ocr_optimizer/service/preset_init.py` | 编排 `init_from_country_template(country)`：建 ApiDef + Version + 30 Modules（单事务） |
| `backend/app/api/v1/country_templates.py` | 端点：`GET /country-templates`、`POST /api-definitions/from-country-template` |
| `backend/alembic/versions/<hash>_apidefinition_status.py` | 加 `api_definitions.status` 列 |

### 3.2 修改文件

| 路径 | 改动 |
|------|------|
| `backend/app/models/api_definition.py` | 加 `status: Mapped[str] = mapped_column(String(24), default='active')` |
| `backend/app/services/api_definition_service.py::list_api_definitions` | 默认 filter `status='active'`；新增 query 参数 `include_pending`；list 入口处跑 lazy cleanup |
| `backend/app/services/document_service.py::reprocess_document` | 当 `prompt=None` 且 `doc.api_definition_id` 有值时，从 active OcrPromptVersion 取 `composed_prompt` 作为 prompt（不再用 `INITIAL_INVOICE_EXTRACTION_PROMPT`） |
| `backend/app/api/v1/documents.py::upload_document` | 收到 `api_definition_id` form 字段时，doc 绑定到该 API，上传后自动 reprocess |

### 3.3 删除文件

| 路径 | 原因 |
|------|------|
| `backend/app/services/initial_extraction.py` | 被 §6.4 + active version 路径完全替代 |
| `backend/app/api/v1/documents.py` 内 `POST /{id}/initial-extract` 端点 | 同上 |

---

## 4. 前端模块清单

### 4.1 新增

| 路径 | 职责 |
|------|------|
| `frontend/src/components/workspace-v2/CountryPickerBar.tsx` | `/workspace/new` 顶部的国家 chip 列表 |
| `frontend/src/lib/api-client.ts` 新方法 | `fetchCountryTemplates()`, `initFromCountryTemplate(country)` |

### 4.2 修改

| 路径 | 改动 |
|------|------|
| `frontend/src/pages/Workspace.tsx` | `isNewMode` 时顶部插入 `<CountryPickerBar onPicked={(apiDefId) => navigate(...)} />`，禁用未选国家时的上传 UI |
| `frontend/src/stores/workspace-store.ts` | **删除** `triggerInitialExtraction` 方法（不再用硬编码 prompt）；调整 `loadDocument` 后的初始化逻辑：检查 `apiDefinitionId` 是否存在，若存在则不另发提取 |
| `frontend/src/components/workspace-v2/InlineUploadPanel.tsx` | 上传时附 `api_definition_id` form 字段（从 store 读） |
| `frontend/src/components/workspace-v2/WorkspaceModals.tsx::SaveModal` | "保存"时若当前是 pending_first_doc，发 PATCH 把 status 改 active + 提交用户改的 name/description/api_code |

### 4.3 删除

| 路径 | 原因 |
|------|------|
| `frontend/src/stores/workspace-store.ts` 的 `_initialExtractionDone` Set 和 `triggerInitialExtraction` | 不再有"硬编码 prompt 路径"，整体废除 |
| 上次会话给 Workspace.tsx mode C 加的 `location.state.fromNewApi` 触发 | 同上，替代为「ApiDef 绑定后自动 reprocess」 |

---

## 5. 数据库迁移

```sql
-- alembic upgrade
ALTER TABLE api_definitions ADD COLUMN status VARCHAR(24) NOT NULL DEFAULT 'active';
-- 不需要回填，DEFAULT 已生效
CREATE INDEX idx_api_definitions_status ON api_definitions(status);

-- alembic downgrade
DROP INDEX idx_api_definitions_status;
ALTER TABLE api_definitions DROP COLUMN status;
```

---

## 6. 占位符替换规则

`MY_invoice_prompt.yaml` 含一个占位符 `{tax_categories_text}`。`template_loader.py` 加载时：

```python
TAX_CATEGORIES_DEFAULT = "请使用文档中出现的原名"
prompt_format = yaml_data["prompt_template"]["prompt_format"]
prompt_format = prompt_format.replace("{tax_categories_text}", TAX_CATEGORIES_DEFAULT)
```

将来若需要每国家独立的税种映射，可改成 yaml 内带 `tax_categories: [...]` 字段，loader 读出后渲染。本期不做。

---

## 7. global_rules 特殊 module 的拆解规则

从 yaml.prompt_format 中按段落标题提取以下内容拼成 global_rules.ocr_prompt：

| yaml 段落标题（含 `**` 加粗） | 是否进 global_rules |
|---|---|
| `**任务描述：**` | 否（开场白，由 composer.GLOBAL_PREAMBLE 替代） |
| `**输入说明：**` | 否（同上） |
| `**处理要求：**` | 否（在 composer.GLOBAL_PREAMBLE 中体现） |
| `**票据分类标准：**` | 否（被各字段 module 的描述继承） |
| `**提取规则：**` 及之后所有段落（金额处理、日期处理、必填字段、缺失信息、税种简称映射、税种全面提取等） | 是 |

提取算法（伪代码）：

```python
def extract_global_rules(prompt_format: str) -> str:
    marker = "**提取规则：**"
    idx = prompt_format.find(marker)
    if idx == -1:
        raise ValueError("yaml.prompt_format 缺少 **提取规则：** 段，无法拆出 global_rules")
    return prompt_format[idx:]
```

---

## 8. 字段拆解的 schema_fragment 取值

对 yaml.json_schema.items.anyOf[0].properties 中每个字段 K：

```python
field_schema = yaml_data["prompt_template"]["json_schema"]["items"]["anyOf"][0]["properties"][K]
module.schema_fragment = field_schema  # 原样拷贝，保留 ARRAY/OBJECT 大写
module.json_path = f"$[*].{K}"
```

数组类字段（detailOfGoodsOrServices, detailOfTaxSummary, originalInvoiceReferences）：
- `module.json_path = "$[*].<K>[*]"`
- `module.schema_fragment = field_schema["items"]` （只取每行的 schema，外层 ARRAY 由 composer 在 assemble_schema 阶段加回）

---

## 9. 测试矩阵

| 场景 | 期望 |
|---|---|
| 点 MY chip → 上传 PDF | OCR 完成，三栏显示 ~26 个字段 + 数组明细 |
| 点 MY chip 后浏览器关闭 → 7 天内再回 `/workspace/api/<id>` | 占位 API 仍在，可继续编辑 |
| 点 MY chip 后浏览器关闭 → 8 天后访问 ApiList | 占位 API 已被 lazy 清理（GET `/api-definitions` 触发） |
| 同时多次点 MY chip（防双击） | 前端 chip 禁用；后端 idempotent 不保证（会建多个占位）。MVP 接受 |
| yaml 文件被删 | `GET /country-templates` 返回该国家 `available:false`；点击灰显的 chip 不能点 |
| 点击灰色 chip（如 CN） | 不发请求，仅 toast「该国家模板尚未提供」 |
| ApiList 不出现 pending API | 默认 query 已过滤 |
| 上传时 doc 绑定 api_definition_id → reprocess prompt 来源 | `composed_prompt` from active OcrPromptVersion（v1 = yaml 原文）|
| v2 起 composer 拼接 | 包含 `global_rules.ocr_prompt`（GLOBAL_PREAMBLE 之后、其他 module 之前）|

---

## 10. 未来扩展点

- **多国家**：补充 `CN_invoice_prompt.yaml` / `US_invoice_prompt.yaml` / `EU_invoice_prompt.yaml` 等到根目录，chip 自动亮起
- **占位 API GC 切 cron**：把 lazy cleanup 改成独立 task（Celery beat / APScheduler）
- **国家切换**：如果业务上需要"换国家"功能，加 `POST /api-definitions/<id>/switch-country` 端点；本期明确不做
- **yaml 模板版本化**：yaml 加 `version` 字段，loader 校验向前兼容；本期 yaml 已自带 `id` 和 `structure_prompt_version`，留作未来字段
