# ApiAnything v2 设计文档

> 基于 `new-design.md` 极简架构，融合 `old-design.md` 的字段精细化 / 标注编辑 / 引擎移植设计，
> 前端严格对标 `api-anything-prototype.html` 高保真原型。

---

## 1. 设计哲学（全文最高优先级）

三条原则贯穿前后端每一个设计决策：

**① 单页核心，弹窗辅助**
整个产品以「工作台 `/workspace`」为唯一核心页面。上传文档、AI 识别、矫正字段、调整 API 格式、调试优化——全部在同一页面内完成，用户无需跳转。模板选择、生成 API、沙箱调试等辅助流程通过按钮 + 弹窗承载。

**② 字段驱动，逆向定位**
不使用传统「框选区域 → 提取内容」，而是「AI 提取字段 → 逆向定位到文档区域高亮」。用户的注意力始终在字段结果上，文档预览是辅助确认手段。

**③ Prompt 泛化，不存位置**
用户在 A 栏画框矫正时触发专项 OCR，结果用于优化 Prompt。系统只保留 Prompt 方法，不保留「坐标→内容」硬编码，确保识别能力可泛化到同类文档。标注位置数据仅用于前端高亮和后续训练，永远不写入 Prompt。

---

## 2. 项目定位

ApiAnything 是一个通用文档结构化数据提取 API 平台。用户上传文档（PDF、图片、Excel），AI 提取结构化数据，通过对话式矫正迭代优化，最终生成可调用的提取 API。

**原型目标**：内部人员可用的最小闭环——「上传 → 提取 → 矫正 → 生成 API → 外部调用」。

**原型不做**：用户体系/多租户、模板市场、计费、国际化。

---

## 3. 技术架构

### 3.1 整体架构

前后端分离，基础设施极简，通过接口抽象保留扩展性。

```
React SPA (Vite)               FastAPI
┌────────────────────┐  HTTP   ┌───────────────────────────────┐
│  /workspace        │◄───────►│  api/v1/                      │
│  /apis             │         │    ├── documents.py            │
│  /settings         │  SSE    │    ├── conversations.py        │
│                    │◄────────│    ├── annotations.py          │
│  三栏工作台         │         │    ├── api_defs.py             │
│  + 弹窗层          │         │    ├── extract.py              │
│                    │         │    └── api_keys.py             │
└────────────────────┘         │                               │
                               │  services/  (业务逻辑)         │
                               │  engine/   (AI引擎，移植代码)   │
                               │                               │
                               │  abstractions/                │
                               │    ├── storage.py              │
                               │    ├── task_runner.py          │
                               │    └── auth.py                 │
                               │                               │
                               │  SQLite / PostgreSQL           │
                               │  本地文件 / S3                  │
                               └───────────────────────────────┘
```

### 3.2 基础设施选型

| 组件 | 原型方案 | 后续扩展 |
|------|---------|---------|
| 数据库 | SQLite（同步 SQLAlchemy） | 换连接串切 PostgreSQL |
| 文件存储 | 本地文件系统 `./data/uploads/` | 实现 S3Storage |
| 任务处理 | 同步调用 | 实现 CeleryRunner |
| 认证 | API Key（提取 API 必须，管理 API 可选） | 实现 JWTAuth + 多租户 |
| API 网关 | FastAPI 内置中间件 | 前置 Kong/APISIX |
| 实时通信 | SSE（对话矫正） | 加 WebSocket（处理进度） |

### 3.3 接口抽象层

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

## 4. 项目结构

```
ApiAnything/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py              # Pydantic BaseSettings
│   │   │   ├── database.py            # SQLAlchemy 引擎
│   │   │   └── deps.py               # get_db, get_current_api_key
│   │   ├── abstractions/
│   │   │   ├── storage.py             # StorageBackend + LocalStorage
│   │   │   ├── task_runner.py         # TaskRunner + SyncRunner
│   │   │   └── auth.py               # AuthProvider + SimpleApiKeyAuth
│   │   ├── models/
│   │   │   ├── document.py            # Document + ProcessingResult
│   │   │   ├── conversation.py        # Conversation + Message
│   │   │   ├── annotation.py          # Annotation（字段标注）
│   │   │   ├── api_definition.py      # ApiDefinition
│   │   │   └── api_key.py             # ApiKey
│   │   ├── schemas/                   # Pydantic Request/Response
│   │   ├── api/v1/
│   │   │   ├── documents.py
│   │   │   ├── conversations.py
│   │   │   ├── annotations.py         # 标注 CRUD
│   │   │   ├── api_defs.py
│   │   │   ├── extract.py
│   │   │   ├── api_keys.py
│   │   │   └── router.py
│   │   ├── services/
│   │   │   ├── document_service.py
│   │   │   ├── conversation_service.py
│   │   │   ├── annotation_service.py  # 标注存储/查询
│   │   │   ├── api_definition_service.py
│   │   │   └── extract_service.py
│   │   └── engine/
│   │       ├── processors/            # ⭐ 移植自旧项目（不可修改）
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
│   │       ├── correction.py          # 对话矫正引擎（新增）
│   │       └── schema_generator.py    # Schema 推断（新增）
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx                    # 路由入口
│   │   ├── pages/
│   │   │   ├── Workspace.tsx          # ⭐ 唯一核心页面（三栏 + 步骤条 + 弹窗）
│   │   │   ├── ApiList.tsx            # API 管理列表
│   │   │   └── Settings.tsx           # API Key 管理
│   │   ├── components/
│   │   │   ├── workspace/
│   │   │   │   ├── StepIndicator.tsx      # 步骤进度条
│   │   │   │   ├── UploadStep.tsx         # 上传 + 模板选择
│   │   │   │   ├── ProcessingOverlay.tsx  # AI 处理中全屏遮罩
│   │   │   │   └── VersionBar.tsx         # 底部版本切换条
│   │   │   ├── document/
│   │   │   │   └── DocumentCanvas.tsx     # A 栏：文档预览 + bbox 叠加 + 拖拽
│   │   │   ├── fields/
│   │   │   │   ├── FieldEditorPanel.tsx   # B 栏：字段列表 + 行内编辑
│   │   │   │   ├── FieldCard.tsx          # 单个字段卡片（名称/值/置信度/位置）
│   │   │   │   ├── AddFieldForm.tsx       # 添加新字段行内表单
│   │   │   │   └── NlCorrectionBar.tsx    # 底部自然语言矫正输入
│   │   │   ├── api/
│   │   │   │   ├── ApiPreviewPanel.tsx    # C 栏：JSON + 格式切换 + 自然语言调整
│   │   │   │   ├── ApiConfigStep.tsx      # 生成 API 成功页（endpoint + 代码）
│   │   │   │   └── CodeSnippet.tsx        # cURL / Python / Node 代码示例
│   │   │   └── modal/
│   │   │       ├── TemplateModal.tsx      # 模板选择弹窗
│   │   │       └── GenerateApiModal.tsx   # 生成 API 确认弹窗
│   │   ├── lib/
│   │   │   ├── api-client.ts          # fetch 封装
│   │   │   └── sse.ts                 # SSE 流式处理
│   │   └── stores/
│   │       └── workspace-store.ts     # Zustand：字段、选中、bbox、版本、步骤
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   └── package.json
│
└── README.md
```

---

## 5. 数据模型

6 个核心模型。

### 5.1 实体关系

```
Document ──1:N── ProcessingResult
Document ──1:1── Conversation ──1:N── Message
Document ──1:N── Annotation（字段标注，用于训练）
ApiDefinition（独立，source_document_id 可选引用 Document）
ApiKey（独立，全局有效）
```

### 5.2 模型定义

#### Document
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| filename | str | 原始文件名 |
| file_path | str | 存储路径 |
| file_type | str | pdf / image / excel |
| status | enum | uploading → processing → completed → failed |
| processor_key | str | 如 `gemini\|gemini-2.5-flash` |
| created_at | datetime | |

#### ProcessingResult
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

#### Conversation
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| document_id | FK → Document (unique) | 一个文档一个对话 |
| created_at | datetime | |

#### Message
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| conversation_id | FK → Conversation | |
| role | enum | user / assistant / system |
| content | text | |
| result_version | int | 关联的 ProcessingResult 版本（可选） |
| created_at | datetime | |

#### Annotation（字段标注 — 训练数据桥梁）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| document_id | FK → Document | |
| field_name | str | 字段名称，如 `invoice_number` |
| field_value | str | 识别/矫正后的值 |
| field_type | str | string / number / date / array |
| bounding_box | JSON | `{x, y, w, h, page}` 百分比坐标 |
| source | enum | ai_detected / manual | AI 识别或手动标注 |
| confidence | float | AI 置信度（手动标注为 null） |
| result_version | int | 关联的 ProcessingResult 版本 |
| created_at | datetime | |
| updated_at | datetime | |

**重要区分**：Annotation 存储的是**特定文档的标注数据**（含 bounding_box），用于后续模型训练。自然语言矫正修改的是 ProcessingResult 里的 **Prompt 和 Schema**，用于泛化。两者数据模型独立，互不影响。

#### ApiDefinition
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

#### ApiKey
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | |
| name | str | 备注名 |
| key_hash | str | SHA-256 |
| key_prefix | str | `sk-...abc` |
| is_active | bool | |
| created_at | datetime | |

### 5.3 关键字段设计补充

| 模型 | 关键字段 | 设计要点 |
|------|---------|---------|
| **Document** | `status` | 状态机：uploading → processing → completed / failed。前端通过轮询或 SSE 监听状态变化驱动 UI |
| **ProcessingResult** | `version` | 每次矫正（自然语言/手动编辑）产生新版本，旧版本不删除，底部版本条可切换回溯 |
| **ProcessingResult** | `structured_data` | 存储完整的 Key-Value 列表，每个字段含 `{id, keyName, value, confidence, bbox}` |
| **ProcessingResult** | `inferred_schema` | 由 `schema_generator.py` 从 structured_data 自动推断，存储 JSON Schema 格式 |
| **Annotation** | `bounding_box` | 百分比坐标 `{x, y, w, h, page}`，与图片尺寸无关。仅用于前端高亮和训练，不写入 Prompt |
| **Annotation** | `source` | `ai_detected`（AI 自动标注）/ `manual`（用户手动标注）。手动标注在训练时权重更高，代表 AI 能力盲区 |
| **ApiDefinition** | `api_code` | 形如 `EXT-INV-A3B7C2`，作为 URL path 标识 |
| **ApiKey** | `key_hash` | 只存 SHA-256 哈希。`sk-` 前缀 + 32 字节 Base62，创建时仅返回一次明文 |

---

## 6. API 端点

### 6.1 管理 API（前端调用，原型阶段无认证）

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

**标注**
| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/documents/:id/annotations` | 获取文档所有标注数据 |
| POST | `/api/v1/documents/:id/annotations` | 新增字段标注（手动添加字段） |
| PATCH | `/api/v1/documents/:id/annotations/:fid` | 更新字段标注（编辑名称/值/bbox） |
| DELETE | `/api/v1/documents/:id/annotations/:fid` | 删除字段标注 |

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

SSE 事件协议：
```
event: text_delta
data: {"content": "正在修改..."}

event: result_update
data: {"version": 3, "structured_data": {...}, "schema": {...}}

event: done
data: {}
```

### 6.2 公有云提取 API（X-API-Key 认证）

```
POST /api/v1/extract/{api_code}
  Headers: X-API-Key: sk-xxxx
  Body: multipart/form-data (file)
  Response: {
    "request_id": "uuid",
    "api_code": "EXT-INV-A3B7C2",
    "data": { ... },
    "schema": { ... }
  }
```

### 6.3 错误响应格式

```json
{
  "error": {
    "code": "api_not_found",
    "message": "API definition not found"
  }
}
```

---

## 7. 前端设计（高保真，对标原型 HTML）

### 7.1 设计系统

#### 色彩体系（暗色主题，与原型完全一致）

```typescript
const colors = {
  // 背景层次
  bg:            "#0f1117",    // 最底层背景
  surface:       "#1a1d27",    // 卡片/面板背景
  surfaceHover:  "#232736",    // 悬停态
  border:        "#2a2e3d",    // 默认边框
  borderActive:  "#6366f1",    // 激活态边框（靛蓝）

  // 文字层次
  text:          "#e2e8f0",    // 主文字
  textMuted:     "#94a3b8",    // 次要文字
  textDim:       "#64748b",    // 最弱文字/禁用

  // 功能色
  primary:       "#6366f1",    // 主色（靛蓝）
  primaryHover:  "#818cf8",    // 主色悬停
  primaryBg:     "rgba(99,102,241,0.12)",  // 主色背景
  success:       "#22c55e",    // 成功/高置信度
  successBg:     "rgba(34,197,94,0.12)",
  warning:       "#f59e0b",    // 警告/中置信度
  warningBg:     "rgba(245,158,11,0.12)",
  danger:        "#ef4444",    // 错误/低置信度
  dangerBg:      "rgba(239,68,68,0.12)",
  accent:        "#06b6d4",    // 强调色（青色，用于 API 格式调整）
};
```

#### 排版

```
字体栈: -apple-system, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif
代码字体: "Fira Code", "Courier New", monospace

文字大小:
  - 标题: 16-20px, font-weight 700
  - 正文: 12-13px, font-weight 400
  - 标签/辅助: 9-11px, font-weight 600, text-transform uppercase, letter-spacing 0.5px

全局:
  - 自定义滚动条: 6px 宽, #1a1d27 轨道, #2a2e3d 滑块
  - 所有输入框 focus 态: border-color #6366f1
  - 所有按钮 hover 态: opacity 0.9
  - 过渡动画: 0.15s ease（选中/高亮/切换）
```

### 7.2 页面结构

| 页面 | 路径 | 功能 |
|------|------|------|
| **工作台** | `/workspace` | ⭐ 唯一核心页面（全部流程在此完成） |
| **API 管理** | `/apis` | API 卡片列表 + 详情 |
| **设置** | `/settings` | API Key 管理 |

### 7.3 工作台页面——完整结构

工作台是一个全屏单页，从上到下分为：顶栏 → 步骤条 → 主内容区 → 底栏。

```
┌─────────────────────────────────────────────────────────────────┐
│  ⚡ ApiAnything [Prototype]                    [← 上一步] [下一步 →] │  ← 顶栏 (TopBar)
├─────────────────────────────────────────────────────────────────┤
│  ① 上传文档 ── ② AI识别预览 ── ③ 矫正结果 ── ④ 调整API格式 ── ⑤ 调试优化 ── ⑥ 生成API  │  ← 步骤条 (StepIndicator)
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│                     主内容区（按步骤切换）                          │
│                                                                 │
│  步骤0: UploadStep（上传 + 模板选择）                               │
│  步骤1-4: 三栏布局（DocumentCanvas | FieldEditor/ApiPreview | —）  │
│  步骤5: ApiConfigStep（API 就绪页）                                │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  v1 ● v2 ○ v3 ○                              [保存并生成 API]    │  ← 底栏 (VersionBar)
└─────────────────────────────────────────────────────────────────┘
```

### 7.4 步骤 0：上传文档 (UploadStep)

全屏展示，上下两部分。

**上半部分 — 拖拽上传区**：
- 虚线边框 `2px dashed`，圆角 12px
- 默认态：border `colors.border`，背景 `colors.surface`
- 拖入态：border `colors.primary`，背景 `colors.primaryBg`
- 中心图标：📄（48px，opacity 0.6）
- 主文字："拖拽文档到此处，或点击上传"（16px 600）
- 副文字："支持 PDF、图片、Word、Excel 等常见格式"（12px `textMuted`）
- 补充："支持任意语言的文字型文档"（11px `textDim`）

**下半部分 — 预置模板**：
- 标题："或选择预置模板快速开始"（13px 600）
- 3 列网格，每个模板卡片：
  - 国旗 emoji（20px）
  - 模板名称（12px 600）
  - 字段数量提示（10px `textDim`）
  - 选中态：border `colors.primary`，背景 `colors.primaryBg`

```
预置模板列表:
🇨🇳 中国增值税发票 (12 个预置字段)
🇺🇸 US Standard Invoice (10 个预置字段)
🇯🇵 日本領収書 (8 个预置字段)
🇩🇪 Deutsche Rechnung (11 个预置字段)
🇰🇷 한국 영수증 (9 个预置字段)
✨ 自定义模板 (自由定义字段)
```

### 7.5 AI 处理中 (ProcessingOverlay)

上传后全屏遮罩，居中展示：
- 旋转齿轮 ⚙️（40px，CSS `spin` 动画 1s linear infinite）
- 主文字："AI 正在识别文档..."（16px 600）
- 副文字："使用多模态大模型提取结构化数据"（12px `textMuted`）
- 进度条：200px 宽 × 3px 高，`loading` 动画（来回扫描效果）
- 背景：`colors.bg` 纯色

### 7.6 步骤 1-4：三栏工作台

步骤 1 至步骤 4 共享同一个三栏布局。左侧为 A 栏文档预览（`flex: 1`），右侧为 B/C 栏切换面板（固定宽度 340px）。

```
┌─────────────────────────────────┬──────────────────┐
│  A 栏: DocumentCanvas            │  Tab: [字段] [API] │
│  (flex: 1, padding 12px)        │  (width: 340px)   │
│                                 │                   │
│  📄 文档预览 — 点击/拖动调整识别区域 │  步骤1-2: 字段 Tab  │
│                                 │  步骤3: API Tab    │
│  ┌───────────────────────┐      │  步骤4: 字段 Tab   │
│  │  白色文档背景           │      │                   │
│  │  + 文字内容渲染         │      │  当前 Tab 内容:    │
│  │  + bbox 高亮叠加层      │      │  FieldEditorPanel │
│  │  + 拖拽/缩放交互        │      │   或              │
│  │                       │      │  ApiPreviewPanel  │
│  └───────────────────────┘      │                   │
│                                 │                   │
│  步骤4独有: [🔄 重新上传同类文档]   │                   │
└─────────────────────────────────┴──────────────────┘
```

#### A 栏 — DocumentCanvas 组件

**文档渲染层**（底层）：
- 白色背景，圆角 8px，`box-shadow: 0 2px 12px rgba(0,0,0,0.3)`
- PDF：使用 `react-pdf` 渲染
- 图片：直接 `<img>` 渲染
- 文档内文字使用等宽字体渲染

**Bbox 高亮叠加层**（上层）：
- 每个识别结果渲染一个绝对定位的高亮框
- 定位基于百分比坐标：`left: bbox.x%`, `top: bbox.y%`, `width: bbox.w%`, `height: bbox.h%`
- 边框颜色按置信度分级：
  - ≥ 95%: `colors.success` (#22c55e 绿色)
  - ≥ 90%: `colors.warning` (#f59e0b 橙色)
  - < 90%: `colors.danger` (#ef4444 红色)
- 非选中态：边框 2px solid + 背景色 11% 透明度
- 选中态：边框 `colors.primary`(#6366f1) + 背景 `rgba(99,102,241,0.15)`
- 每个框左上角标签牌：字段名（9px 600 白色字，背景为边框同色，圆角 `3px 3px 0 0`，绝对定位 `top: -20px`）

**交互行为**：
- 点击框 → `onSelect(item.id)`，三栏联动选中
- 点击空白 → `onSelect(null)`，取消选中
- 选中后框体可拖拽移动（cursor: grab → grabbing）
- 选中后右下角显示 8×8px 缩放手柄（`colors.primary` 正方形），拖拽调整尺寸
- 拖拽时取消过渡动画（`transition: none`），释放后恢复 `transition: all 0.15s ease`
- 鼠标事件处理：mousedown 记录起始位置和原始 bbox → mousemove 计算 dx/dy 转换为百分比偏移 → mouseup 结束

#### B 栏 — FieldEditorPanel 组件

从上到下：自然语言矫正输入 → 字段列表。

**自然语言矫正区（顶部，固定）**：
- 标题："自然语言矫正"（11px 600 uppercase `textMuted`）
- 输入行：`<input>` + [矫正] 按钮
  - placeholder: `'例如: "Payment Method 应该是 Wire Transfer"'`
  - 输入框：`colors.bg` 背景，`colors.border` 边框，12px 字号
  - 按钮：`colors.primary` 背景，白色字，12px 600
  - Enter 键触发
- 矫正历史（最大 50px，overflow: auto）：
  - 每条："✓ 时间 — 指令内容"（10px `colors.success`）

**字段列表区（flex: 1, overflow: auto）**：
- 标题："识别字段 (N)"（11px 600 uppercase `textMuted`）
- 每个字段渲染为 `FieldCard`：

```
┌────────────────────────────────────────────┐
│  Invoice Number                    [98%]   │  ← keyName(11px 600) + 置信度标签
│  ┌──────────────────────────────────────┐  │
│  │ INV-2026-00421                      │  │  ← value 输入框（12px, 可直接编辑）
│  └──────────────────────────────────────┘  │
│  位置: (58.0%, 8.0%) | 20.0×3.5           │  ← bbox 信息（9px textDim）
└────────────────────────────────────────────┘
```

  - 默认态：透明背景，透明边框
  - 选中态：`colors.primaryBg` 背景，`colors.borderActive` 边框
  - 置信度标签：圆角药丸（9px），颜色同 bbox 分级
  - value 输入框：直接可编辑，选中时边框变为 `colors.borderActive`
  - 位置信息：`(x%, y%) | w×h` 格式

**字段精细化能力（B 栏核心）**：

B 栏支持复杂嵌套数据结构的可视化编辑：

1. **多行货物明细**：数组类型字段展开为子卡片列表，每行显示 `{行号, 货物名称, 规格型号, 数量, 单价, 金额, 税额}`
2. **税额汇总**：数组 + 合计行，合计字段可显示公式引用标记
3. **数据逻辑加工规则**：如 `amount = qty × price`、校验容差 `±0.01`，这些规则会写入 Prompt 和 ApiDefinition
4. **字段操作**：
   - 修改值 → 直接在输入框编辑
   - 修改类型 → 点击类型标签切换（string / number / date / array）
   - 增删字段 → [+ 添加识别字段] 按钮 / 删除按钮
   - 重命名 → 双击 keyName 进入编辑
   - 拖拽调整嵌套层级（后续扩展）
5. **置信度显示**：每个字段右侧显示 AI 识别置信度百分比 + 颜色标签；手动添加的字段标记为"手动"

**标注编辑模式（B 栏 — 连接 AI 识别与训练优化的桥梁）**：

```
双击字段行 → 进入编辑模式
  → B 栏: 字段名 + 值变为 contenteditable，右侧显示 ✓ 保存按钮
  → A 栏: 对应高亮区域显示四角拖拽手柄
    • 拖拽框体 → 移动识别区域位置
    • 拖拽手柄 → 扩大/缩小识别区域
  → C 栏: 对应 JSON 行高亮，字段名和格式可联动编辑
  → 点击 ✓ 保存:
    1. 前端更新 workspace-store 中的字段定义和值
    2. 调用 PATCH /api/v1/documents/:id/annotations/:field_id
    3. 标注数据（字段名、值、bounding_box、类型）写入 Annotation 模型
    4. Annotation 数据用于后续微调训练，不影响当前 Prompt 泛化逻辑

添加新字段 → 点击 [+ 添加识别字段]
  → 展开行内表单: 名称 | 值 | 类型(string/number/date/array) | 保存/取消
  → 保存后:
    • 调用 POST /api/v1/documents/:id/annotations
    • B 栏新增一行字段（置信度标记为"手动"）
    • C 栏 JSON 输出同步新增该字段
```

**重要区分**：标注编辑保存的是**特定文档的标注数据**（Annotation），用于训练。自然语言矫正修改的是 **Prompt 和 Schema**（ProcessingResult），用于泛化。两者互不干扰。

#### C 栏 — ApiPreviewPanel 组件

从上到下：格式切换 → 自然语言格式调整 → JSON 输出。

**格式切换（顶部）**：
- 标题："API 返回格式"（11px 600 uppercase `textMuted`）
- 三个按钮并排：[扁平] [详细] [分组]
  - 选中态：border `colors.primary`，背景 `colors.primaryBg`，文字 `colors.primaryHover`
  - 未选中：border `colors.border`，透明背景，文字 `colors.textMuted`

**自然语言格式调整**：
- 输入框 + [调整] 按钮
  - placeholder: `'例如: "按供应商信息和财务信息分组"'`
  - 按钮：`colors.accent`(#06b6d4) 背景

**JSON 输出区（flex: 1, overflow: auto）**：
- `<pre>` 代码块：`colors.bg` 背景，`colors.border` 边框，圆角 8px
- 代码文字：11px 行高 1.5，颜色 `#a5f3fc`（青色代码高亮）
- 字体：`'Fira Code', 'Courier New', monospace`

**三种格式输出**：

扁平模式:
```json
{
  "success": true,
  "data": {
    "invoice_number": "INV-2026-00421",
    "total_amount": "¥128,450.00"
  }
}
```

详细模式:
```json
{
  "success": true,
  "fields": [
    {
      "key": "invoice_number",
      "label": "Invoice Number",
      "value": "INV-2026-00421",
      "confidence": 0.98,
      "position": { "x": 58, "y": 8, "width": 20, "height": 3.5 }
    }
  ],
  "meta": { "total_fields": 10, "doc_type": "invoice" }
}
```

分组模式:
```json
{
  "success": true,
  "data": {
    "vendor": { "vendor_name": "...", "vendor_address": "..." },
    "financial": { "total_amount": "...", "tax_amount": "..." },
    "payment": { "payment_method": "...", "bank_account": "..." }
  }
}
```

#### Tab 切换逻辑

右侧面板顶部有 [字段] [API] 两个 Tab 按钮：
- 步骤 1-2：默认激活「字段」Tab
- 步骤 3：默认激活「API」Tab
- 步骤 4：默认激活「字段」Tab
- 用户可随时手动点击切换
- 激活态：`colors.surface` 背景，`colors.primary` 文字，底部 2px 实线
- 未激活：`colors.bg` 背景，`colors.textDim` 文字

### 7.7 步骤 5：API 就绪 (ApiConfigStep)

全屏展示，居中布局，上下排列。

```
        🎉 (48px)
    API 已就绪 (20px 700)
  您的自定义文档提取 API 已生成 (13px textMuted)

  ┌─ API 编码 ─────────────────── [复制] ─┐
  │  EXT-INV-A3B7C2                       │   (16px 700 primary, monospace)
  └───────────────────────────────────────┘
  ┌─ Endpoint ─────────────────── [复制] ─┐
  │  https://api.apianything.com/v1/      │   (12px accent, monospace)
  │       extract/EXT-INV-A3B7C2          │
  └───────────────────────────────────────┘
  ┌─ cURL 示例 ───────────────────────────┐
  │  curl -X POST "..." \                 │   (11px #a5f3fc, 代码块)
  │    -H "Authorization: Bearer ..." \   │
  │    -F "file=@invoice.pdf"             │
  └───────────────────────────────────────┘
  ┌─ Python 示例 ─────────────────────────┐
  │  import requests                      │   (11px #a5f3fc, 代码块)
  │  response = requests.post(...)        │
  └───────────────────────────────────────┘

  [ 下载 SDK 配置文件 ]   ← 渐变按钮 (primary → accent)
```

每个卡片：`colors.surface` 背景，`colors.border` 边框，padding 16px，圆角 8px。
[复制] 按钮点击后变为 "✓ 已复制"（2秒后恢复）。

### 7.8 三栏联动机制

**原型阶段实现**（步骤 1-4 共享）：

| 操作 | A 栏响应 | B 栏响应 | C 栏响应 |
|------|---------|---------|---------|
| 点击 A 栏 bbox | 该框高亮+选中态 | 滚动到对应字段并选中 | 对应 JSON path 高亮 |
| 点击 B 栏字段行 | 对应 bbox 高亮+选中态 | 该行选中背景 | 对应 JSON path 高亮 |
| 点击空白/Escape | 所有框恢复默认 | 取消选中 | 取消高亮 |
| 编辑 B 栏值 | — | 实时更新 | C 栏 JSON 实时同步 |
| 拖拽 A 栏 bbox | 位置实时更新 | 位置信息实时更新 | — |

**联动实现**：统一由 `workspace-store.ts` 的 `selectedId` 状态驱动。所有组件订阅 `selectedId`，自行决定渲染状态。

### 7.9 步骤条 (StepIndicator) 组件

水平排列 6 个步骤节点，节点间以线段连接。

```
已完成步骤: 圆形 ✓ 绿色(success) 背景
当前步骤:   圆形 序号 靛蓝(primary) 背景
未来步骤:   圆形 序号 透明背景 + border(border色)
连接线:     已完成段 success 色，未完成段 border 色

圆形尺寸: 28×28px, 序号 13px 600
步骤名称: 13px, 当前步骤 600 + text色, 非当前 400 + textDim色
```

### 7.10 顶栏 (TopBar) 组件

```
┌──────────────────────────────────────────────────────────┐
│  ⚡ ApiAnything  [Prototype]          [← 上一步] [下一步 →] │
└──────────────────────────────────────────────────────────┘

- 左侧: ⚡ emoji(20px) + "ApiAnything"(16px 700 letterSpacing -0.5) + Prototype 标签(10px textDim, primaryBg 背景, 圆角 4px)
- 右侧: 导航按钮仅在步骤 1-4 显示
  - [← 上一步]: border(border色), 透明背景, textMuted 文字
  - [下一步 →]: primary 背景, 白色字, 600
  - 步骤4时下一步文字变为 "确认生成 API"
- 背景: colors.surface, 底部 border
- padding: 10px 20px
```

### 7.11 底栏 (VersionBar) 组件

```
┌──────────────────────────────────────────────────────────┐
│  v1 ● v2 ○ v3 ○                       [保存并生成 API]   │
└──────────────────────────────────────────────────────────┘

- 左侧: 版本圆点列表，当前版本实心(●)，其他空心(○)，点击切换
- 右侧: [保存并生成 API] 按钮（渐变背景 primary → accent）
- 步骤 0 和步骤 5 时隐藏
```

### 7.12 状态管理 (workspace-store.ts)

```typescript
interface WorkspaceState {
  // 步骤
  currentStep: number;               // 0-5
  setStep: (step: number) => void;

  // 文档
  documentId: string | null;
  documentStatus: 'idle' | 'uploading' | 'processing' | 'completed' | 'failed';

  // 识别结果
  results: RecognitionField[];       // 字段列表（含 id, keyName, value, bbox, confidence）
  selectedId: number | null;         // 三栏联动选中 ID
  setSelectedId: (id: number | null) => void;
  updateFieldValue: (id: number, value: string) => void;
  updateFieldBbox: (id: number, bbox: BoundingBox) => void;
  addField: (field: NewField) => void;
  removeField: (id: number) => void;

  // 版本
  currentVersion: number;
  versions: VersionInfo[];

  // API 格式
  apiFormat: 'flat' | 'detailed' | 'grouped';
  setApiFormat: (format: string) => void;

  // API 定义
  apiCode: string | null;
}

interface RecognitionField {
  id: number;
  keyName: string;
  value: string;
  bbox: { x: number; y: number; w: number; h: number; page?: number };
  confidence: number;
  source: 'ai_detected' | 'manual';
  fieldType: 'string' | 'number' | 'date' | 'array';
}
```

### 7.13 前端依赖

| 库 | 版本 | 用途 |
|----|------|------|
| React 18 | ^18.2.0 | 框架 |
| Vite | ^5.x | 构建工具 |
| React Router | ^6.x | 路由（仅 3 个页面） |
| TailwindCSS | ^3.x | 样式 |
| zustand | ^4.x | 状态管理 |
| react-pdf | ^7.x | PDF 渲染（A 栏） |
| react-json-view-lite | ^1.x | JSON 展示（C 栏） |
| fetch + EventSource | 原生 | API 调用 + SSE |

---

## 8. 引擎层代码移植

### 8.1 从旧项目移植（无改动/微改动）

| 源文件 (label-studio-ml-backend/invoice_extractor/) | 目标 (backend/app/engine/) | 行数 | 改动 |
|-----|------|------|------|
| `processors/base.py` | `processors/base.py` | 14 | 无改动 |
| `processors/factory.py` | `processors/factory.py` | 87 | 去掉 LS 依赖（约 10%） |
| `processors/gemini.py` | `processors/gemini.py` | 277 | 无改动 |
| `processors/openai.py` | `processors/openai.py` | 441 | 无改动 |
| `processors/piaozone.py` | `processors/piaozone.py` | 407 | 无改动 |
| `processors/mock.py` | `processors/mock.py` | 20 | 无改动 |
| `config/manager.py` | `config/manager.py` | 391 | 去掉 IP 白名单（约 20%） |
| `config/models.yaml` | `config/models.yaml` | 159 | 无改动 |
| `analyzers/excel_analyzer.py` | `analyzers/excel.py` | 288 | 无改动 |

**零风险**：处理器仅依赖 `google-genai` 和 `openai` SDK，不依赖 Label Studio。

### 8.2 新增引擎模块

**correction.py** — 对话矫正引擎（核心新增）：
- 解析用户自然语言指令（如 "Payment Method 应该是 Wire Transfer"）
- 根据指令类型分发：
  - 字段值修改 → 直接更新 structured_data
  - Schema 调整 → 修改 inferred_schema + prompt_template
  - 格式变更 → 重组输出结构
- 调用 LLM 理解意图 → 生成修改后的 Prompt → 调用处理器重新提取
- 输出新版本 ProcessingResult

**schema_generator.py** — Schema 推断与动态生成：
- 从 AI 返回的 JSON 自动推断 JSON Schema
- 类型检测（string/number/date/boolean/array/object）
- 嵌套数组支持（如 `detailOfGoodsOrServices` 为对象数组）
- 字段描述自动生成

### 8.3 原型不做（后续扩展）

- `region_ocr.py` — 画框专项 OCR + Prompt 自优化（auto-research 最多 3 轮）
- `prompt_optimizer.py` — Prompt 自动优化循环
- `highlight.py` — 字段逆向定位算法（fuse.js 模糊匹配，前端实现）

---

## 9. 核心流程

### 流程 1：上传文档 → AI 提取

```
前端 → POST /api/v1/documents/upload (multipart)

后端 DocumentService.upload():
  1. storage.save(file) → 本地存储
  2. 创建 Document(status=processing)
  3. task_runner.run(process_document):
       processor = factory.create(processor_key)
       result = processor.extract(file_bytes)
       schema = schema_generator.infer(result)
  4. 创建 ProcessingResult(version=1, structured_data, inferred_schema, prompt_used)
  5. 从 structured_data 中每个字段创建 Annotation(source=ai_detected, confidence=xxx)
  6. Document.status = completed

返回 → Document + ProcessingResult + Annotations
```

### 流程 2：自然语言矫正（SSE）

```
前端 → POST /api/v1/documents/:id/conversation/messages (SSE)

后端 ConversationService.correct():
  1. 获取当前 ProcessingResult + 对话历史
  2. 创建 Message(role=user)
  3. correction.build_correction_prompt(用户指令, 当前 schema, 当前 prompt, 历史消息)
  4. 调用 LLM 流式返回 → SSE text_delta
  5. 解析 LLM 输出 → 新的 prompt + schema 修改
  6. processor.extract(file, new_prompt) → 新结果
  7. 创建 ProcessingResult(version=N+1)
  8. 创建 Message(role=assistant, result_version=N+1)
  9. SSE result_update 事件
  10. SSE done
```

### 流程 3：字段编辑 + 标注保存（双击编辑）

```
前端 → 双击字段行 → 编辑名称/值 → 拖拽 A 栏 bbox → 点击 ✓ 保存

后端:
  a. PATCH /api/v1/documents/:id/annotations/:field_id
     body: { field_name, field_value, bounding_box: {x,y,w,h} }
     → 更新 Annotation 记录
  b. POST /api/v1/documents/:id/results (或 PATCH)
     → 创建新 ProcessingResult(version=N+1, source=manual_edit)

两套数据独立更新:
  - Annotation (含 bbox) → 用于训练
  - ProcessingResult (字段值) → 用于 API 泛化
```

### 流程 4：添加新字段

```
前端 → 点击 [+ 添加识别字段] → 填写名称/值/类型 → 保存

后端:
  POST /api/v1/documents/:id/annotations
  body: { field_name, field_value, field_type, bounding_box(可选), source: "manual" }
  → 创建 Annotation(source=manual, confidence=null)
  → 创建新 ProcessingResult(version=N+1, source=manual_edit)

前端:
  B 栏新增一行（置信度标记为"手动"）
  C 栏 JSON 同步新增该字段
```

### 流程 5：生成 API

```
前端 → POST /api/v1/api-definitions
  body: { document_id, name, api_code, description }

后端 ApiDefinitionService.create():
  1. 获取文档最新 ProcessingResult
  2. 创建 ApiDefinition:
       schema_definition = result.inferred_schema
       prompt_template = result.prompt_used
       processor_key = document.processor_key
       status = active

返回 → ApiDefinition + 调用示例
```

### 流程 6：外部调用提取 API

```
POST /api/v1/extract/{api_code}
  Headers: X-API-Key: sk-xxxx

后端 ExtractService.extract():
  1. auth.authenticate(request)
  2. definition = 查询 ApiDefinition(api_code, status=active)
  3. storage.save(上传文件) → 临时存储
  4. processor = factory.create(definition.processor_key)
  5. result = processor.extract(file, prompt=definition.prompt_template, schema=definition.schema_definition)

返回 → { request_id, api_code, data, schema }
```

---

## 10. 工作台完整交互流程（按步骤）

> 此节汇总用户从打开页面到生成 API 的完整操作链路。

```
步骤 0 — 上传文档
  └→ 用户拖拽/点击上传 或 选择预置模板
  └→ 触发上传 → 显示 ProcessingOverlay（全屏加载动画）
  └→ AI 处理完成 → 自动跳到步骤 1

步骤 1 — AI 识别预览
  └→ 三栏同时填充:
       A 栏: 文档渲染 + 所有字段 bbox 高亮
       B 栏: 识别字段卡片列表（含置信度和位置）
       C 栏: 默认「详细」格式的 JSON 输出
  └→ 用户浏览确认结果 → 下一步

步骤 2 — 矫正结果
  └→ 用户可在 B 栏:
       • 直接编辑字段值（输入框）
       • 双击字段名编辑 keyName
       • 用自然语言矫正（顶部输入框）
       • 点击 [+ 添加识别字段] 添加 AI 遗漏的字段
  └→ 用户可在 A 栏:
       • 点击 bbox 选中字段（三栏联动）
       • 拖拽移动 bbox 位置
       • 拖拽手柄调整 bbox 大小
  └→ 每次修改 → 版本 +1（底栏版本条更新）
  └→ 满意后 → 下一步

步骤 3 — 调整 API 格式
  └→ 右侧 Tab 自动切换到「API」
  └→ C 栏显示: 格式切换按钮 [扁平] [详细] [分组]
  └→ 用户可用自然语言调整格式（如 "按供应商信息分组"）
  └→ 确认格式后 → 下一步

步骤 4 — 调试优化
  └→ A 栏顶部出现 [🔄 重新上传同类文档 (N 次调试)]
  └→ 用户上传新文档 → AI 用当前 Prompt 处理 → 对比结果
  └→ 可继续矫正，直到满意
  └→ 点击 "确认生成 API" → 跳到步骤 5

步骤 5 — 生成 API
  └→ 显示 API 就绪页:
       API 编码 + Endpoint + cURL + Python 示例
  └→ 每个代码块带 [复制] 按钮
  └→ [下载 SDK 配置文件] 按钮
```

---

## 11. 环境变量

```bash
# 数据库
DATABASE_URL=sqlite:///./data/apianything.db

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
CORS_ORIGINS=http://localhost:5173
```

---

## 12. 扩展路径

当内部原型验证可用后，按需逐步扩展：

| 阶段 | 扩展内容 | 改动范围 |
|------|---------|---------|
| **扩展 1** | SQLite → PostgreSQL | 改 DATABASE_URL |
| **扩展 2** | 本地存储 → S3 | 实现 S3Storage，改 STORAGE_BACKEND=s3 |
| **扩展 3** | 同步 → Celery 异步 | 实现 CeleryRunner + WebSocket 进度 |
| **扩展 4** | 画框矫正 + 高亮逆向定位 | 加 region_ocr + prompt_optimizer + fuse.js 匹配 + canvas 叠加 |
| **扩展 5** | 用户体系 + 多租户 | 加 User/Org 模型 + JWTAuth + 数据隔离 |
| **扩展 6** | 模板市场 | 加 Template 模型（parent_id 继承链）+ 前端模板页 |
| **扩展 7** | 计费 + 网关 | 加 UsageRecord + Kong/APISIX |

每个扩展独立，不互相依赖。

---

## 13. 重要约束

1. **引擎处理器代码不可修改**：`engine/processors/` 下的 gemini.py、openai.py、piaozone.py 是生产验证代码。
2. **Schema 跨模型兼容已内置**：处理器内部已处理 Gemini/OpenAI Schema 差异（`_normalize_schema()` + `_convert_gemini_schema_to_openai()`）。
3. **处理器运行时切换**：使用 `processor_type|model_name` 格式。
4. **API Key 安全**：`sk-` 前缀 + 32 字节 Base62，数据库只存 SHA-256，创建时仅返回一次明文。
5. **SSE 对话协议**：`text_delta` → `result_update` → `done` 三阶段。
6. **Prompt 泛化原则**：Prompt 不绑定特定文档坐标。Annotation 里的 bounding_box 仅用于前端高亮和训练数据，永远不写入 Prompt。
7. **标注与矫正独立**：双击编辑保存 Annotation（特定文档标注，含 bbox），自然语言矫正修改 ProcessingResult（泛化 Prompt + Schema）。两者数据模型独立。
8. **手动标注的训练价值**：用户通过 [+ 添加识别字段] 手动标注 AI 未识别的字段，这些标注（source=manual）在训练时权重更高，因为它们代表 AI 的能力盲区。
9. **数据逻辑加工规则写入 Prompt**：B 栏定义的校验规则（如 amount = qty × price）需写入 Prompt 让 AI 提取时同步校验，同时写入 ApiDefinition 在公有云调用时校验。
