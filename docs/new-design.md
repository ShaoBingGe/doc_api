# ApiAnything MVP 设计文档

> 基于原始 `apianything_design.md` 的简化版本，面向内部原型使用，保留扩展性。

---

## 1. 项目定位

ApiAnything 是一个通用文档结构化数据提取 API 平台。用户上传文档（PDF、图片、Excel），AI 提取结构化数据，通过对话式矫正迭代优化，最终生成可调用的提取 API。

**原型目标**：内部人员可用的最小闭环，覆盖"上传 → 提取 → 矫正 → 生成 API → 外部调用"全流程。

**原型不做**：用户体系/多租户、模板市场、计费、画框矫正/区域 OCR、高亮逆向定位、国际化。

---

## 2. 技术架构

### 2.1 整体架构

前后端分离，基础设施极简，通过接口抽象保留扩展性。

```
React SPA (Vite)          FastAPI
┌──────────────┐    HTTP    ┌─────────────────────────────┐
│  /workspace  │◄──────────►│  api/v1/                    │
│  /apis       │            │    ├── documents.py          │
│  /settings   │            │    ├── conversations.py      │
│              │   SSE      │    ├── api_defs.py           │
│  ChatPanel   │◄───────────│    ├── extract.py            │
└──────────────┘            │    └── api_keys.py           │
                            │                              │
                            │  services/ (业务逻辑)         │
                            │  engine/  (AI处理，移植代码)   │
                            │                              │
                            │  abstractions/               │
                            │    ├── storage.py             │
                            │    ├── task_runner.py         │
                            │    └── auth.py                │
                            │                              │
                            │  SQLite / PostgreSQL          │
                            │  本地文件 / S3                │
                            └─────────────────────────────┘
```

### 2.2 基础设施选型

| 组件 | 原型方案 | 后续扩展 |
|------|---------|---------|
| 数据库 | SQLite（同步 SQLAlchemy） | 换连接串切 PostgreSQL |
| 文件存储 | 本地文件系统 `./data/uploads/` | 实现 S3Storage |
| 任务处理 | 同步调用 | 实现 CeleryRunner |
| 认证 | API Key（提取 API 必须，管理 API 可选） | 实现 JWTAuth + 多租户 |
| API 网关 | FastAPI 内置中间件 | 前置 Kong/APISIX |
| 实时通信 | SSE（对话矫正） | 加 WebSocket（处理进度） |

### 2.3 接口抽象层

三个抽象接口，通过环境变量切换实现：

```python
# StorageBackend
class StorageBackend(ABC):
    def save(self, file, filename) -> str: ...
    def load(self, path) -> bytes: ...
    def delete(self, path) -> None: ...

# 实现：LocalStorage（原型）、S3Storage（后续）

# TaskRunner
class TaskRunner(ABC):
    def run(self, func, *args, **kwargs) -> Any: ...
    def get_status(self, task_id) -> str: ...

# 实现：SyncRunner（原型）、CeleryRunner（后续）

# AuthProvider
class AuthProvider(ABC):
    def authenticate(self, request) -> dict: ...
    def create_key(self, name) -> tuple[str, str]: ...

# 实现：SimpleApiKeyAuth（原型）、JWTAuth（后续）
```

环境变量配置：
```bash
STORAGE_BACKEND=local    # 或 s3
TASK_RUNNER=sync         # 或 celery
AUTH_PROVIDER=api_key    # 或 jwt
```

---

## 3. 项目结构

```
ApiAnything/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py          # Pydantic BaseSettings
│   │   │   ├── database.py        # SQLAlchemy 引擎
│   │   │   └── deps.py            # get_db, get_current_api_key
│   │   ├── abstractions/
│   │   │   ├── storage.py         # StorageBackend + LocalStorage
│   │   │   ├── task_runner.py     # TaskRunner + SyncRunner
│   │   │   └── auth.py            # AuthProvider + SimpleApiKeyAuth
│   │   ├── models/
│   │   │   ├── document.py        # Document + ProcessingResult
│   │   │   ├── conversation.py    # Conversation + Message
│   │   │   ├── api_definition.py  # ApiDefinition
│   │   │   └── api_key.py         # ApiKey
│   │   ├── schemas/               # Pydantic Request/Response
│   │   ├── api/v1/
│   │   │   ├── documents.py
│   │   │   ├── conversations.py
│   │   │   ├── api_defs.py
│   │   │   ├── extract.py
│   │   │   ├── api_keys.py
│   │   │   └── router.py
│   │   ├── services/
│   │   │   ├── document_service.py
│   │   │   ├── conversation_service.py
│   │   │   ├── api_definition_service.py
│   │   │   └── extract_service.py
│   │   └── engine/
│   │       ├── processors/
│   │       │   ├── base.py
│   │       │   ├── factory.py
│   │       │   ├── gemini.py
│   │       │   ├── openai.py
│   │       │   ├── piaozone.py
│   │       │   └── mock.py
│   │       ├── config/
│   │       │   ├── manager.py
│   │       │   └── models.yaml
│   │       ├── analyzers/
│   │       │   └── excel.py
│   │       ├── correction.py       # 对话矫正引擎（新增）
│   │       └── schema_generator.py  # Schema 推断（新增）
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Workspace.tsx
│   │   │   ├── ApiList.tsx
│   │   │   └── Settings.tsx
│   │   ├── components/
│   │   │   ├── document/          # DocumentPreview
│   │   │   ├── fields/            # FieldEditor, FieldCard
│   │   │   ├── chat/              # ChatPanel, MessageBubble
│   │   │   └── api/               # JsonPreview, CodeSnippet
│   │   ├── lib/
│   │   │   ├── api-client.ts
│   │   │   └── sse.ts
│   │   └── stores/
│   │       └── workspace-store.ts
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
│
└── README.md
```

---

## 4. 数据模型

5 个核心模型：

### Document
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| filename | str | 原始文件名 |
| file_path | str | 存储路径 |
| file_type | str | pdf / image / excel |
| status | enum | uploading → processing → completed → failed |
| processor_key | str | 如 `gemini\|gemini-2.5-flash` |
| created_at | datetime | |

### ProcessingResult
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| document_id | FK → Document | |
| version | int | 每次矫正 +1 |
| structured_data | JSON | AI 提取的结构化数据 |
| inferred_schema | JSON | JSON Schema |
| prompt_used | text | 本次使用的 prompt |
| source | enum | initial / correction / manual_edit |
| created_at | datetime | |

### Conversation
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| document_id | FK → Document (unique) | 一个文档一个对话 |
| created_at | datetime | |

### Message
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| conversation_id | FK → Conversation | |
| role | enum | user / assistant / system |
| content | text | |
| result_version | int | 关联的 ProcessingResult 版本（可选） |
| created_at | datetime | |

### ApiDefinition
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| name | str | 用户命名 |
| api_code | str | 唯一编码，调用 URL 标识 |
| description | str | |
| status | enum | draft → active → deprecated |
| schema_definition | JSON | 输出 Schema |
| prompt_template | text | 最终 Prompt |
| processor_key | str | |
| source_document_id | FK → Document (可选) | |
| created_at | datetime | |
| updated_at | datetime | |

### ApiKey
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| name | str | 备注名 |
| key_hash | str | SHA-256 |
| key_prefix | str | `sk-...abc` |
| is_active | bool | |
| created_at | datetime | |

实体关系：
```
Document ──1:N── ProcessingResult
Document ──1:1── Conversation ──1:N── Message
ApiDefinition (独立，source_document_id 可选引用 Document)
ApiKey (独立，全局有效)
```

---

## 5. API 端点

### 5.1 管理 API（前端调用，原型阶段无认证）

**文档**
| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/documents/upload` | 上传文档，同步处理后返回结果 |
| GET | `/api/v1/documents` | 文档列表 |
| GET | `/api/v1/documents/:id` | 文档详情 + 最新 ProcessingResult |
| GET | `/api/v1/documents/:id/results` | 所有版本处理结果 |
| POST | `/api/v1/documents/:id/reprocess` | 重新处理 |

**对话矫正**
| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/documents/:id/conversation` | 获取/自动创建对话 |
| POST | `/api/v1/documents/:id/conversation/messages` | 发送矫正指令（SSE 流式） |

SSE 事件协议：
```
event: text_delta
data: {"content": "正在修改..."}

event: result_update
data: {"version": 3, "structured_data": {...}, "schema": {...}}

event: done
data: {}
```

**API 定义**
| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/api-definitions` | 创建 API 定义 |
| GET | `/api/v1/api-definitions` | 列表 |
| GET | `/api/v1/api-definitions/:id` | 详情 |
| PATCH | `/api/v1/api-definitions/:id` | 更新 |

**API Key**
| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/api-keys` | 创建（仅返回一次明文） |
| GET | `/api/v1/api-keys` | 列表 |
| DELETE | `/api/v1/api-keys/:id` | 删除 |

### 5.2 公有云提取 API（X-API-Key 认证）

```
POST /api/v1/extract/{api_code}
  Headers: X-API-Key: sk-xxxx
  Body: multipart/form-data (file)
  Response: {
    "request_id": "uuid",
    "api_code": "invoice-v1",
    "data": { ... },
    "schema": { ... }
  }
```

### 5.3 错误响应格式

```json
{
  "error": {
    "code": "api_not_found",
    "message": "API definition not found"
  }
}
```

---

## 6. 前端设计

### 6.1 页面结构

三个页面，路由用 React Router：

| 页面 | 路径 | 功能 |
|------|------|------|
| 工作台 | `/workspace` | 核心三栏页面 |
| API 管理 | `/apis` | API 卡片列表 + 详情 |
| 设置 | `/settings` | API Key 管理 |

### 6.2 工作台三栏布局

```
┌─────────────────────────────────────────────────────┐
│  [上传文档]              ApiAnything        [设置]   │
├──────────────────┬───────────────┬──────────────────┤
│  A: 文档预览      │  B: 字段编辑   │  C: JSON 输出    │
│  (50%)           │  (30%)        │  (20%)           │
│                  │               │                  │
│  PDF/图片/表格    │  字段卡片列表   │  JSON 树形展示   │
│  渲染            │  双击行内编辑   │  代码示例 tab    │
│                  │  [+ 添加字段]  │                  │
│                  ├───────────────┤                  │
│                  │  对话矫正区    │                  │
│                  │  SSE 流式显示  │                  │
├──────────────────┴───────────────┴──────────────────┤
│  v1 ● v2 ○ v3 ○                [保存并生成 API]     │
└─────────────────────────────────────────────────────┘
```

**A 栏**：PDF 用 react-pdf 渲染，图片直接展示，Excel 表格渲染。不做高亮叠加层。

**B 栏**：
- 字段卡片列表，树形展示支持嵌套/数组
- 双击 → 行内编辑（字段名 + 值变为 input）→ 保存更新 ProcessingResult
- [+ 添加字段] → 行内表单
- 底部对话区：输入框 + 消息列表，SSE 流式

**C 栏**：JSON 树形展示 + 代码示例 tab。点击 B 栏字段高亮对应 JSON 路径。

**版本切换**：底部版本条，点击查看历史版本。

### 6.3 三栏联动（简化版）

原型阶段只做 B→C 单向联动：
- 点击 B 栏字段 → C 栏高亮对应 JSON 路径
- 不做 A 栏高亮（需要 canvas + 逆向定位，后续加）

### 6.4 前端依赖

| 库 | 用途 |
|----|------|
| React 18 + Vite | 框架 |
| React Router | 路由 |
| TailwindCSS | 样式 |
| zustand | 状态管理 |
| react-pdf | PDF 渲染 |
| react-json-view-lite | JSON 展示 |
| fetch + EventSource | API + SSE |

---

## 7. 引擎移植

### 7.1 从旧项目移植（无改动/微改动）

| 源文件 (label-studio-ml-backend/invoice_extractor/) | 目标 (backend/app/engine/) | 改动 |
|-----|------|------|
| processors/base.py | processors/base.py | 无 |
| processors/factory.py | processors/factory.py | 去掉 LS 依赖 |
| processors/gemini.py | processors/gemini.py | 无 |
| processors/openai.py | processors/openai.py | 无 |
| processors/piaozone.py | processors/piaozone.py | 无 |
| processors/mock.py | processors/mock.py | 无 |
| config/manager.py | config/manager.py | 去掉 IP 白名单 |
| config/models.yaml | config/models.yaml | 无 |
| analyzers/excel_analyzer.py | analyzers/excel.py | 无 |

### 7.2 新增模块

- **correction.py** — 对话矫正引擎：解析用户自然语言指令 → 修改 Schema + Prompt → 调用处理器重新提取
- **schema_generator.py** — 从 AI 返回的 JSON 自动推断 JSON Schema（类型检测、嵌套数组、字段描述）

### 7.3 原型不做

- region_ocr.py — 画框 OCR
- prompt_optimizer.py — auto-research 循环

---

## 8. 核心流程

### 流程 1：上传文档 → 提取

```
POST /api/v1/documents/upload (multipart)
  → DocumentService.upload():
      storage.save(file) → 本地存储
      创建 Document(status=processing)
      task_runner.run(process_document):
        processor = factory.create(processor_key)
        result = processor.extract(file_bytes)
        schema = schema_generator.infer(result)
      创建 ProcessingResult(version=1, structured_data, inferred_schema, prompt_used)
      Document.status = completed
  → 返回 Document + ProcessingResult
```

### 流程 2：对话矫正

```
POST /api/v1/documents/:id/conversation/messages (SSE)
  → ConversationService.correct():
      获取当前 ProcessingResult + 对话历史
      创建 Message(role=user)
      correction.build_correction_prompt(用户指令, 当前 schema, 当前 prompt, 历史消息)
      调用 LLM 流式返回 → SSE text_delta
      解析 LLM 输出 → 新的 prompt + schema 修改
      processor.extract(file, new_prompt) → 新结果
      创建 ProcessingResult(version=N+1)
      创建 Message(role=assistant, result_version=N+1)
      SSE result_update 事件
      SSE done
```

### 流程 3：双击编辑字段

```
前端双击字段 → 行内编辑 → 保存
  → POST /api/v1/documents/:id/results (或 PATCH)
      更新 structured_data 中对应字段
      创建新 ProcessingResult(version=N+1, source=manual_edit)
  → 前端更新 B 栏 + C 栏
```

### 流程 4：生成 API

```
POST /api/v1/api-definitions
  body: { document_id, name, api_code, description }
  → ApiDefinitionService.create():
      获取文档最新 ProcessingResult
      创建 ApiDefinition:
        schema_definition = result.inferred_schema
        prompt_template = result.prompt_used
        processor_key = document.processor_key
        status = active
  → 返回 ApiDefinition + 调用示例
```

### 流程 5：外部调用提取 API

```
POST /api/v1/extract/{api_code}
  Headers: X-API-Key: sk-xxxx
  → ExtractService.extract():
      auth.authenticate(request)
      definition = 查询 ApiDefinition(api_code, status=active)
      storage.save(上传文件) → 临时存储
      processor = factory.create(definition.processor_key)
      result = processor.extract(file, prompt=definition.prompt_template, schema=definition.schema_definition)
  → 返回 { request_id, api_code, data, schema }
```

---

## 9. 环境变量

```bash
# 数据库
DATABASE_URL=sqlite:///./data/apianything.db   # 开发用 SQLite

# 文件存储
STORAGE_BACKEND=local
UPLOAD_DIR=./data/uploads

# 任务处理
TASK_RUNNER=sync

# AI 处理器
GEMINI_API_KEY=xxx
OPENAI_API_KEY=xxx
DEFAULT_PROCESSOR=gemini
DEFAULT_MODEL=gemini-2.5-flash

# 应用
APP_ENV=development
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173   # Vite 默认端口
```

---

## 10. 扩展路径

当内部原型验证可用后，按需逐步扩展：

| 阶段 | 扩展内容 | 改动范围 |
|------|---------|---------|
| **扩展 1** | SQLite → PostgreSQL | 改 DATABASE_URL |
| **扩展 2** | 本地存储 → S3 | 实现 S3Storage，改 STORAGE_BACKEND=s3 |
| **扩展 3** | 同步 → Celery 异步 | 实现 CeleryRunner + WebSocket 进度，改 TASK_RUNNER=celery |
| **扩展 4** | 加用户体系 + 多租户 | 加 User/Org 模型 + JWTAuth + 数据隔离 |
| **扩展 5** | 画框矫正 + 高亮叠加 | 加 region_ocr + prompt_optimizer + 前端 canvas 层 |
| **扩展 6** | 模板市场 | 加 Template 模型 + 前端模板页 |
| **扩展 7** | 计费 + 网关 | 加 UsageRecord + Kong/APISIX |

每个扩展独立，不互相依赖，可按需选择顺序。

---

## 11. 重要约束（沿用原设计）

1. **引擎处理器代码不可随意修改**：gemini.py、openai.py、piaozone.py 是生产验证代码。
2. **Schema 跨模型兼容已内置**：处理器内部已处理 Gemini/OpenAI Schema 差异。
3. **处理器运行时切换**：使用 `processor_type|model_name` 格式。
4. **API Key 安全**：`sk-` 前缀 + 32 字节 Base62，数据库只存 SHA-256，创建时仅返回一次明文。
5. **SSE 对话协议**：text_delta → result_update → done 三阶段。
6. **Prompt 泛化原则**：prompt 不绑定特定文档坐标，确保可泛化到同类文档。
