# CLAUDE.md — ApiAnything 通用文档结构化数据提取 API 平台

> 本文件为 Claude Code 提供项目上下文和开发指引。
> 详细设计参见 `docs/UI_DESIGN.md` 和 `docs/API_DESIGN.md`。

---

## 项目定位

ApiAnything 是一个**通用文档结构化数据提取 API 平台**，客户上传任意文档（PDF、图片、Excel），通过 AI 提取结构化数据，并以自定义 API 的形式对外提供服务。

**核心用户旅程**：上传文档 → AI 生成预览数据 + 推荐 Schema → 自然语言矫正/调整 → 迭代调试 → 确认后生成 API 编码 → 通过 API-key + 编码调用公有云获取结果。

**项目来源**：从 Label Studio fork（标注工具）转型而来。AI 文档处理引擎（约 2,500 行）从旧项目 `label-studio-ml-backend/invoice_extractor/` 原样移植，其余全部重新开发。旧项目保留在 `label-studio/` 和 `label-studio-ml-backend/` 目录供参考，但不再维护。

---

## 技术栈

| 层 | 技术选型 | 说明 |
|---|---------|------|
| **前端** | Next.js 14+ (App Router) + TailwindCSS + shadcn/ui | SSR/SSG，管理控制台 + 对话式 UI |
| **后端** | FastAPI (Python 3.11+) | 异步、自动 OpenAPI 文档、与处理器代码无缝集成 |
| **数据库** | PostgreSQL 16 + SQLAlchemy 2.0 + Alembic | 异步 ORM，Alembic 做 migration |
| **缓存/队列** | Redis 7 + Celery | 缓存热数据、异步文档处理队列 |
| **文档存储** | S3 / MinIO | 客户上传的原始文档和处理结果 |
| **API 网关** | Kong / APISIX（Phase 3 引入） | 限流、鉴权、动态路由、监控 |
| **AI 引擎** | 移植自 invoice_extractor | Gemini / OpenAI / PiaoZone 处理器 |
| **实时通信** | WebSocket (处理进度) + SSE (对话流) | 见下方详述 |

### 前端关键依赖

| 库 | 用途 |
|----|------|
| `@tanstack/react-query` | 服务端状态管理（API 列表、模板等） |
| `zustand` | 客户端状态管理（字段、高亮、UI 状态） |
| `react-hook-form` + `zod` | 表单验证，类型安全 |
| `pdfjs-dist` | PDF 文档渲染 |
| `@monaco-editor/react` | JSON / Schema 代码编辑器 |
| `react-diff-viewer` | Schema 变更 diff 对比 |
| `recharts` | 调用统计图表 |
| `framer-motion` | 动画过渡（高亮、diff） |
| `next-intl` | 国际化（中/英/日/韩） |
| `fabric.js` 或 `konva` | 文档预览 Canvas 画框/高亮叠加 |
| `fuse.js` | 字段值模糊匹配（逆向定位用） |

---

## 项目结构

```
ApiAnything/
├── frontend/                       # Next.js 前端应用
│   ├── app/                        # App Router 页面
│   │   ├── (auth)/                 # 登录 /login、注册 /register
│   │   ├── (dashboard)/            # 主控制台布局（顶部导航：工作台/API管理/设置）
│   │   │   ├── workspace/          # ⭐ 核心唯一页面：三栏布局（55%文档|30%字段|15%API结果）
│   │   │   │   └── page.tsx        #   承载上传/预览/矫正/生成API全部功能
│   │   │   ├── apis/               # API 管理
│   │   │   │   ├── page.tsx        #   API 卡片列表（状态/调用量/成功率）
│   │   │   │   └── [id]/page.tsx   #   API 详情（概览/Schema/文档/日志/版本 Tab）
│   │   │   └── settings/           # 设置（账户/密钥/用量/团队 四个 Tab）
│   │   └── layout.tsx
│   ├── components/
│   │   ├── ui/                     # shadcn/ui 基础组件
│   │   ├── chat/                   # ChatPanel, MessageBubble, StreamingText
│   │   ├── document/               # DocumentPreview, HighlightOverlay, DrawingTool
│   │   ├── schema/                 # FieldEditor, FieldGroup, ValidationRules, SchemaDiff
│   │   ├── api/                    # JsonPreview, CodeSnippet, ApiCard
│   │   └── modal/                  # TemplateModal, GenerateApiModal, PlaygroundModal
│   ├── lib/
│   │   ├── api-client.ts           # 后端 API 封装（fetch + auth interceptor）
│   │   ├── sse.ts                  # SSE 流式响应处理
│   │   ├── websocket.ts            # WebSocket 连接管理
│   │   └── highlight.ts            # 字段逆向定位 + 模糊匹配算法
│   └── stores/                     # Zustand stores
│       ├── workspace-store.ts      # 字段定义、高亮映射、JSON 输出
│       ├── highlight-store.ts      # 字段 → 文档位置映射（三栏联动）
│       └── auth-store.ts           # 认证状态
│
├── backend/                        # FastAPI 后端
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口、中间件、路由挂载
│   │   ├── core/
│   │   │   ├── config.py           # Pydantic BaseSettings
│   │   │   ├── security.py         # JWT 编解码、密码哈希、API Key 验证
│   │   │   ├── deps.py             # get_db, get_current_user, get_org, get_api_key_auth
│   │   │   └── exceptions.py       # AppException 体系 + 全局异常处理器
│   │   ├── models/                 # SQLAlchemy ORM（9 个核心模型）
│   │   │   ├── user.py             # User + Organization
│   │   │   ├── document.py         # Document + ProcessingResult
│   │   │   ├── conversation.py     # Conversation + Message
│   │   │   ├── api_definition.py   # ApiDefinition
│   │   │   ├── api_key.py          # ApiKey
│   │   │   ├── template.py         # Template（支持 parent_id 继承）
│   │   │   ├── prompt_version.py   # PromptVersion
│   │   │   └── usage.py            # UsageRecord
│   │   ├── schemas/                # Pydantic Request/Response
│   │   ├── api/v1/                 # 路由层（薄层，只做参数校验 + 调用 Service）
│   │   │   ├── auth.py             # 注册/登录/刷新/改密
│   │   │   ├── documents.py        # 上传/列表/预览/重处理
│   │   │   ├── conversations.py    # 对话/消息(SSE)/Schema/回滚
│   │   │   ├── api_defs.py         # API 定义 CRUD + 状态管理
│   │   │   ├── templates.py        # 模板列表/详情/使用/Fork/评分
│   │   │   ├── api_keys.py         # 密钥 CRUD + 轮换
│   │   │   ├── extract.py          # ⭐ 公有云提取端点 POST /extract/{api_code}
│   │   │   ├── usage.py            # 用量统计 + 日志
│   │   │   └── router.py           # 路由汇总
│   │   ├── services/               # 业务逻辑层
│   │   │   ├── auth_service.py
│   │   │   ├── document_service.py # 上传 → S3 → 创建记录 → 派发 Celery 任务
│   │   │   ├── conversation_service.py # 矫正对话 + LLM 交互 + Schema 更新
│   │   │   ├── api_generation_service.py # 从 Conversation 创建 ApiDefinition
│   │   │   ├── template_service.py # 模板 CRUD + 继承逻辑
│   │   │   ├── extract_service.py  # 公有云调用：鉴权 → 加载定义 → 调用引擎
│   │   │   └── billing_service.py  # 用量记录 + 配额检查
│   │   ├── engine/                 # ⭐ AI 文档处理引擎（核心，大部分移植自旧项目）
│   │   │   ├── processors/
│   │   │   │   ├── base.py         # DocumentProcessor 抽象基类
│   │   │   │   ├── factory.py      # ProcessorFactory（processor_type|model_name）
│   │   │   │   ├── gemini.py       # Gemini 处理器（277行，原样移植）
│   │   │   │   ├── openai.py       # OpenAI 处理器（441行，原样移植）
│   │   │   │   ├── piaozone.py     # PiaoZone 处理器（407行，原样移植）
│   │   │   │   └── mock.py         # Mock 处理器（测试用）
│   │   │   ├── config/
│   │   │   │   ├── manager.py      # ConfigManager（391行，移植去掉 IP 白名单）
│   │   │   │   └── models.yaml     # 模型配置（159行，原样移植）
│   │   │   ├── analyzers/
│   │   │   │   └── excel.py        # ExcelAnalyzer（288行，原样移植）
│   │   │   ├── correction.py       # ⭐ 自然语言矫正引擎（新增）
│   │   │   └── schema_generator.py # ⭐ Schema 推断与动态生成（新增）
│   │   ├── tasks/                  # Celery 异步任务
│   │   │   ├── document_tasks.py   # process_document_task（带重试，max_retries=3）
│   │   │   ├── correction_tasks.py # reprocess_with_correction_task
│   │   │   └── billing_tasks.py    # aggregate_daily_usage（定时任务）
│   │   └── utils/
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
│
├── docker/
│   ├── docker-compose.yml          # 生产部署
│   ├── docker-compose.dev.yml      # 开发环境（含 PostgreSQL + Redis + MinIO）
│   ├── Dockerfile.frontend
│   └── Dockerfile.backend
│
├── docs/
│   ├── UI_DESIGN.md                # 完整 UI 设计文档（页面/组件/交互/设计系统）
│   ├── API_DESIGN.md               # 完整 API 设计文档（端点/模型/安全/异步任务）
│   └── MIGRATION_GUIDE.md          # 引擎代码移植指南
│
├── label-studio/                   # ⚠️ 旧项目 — 仅供参考，不再维护
├── label-studio-ml-backend/        # ⚠️ 旧项目 — engine 代码从此处移植
├── TECHNICAL_ASSESSMENT_REPORT.md  # 技术评估报告（决策依据）
└── CLAUDE.md                       # 本文件
```

---

## 核心数据模型

### 实体关系概览

```
Organization ──1:N── User
Organization ──1:N── ApiDefinition
Organization ──1:N── ApiKey
Organization ──1:N── UsageRecord

User ──1:N── Document ──1:N── ProcessingResult
User ──1:N── Conversation ──1:N── Message
Document ──1:1── Conversation

ApiDefinition ──N:1── PromptVersion（当前版本）
ApiDefinition ──N:1── Template（基于模板，可选）
Template ──self── Template（parent_id 继承链）
```

### 关键字段

| 模型 | 关键字段 | 说明 |
|------|---------|------|
| **Document** | `status`: uploading → queued → processing → completed / failed | 文档处理状态机 |
| **ProcessingResult** | `version`: int, `structured_data`: JSON, `inferred_schema`: JSON | 每次矫正产生新版本 |
| **ApiDefinition** | `api_code`: 唯一编码, `status`: draft → active → deprecated | 对外 API 标识 |
| **ApiKey** | `key_hash`: SHA-256, `key_prefix`: "sk-...abc", `rate_limit`: int | 仅创建时返回明文 |
| **Template** | `parent_id`: 继承, `base_schema` + `base_prompt`: 预置内容 | 子模板覆盖父模板字段 |
| **PromptVersion** | `source`: user_correction / system_init / manual_edit | 追踪 Prompt 变更来源 |
| **Annotation** | `field_name`, `field_value`, `bounding_box`, `source`: ai_detected / manual | 文档标注数据（训练用） |

详见 `docs/API_DESIGN.md` 第二节完整 SQLAlchemy 模型定义。

---

## API 端点速查

### 管理 API（JWT 认证，前端控制台使用）

| 模块 | 端点 | 说明 |
|------|------|------|
| 认证 | `POST /api/v1/auth/register, login, refresh` | 注册、登录、刷新 Token |
| 文档 | `POST /api/v1/documents/upload` | 上传文档（multipart） |
| 文档 | `GET /api/v1/documents/:id/results` | 处理结果列表（多版本） |
| 文档 | `POST /api/v1/documents/:id/region-ocr` | 画框专项 OCR + Prompt 自优化 |
| 文档 | `GET /api/v1/documents/:id/highlights` | 字段逆向定位（字段→文档区域映射） |
| 标注 | `POST /api/v1/documents/:id/annotations` | 新增字段标注（手动添加字段） |
| 标注 | `PATCH /api/v1/documents/:id/annotations/:fid` | 更新字段标注（编辑字段名/值/区域） |
| 标注 | `GET /api/v1/documents/:id/annotations` | 获取文档所有标注数据 |
| 对话 | `POST /api/v1/conversations/:id/messages` | 发送矫正指令（**SSE 流式响应**） |
| 对话 | `POST /api/v1/conversations/:id/rollback/:ver` | 回滚到指定版本 |
| API定义 | `POST /api/v1/api-definitions` | 从对话创建 API |
| API定义 | `PATCH /api/v1/api-definitions/:id/status` | 激活/废弃 |
| 模板 | `GET /api/v1/templates` | 模板列表（分类+语言筛选） |
| 模板 | `POST /api/v1/templates/:id/fork` | 复制并定制 |
| 密钥 | `POST /api/v1/api-keys` | 创建密钥（**仅返回一次明文**） |
| 用量 | `GET /api/v1/usage/summary` | 本月用量/配额概览 |

### 公有云 API（API Key 认证，客户系统调用）

```
POST /api/v1/extract/{api_code}
  Headers: X-API-Key: sk-xxxx
  Body: multipart/form-data (file) 或 JSON (file_url / file_base64)
  Response: { request_id, api_code, data: {...}, metadata: {...} }
```

### 实时通信

| 类型 | 端点 | 用途 |
|------|------|------|
| WebSocket | `/api/v1/ws/documents/:id/status` | 文档处理进度推送 |
| SSE | `POST /api/v1/conversations/:id/messages` | 对话矫正流式响应 |

### 统一分页约定

所有列表接口：`?page=1&page_size=20&sort_by=created_at&sort_order=desc`

### 错误码（公有云 API）

| HTTP | code | 说明 |
|------|------|------|
| 401 | `invalid_api_key` | 密钥无效 |
| 404 | `api_not_found` | api_code 不存在 |
| 413 | `file_too_large` | 超过 20MB |
| 415 | `unsupported_file_type` | 不支持的格式 |
| 422 | `processing_error` | AI 处理失败 |
| 429 | `rate_limit_exceeded` / `quota_exceeded` | 限流/超额 |

详见 `docs/API_DESIGN.md` 完整端点设计。

---

## 前端页面架构

### 设计哲学

**单页核心，弹窗辅助**：整个产品以「工作台」为唯一核心页面。上传文档、矫正字段、调整 API 格式、调试优化全部在同一页面内完成。模板选择、API 生成、沙箱调试等辅助流程通过按钮+弹窗承载，用户无需跳转页面。

**字段驱动，逆向定位**：不使用传统「框选图像区域 → 提取内容」，而是「AI 提取字段 → 逆向定位到文档区域高亮」。

**Prompt 泛化，不存位置**：用户画框矫正时触发专项 OCR，结果用于优化 Prompt。系统只保留 Prompt 方法，不保留「坐标→内容」的硬编码，确保识别能力可泛化到同类文档。

### 页面总览

| 页面 | 路径 | 核心功能 |
|------|------|---------|
| **工作台** | `/workspace` | ⭐ 核心唯一页面。三栏：A=文档预览(55%) / B=字段定义与矫正(30%) / C=API结果(15%)。顶部按钮触发弹窗（模板库/生成API/沙箱调试） |
| **API 管理** | `/apis` | 已生成 API 卡片列表 → 详情页（概览/Schema/文档/日志/版本） |
| **设置** | `/settings` | 账户/密钥/用量/团队 四个 Tab |

### 工作台三栏交互流程（最重要）

```
1. 用户点击 [上传文档] 或 [模板库] 按钮
   → 上传后: uploading（进度条）→ queued → processing（spinner）
   → 模板: 预填 Schema + Prompt 到 B 栏，等待上传文档

2. AI 处理完成 → 三栏同时填充
   → A 栏: 文档原文渲染 + 字段高亮叠加层（逆向定位）
   → B 栏: 分组卡片式字段编辑器（支持数组/嵌套），底部 AI 对话区
   → C 栏: 实时 JSON 输出 + Schema 验证状态 + 代码示例

3. 三栏联动（单击选中）
   → 单击 B 栏字段行 → A 栏高亮对应文档区域（彩色半透明叠加）+ C 栏高亮对应 JSON 行
   → 单击 C 栏 JSON key → A 栏高亮 + B 栏字段高亮
   → 单击 A 栏高亮区域 → B 栏滚动到对应字段并选中
   → 其余未选中的高亮区域自动 dim（降低透明度），突出当前焦点
   → Escape 键取消选中，恢复所有高亮为默认状态

4. 双击编辑 + 标注保存（B 栏核心交互）
   → 双击 B 栏字段名称或识别结果 → 进入行内编辑模式：
     • 字段名称、字段值均变为可编辑（contenteditable）
     • 该行右侧出现 ✓ 保存图标按钮
     • A 栏：对应高亮区域进入"可编辑模式"（显示四角拖拽手柄），
       用户可拖拽移动高亮框位置，或拖拽手柄调整框的大小
     • C 栏：对应 JSON 行高亮，字段名/格式可联动修改
   → 点击 ✓ 保存 → 调用后端标注保存 API → 更新 ProcessingResult 的标注数据
   → 标注数据用于后续模型训练优化（存入 Annotation 模型）

5. 添加新识别字段（B 栏顶部 "+" 按钮）
   → B 栏字段区域上方有 [+ 添加识别字段] 按钮
   → 点击展开行内表单：字段名称 | 识别结果值 | 类型(string/number/date/array) | 保存/取消
   → 保存后：
     • B 栏新增一行字段（置信度标记为"手动"）
     • C 栏 JSON 输出同步新增该字段
     • 调用后端 API 保存标注（新增字段记录，备后续训练）
   → 用户也可在 A 栏画框标注新字段的文档区域

6. 自然语言矫正（底部 AI 对话区）
   → 输入指令 → AI 流式回复（SSE）→ B 栏字段 diff 动画
   → 版本号递增（v1 → v2 → v3），底部会话栏可切换

7. 画框辅助矫正（A 栏画框工具）
   → 用户框选区域 → 专项 OCR → 浮窗：新增字段 / 矫正现有 / 辅助信息
   → 触发 Prompt 自优化循环（最多 3 轮 auto-research）
   → 仅保存最终 Prompt，不保存坐标

8. 确认生成 API → 点击 [保存并生成API] 按钮
   → 弹窗三步确认：命名 → Schema → 密钥
   → 生成成功后显示 endpoint + 代码
```

### 字段精细化设计（B 栏核心能力）

B 栏支持复杂嵌套数据结构的可视化编辑：
- **多行货物明细**：数组类型，每行展开显示 {行号, 货物名称, 规格型号, 数量, 单价, 金额, 税额}
- **税额汇总**：数组 + 合计行，合计字段显示公式引用（🔗 SUM）
- **数据逻辑加工规则**：amount = qty × price、校验容差 ±0.01 等，写入 Prompt 和 ApiDefinition
- **字段操作**：修改值/类型、增删字段、重命名、拖拽调整嵌套层级、管理数组行
- **置信度显示**：每个字段右侧显示 AI 识别置信度百分比 + 进度条；手动添加的字段标记为"手动"

### 标注编辑与训练数据（B 栏编辑模式）

标注编辑是连接「AI 识别」与「持续训练优化」的桥梁：

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
  → 展开行内表单: 名称 | 值 | 类型 | 保存/取消
  → 保存后调用 POST /api/v1/documents/:id/annotations
  → B 栏新增行（置信度="手动"），C 栏 JSON 同步更新
```

**重要区分**：标注编辑保存的是**特定文档的标注数据**（Annotation），用于训练。而自然语言矫正修改的是**Prompt 和 Schema**，用于泛化。两者互不干扰。

### 逆向定位机制

```
AI 返回 JSON → OCR 全文 + 坐标 → 用字段值模糊匹配(fuse.js)
  → 匹配成功: 记录 bounding_box → 前端 SVG 高亮
  → 匹配失败(计算值): 标记 "derived"，不显示高亮
位置信息仅前端使用，不写入 Prompt，不影响泛化
```

### 状态管理策略

| 数据类型 | 方案 |
|---------|------|
| 服务端数据（API列表、模板等） | React Query（TanStack Query） |
| 对话消息流 | React Query + SSE |
| 字段定义 + 高亮映射 | Zustand (workspace-store + highlight-store) |
| 表单 | React Hook Form + Zod |

详见 `docs/UI_DESIGN.md` 完整设计。

---

## 架构分层与依赖规则

```
前端 (Next.js)
  ↕ HTTP/WS/SSE
后端 (FastAPI)
  api/v1/  →  services/  →  engine/ + models/
                  ↓
               tasks/（Celery 异步调用 engine）
```

**严格单向依赖**：
- `api/v1/` 路由层只调用 `services/`，不直接调用 `engine/` 或 `models/`
- `services/` 调用 `engine/`（AI 处理）和 `models/`（数据库）
- `engine/` 完全独立，不依赖 `services/` 或 `api/`
- `tasks/` 调用 `services/` 或直接调用 `engine/`

**禁止**：路由层直接访问数据库、服务层之间循环依赖、引擎层依赖业务层。

### 多租户隔离

所有数据查询通过 FastAPI Dependency `get_current_org()` 自动注入 `organization_id` 过滤。API Key 绑定到 Organization，不能跨租户。

---

## 引擎层代码移植

从 `label-studio-ml-backend/invoice_extractor/` 移植到 `backend/app/engine/`。

| 源文件 | 目标 | 改动 |
|--------|------|------|
| `processors/base.py` | `engine/processors/base.py` | 无改动 |
| `processors/factory.py` | `engine/processors/factory.py` | 去掉 LS 依赖（约 10%） |
| `processors/gemini.py` (277行) | `engine/processors/gemini.py` | 无改动 |
| `processors/openai.py` (441行) | `engine/processors/openai.py` | 无改动 |
| `processors/piaozone.py` (407行) | `engine/processors/piaozone.py` | 无改动 |
| `processors/mock.py` | `engine/processors/mock.py` | 无改动 |
| `config/manager.py` (391行) | `engine/config/manager.py` | 去掉 IP 白名单（约 20%） |
| `config/models.yaml` (159行) | `engine/config/models.yaml` | 无改动 |
| `analyzers/excel_analyzer.py` (288行) | `engine/analyzers/excel.py` | 无改动 |

**零风险**：处理器仅依赖 `google-genai` 和 `openai` SDK，不依赖 Label Studio。

**新增引擎模块**（需从零开发）：
- `engine/correction.py` — 自然语言矫正引擎：解析用户指令 → 修改 Schema + Prompt → 调用处理器重新提取
- `engine/schema_generator.py` — Schema 推断：从 AI 返回的 JSON 自动推断 JSON Schema，支持类型检测、嵌套数组、字段描述
- `engine/region_ocr.py` — 画框专项 OCR：接收用户框选区域坐标 → 裁切文档图像 → OCR 提取文本 → 返回结构化结果
- `engine/prompt_optimizer.py` — Prompt 自优化引擎（auto-research）：框选矫正触发 → 最多 3 轮迭代（生成Prompt → 重新处理 → 对比 → 自我反思调整）→ 仅保存最终 Prompt

---

## 环境变量

```bash
# === 数据库 ===
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/apianything

# === Redis ===
REDIS_URL=redis://localhost:6379/0

# === 文档存储 ===
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=documents

# === AI 处理器（从旧项目沿用） ===
GEMINI_API_KEY=xxx              # Gemini API 密钥
OPENAI_API_KEY=xxx              # OpenAI API 密钥
DEFAULT_PROCESSOR=gemini        # 默认处理器
DEFAULT_MODEL=gemini-2.5-flash  # 默认模型

# === 安全 ===
JWT_SECRET_KEY=xxx              # JWT 签名密钥
API_KEY_SALT=xxx                # API Key 哈希盐值

# === 应用 ===
APP_ENV=development             # development | staging | production
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:3000  # 前端地址
```

---

## 常用命令

```bash
# === 后端 ===
cd backend
pip install -e ".[dev]"                 # 安装开发依赖
uvicorn app.main:app --reload           # 启动后端（开发模式，热重载）
alembic upgrade head                    # 执行数据库迁移
alembic revision --autogenerate -m ""   # 生成新迁移脚本
celery -A app.tasks worker --loglevel=info  # 启动 Celery Worker
pytest                                  # 运行测试
pytest --cov=app tests/                 # 测试 + 覆盖率报告

# === 前端 ===
cd frontend
pnpm install                            # 安装依赖
pnpm dev                                # 启动前端（开发模式）
pnpm build                              # 构建生产版本
pnpm lint                               # Lint 检查
pnpm type-check                         # TypeScript 类型检查

# === Docker 全栈 ===
docker compose -f docker/docker-compose.dev.yml up      # 开发环境（含 PG+Redis+MinIO）
docker compose -f docker/docker-compose.yml up -d       # 生产部署
```

---

## 编码规范

### 后端 (Python)

- **类型标注**：所有函数签名必须有完整类型标注，使用 `from __future__ import annotations`
- **异步优先**：数据库查询用 `AsyncSession`，外部 API 调用用 `httpx.AsyncClient`
- **分层架构**：Router（参数校验）→ Service（业务逻辑）→ Engine/Repository（AI处理/数据访问）
- **错误处理**：业务异常继承 `AppException`，统一返回 `{"error": {"code": "...", "message": "..."}}`
- **命名**：snake_case（函数/变量），PascalCase（类），UPPER_SNAKE_CASE（常量）
- **测试**：每个 Service 方法至少一个 happy path + 一个 error path 测试

### 前端 (TypeScript)

- **严格模式**：`tsconfig.json` 开启 `strict: true`
- **组件**：函数式组件 + hooks，不使用 class 组件，单文件不超过 300 行
- **状态管理**：Server State → React Query，Client State → Zustand，表单 → React Hook Form + Zod
- **样式**：TailwindCSS utility-first，不写自定义 CSS 文件（除非动画需要）
- **命名**：camelCase（函数/变量），PascalCase（组件/类型），kebab-case（文件名）
- **API 调用**：统一通过 `lib/api-client.ts` 封装，自动携带 JWT、处理 401 刷新

---

## 分阶段里程碑

| Phase | 内容 | 周期 | 关键交付 |
|-------|------|------|---------|
| **Phase 1** | 核心引擎 + 文档上传 + JSON 预览 | 4-6w | 上传文档 → 返回结构化 JSON 的最小可用系统 |
| **Phase 2** | 自然语言矫正 + Schema 管理 + 对话 UI | 4-6w | 对话式修改 Schema + Prompt + 版本管理 |
| **Phase 3** | API 生成 + 网关 + 模板市场 | 4-6w | 客户生成自定义 API 并通过公有云调用 |
| **Phase 4** | 计费 + 多租户 + 监控 + SDK | 3-4w | 可对外商业化的完整平台 |

---

## 重要注意事项

1. **引擎处理器代码不可随意修改**：`engine/processors/` 下的 gemini.py、openai.py、piaozone.py 是经过生产验证的代码，除非修复 bug，不要改动。

2. **Schema 跨模型兼容已内置**：Gemini 和 OpenAI 的 JSON Schema 格式差异已在处理器内部处理（`_normalize_schema()` + `_convert_gemini_schema_to_openai()`），不要在外层重复实现。

3. **处理器运行时切换**：使用 `processor_type|model_name` 格式（如 `gemini|gemini-2.5-flash`），这个模式从旧系统验证过，新系统沿用。

4. **模板继承规则**：子模板通过 `parent_id` 关联父模板。子模板的 `base_schema` 和 `base_prompt` 若有值则覆盖父模板，若为 null 则继承父模板值。查询时需递归解析继承链。

5. **API Key 安全**：`sk-` 前缀 + 32 字节随机 Base62。数据库只存 SHA-256 哈希。创建接口仅返回一次明文，前端需提示用户保存。

6. **SSE 对话协议**：矫正消息的流式响应使用 SSE，事件类型包括 `message_start` → `text_delta`(多次) → `schema_update` → `result_update` → `message_end`。前端需按事件类型分别更新对话区和 Schema 区。

7. **文档处理是异步的**：上传后通过 Celery 异步处理，前端通过 WebSocket 监听进度。不要在 HTTP 请求中同步等待处理完成。

8. **所有 API 路径以 `/api/v1/` 开头**：为后续版本预留空间。公有云提取端点 `/api/v1/extract/{api_code}` 与管理 API 共用前缀但使用不同认证（API Key vs JWT）。

9. **逆向定位只在前端使用**：字段 → 文档区域的高亮映射（bounding_box）仅在前端渲染时计算和使用，不写入 PromptVersion，不写入 ApiDefinition。这确保 Prompt 可泛化到任意同类型文档，不绑定特定文档的坐标。

10. **画框矫正的 Prompt 自优化**：用户画框触发专项 OCR 后，系统自动执行最多 3 轮 auto-research 循环（生成 Prompt → 重新处理 → 对比 → 反思调整）。最终只保存 Prompt 文本，不保存框选坐标、OCR 中间结果。这是识别能力泛化的关键。

11. **数据逻辑加工规则写入 Prompt**：B 栏定义的校验规则（如 amount = qty × price）不仅在前端校验，还需写入 Prompt 让 AI 在提取时同步校验，同时写入 ApiDefinition 在公有云调用时自动校验。

12. **标注数据与 Prompt 矫正是两套独立机制**：双击编辑字段保存的是 Annotation（特定文档的标注，含 bounding_box，用于训练）。自然语言矫正修改的是 PromptVersion（泛化 Prompt + Schema，用于 API）。前者关注「这份文档」，后者关注「这类文档」。两者数据模型独立，互不影响。

13. **标注编辑时的三栏联动**：双击字段进入编辑模式后，A 栏高亮区域变为可拖拽/可缩放（显示四角手柄），C 栏对应 JSON 行高亮。保存时同时提交字段名、字段值、bounding_box 三项数据。这是 Label Studio 式标注能力在新系统中的继承。

14. **手动添加字段的训练价值**：用户通过 [+ 添加识别字段] 手动标注 AI 未识别到的字段，这些标注（source=manual）在训练时权重更高，因为它们代表 AI 的能力盲区。
