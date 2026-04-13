# ApiAnything API 设计文档

> 版本：v1.0 | 日期：2026-04-02
> 技术栈：FastAPI (Python 3.11+) + PostgreSQL + Redis + S3

---

## 一、API 总览

所有内部管理 API 以 `/api/v1/` 为前缀，公有云文档提取 API 以 `/api/v1/extract/` 为前缀。

### 认证方式

| 场景 | 认证方式 | Header |
|------|---------|--------|
| 管理控制台（前端） | JWT Bearer Token | `Authorization: Bearer <token>` |
| 公有云 API 调用 | API Key | `X-API-Key: <key>` |

---

## 二、数据模型（SQLAlchemy ORM）

### 2.1 核心实体

```python
# === 租户与用户 ===

class Organization(Base):
    __tablename__ = "organizations"

    id: UUID                    # 主键
    name: str                   # 组织名称
    slug: str                   # URL 友好标识（唯一）
    plan: str                   # free | pro | enterprise
    monthly_quota: int          # 月度 API 调用配额
    created_at: datetime
    updated_at: datetime

class User(Base):
    __tablename__ = "users"

    id: UUID
    email: str                  # 唯一
    hashed_password: str
    name: str
    role: str                   # owner | admin | member
    organization_id: UUID       # FK → organizations
    is_active: bool
    last_login_at: datetime
    created_at: datetime

# === 文档与处理 ===

class Document(Base):
    __tablename__ = "documents"

    id: UUID
    user_id: UUID               # FK → users
    organization_id: UUID       # FK → organizations
    filename: str               # 原始文件名
    file_type: str              # pdf | png | jpg | xlsx
    file_size: int              # 字节数
    s3_key: str                 # S3 存储路径
    status: str                 # uploading | queued | processing | completed | failed
    error_message: str | None
    created_at: datetime

class ProcessingResult(Base):
    __tablename__ = "processing_results"

    id: UUID
    document_id: UUID           # FK → documents
    version: int                # 结果版本（矫正后版本递增）
    processor_type: str         # gemini | openai | piaozone
    model_name: str             # gemini-2.5-flash 等
    raw_output: dict            # AI 原始输出（JSON）
    structured_data: dict       # 结构化提取数据（JSON）
    inferred_schema: dict       # 自动推断的 JSON Schema
    tokens_used: int
    processing_time_ms: int
    prompt_version_id: UUID | None  # FK → prompt_versions
    created_at: datetime

# === 标注数据（训练用） ===

class Annotation(Base):
    __tablename__ = "annotations"

    id: UUID
    document_id: UUID           # FK → documents
    processing_result_id: UUID | None  # FK → processing_results（关联到哪个版本的结果）
    field_name: str             # 字段名（如 invoice_no、buyer_name）
    field_value: str | None     # 字段值（如 "04172872"）
    field_type: str             # string | number | date | array
    bounding_box: dict | None   # {page, x, y, width, height} 文档区域坐标
    source: str                 # ai_detected | manual（AI 识别 or 用户手动添加）
    is_corrected: bool          # 是否被用户修正过（AI 识别后用户编辑了值/区域）
    original_value: str | None  # 修正前的原始值（用于计算修正率）
    original_bbox: dict | None  # 修正前的原始区域
    created_by: UUID            # FK → users
    created_at: datetime
    updated_at: datetime

# === 对话与矫正 ===

class Conversation(Base):
    __tablename__ = "conversations"

    id: UUID
    user_id: UUID               # FK → users
    document_id: UUID           # FK → documents
    title: str                  # 自动生成或用户命名
    status: str                 # active | completed | archived
    current_schema: dict        # 当前 JSON Schema 快照
    created_at: datetime
    updated_at: datetime

class Message(Base):
    __tablename__ = "messages"

    id: UUID
    conversation_id: UUID       # FK → conversations
    role: str                   # user | assistant | system
    content: str                # 消息文本
    schema_diff: dict | None    # Schema 变更 diff（assistant 消息附带）
    prompt_version_id: UUID | None  # 关联的 Prompt 版本
    created_at: datetime

# === API 定义 ===

class ApiDefinition(Base):
    __tablename__ = "api_definitions"

    id: UUID
    organization_id: UUID       # FK → organizations
    user_id: UUID               # FK → users（创建者）
    name: str                   # API 显示名称
    api_code: str               # 唯一编码（如 inv-cn-vat-v1）
    description: str
    status: str                 # draft | active | deprecated
    response_schema: dict       # JSON Schema 定义
    prompt_version_id: UUID     # FK → prompt_versions（当前使用的 Prompt）
    processor_type: str         # 默认处理器类型
    model_name: str             # 默认模型
    config: dict                # 额外配置（temperature 等）
    template_id: UUID | None    # FK → templates（基于哪个模板）
    version: int                # API 版本号
    created_at: datetime
    updated_at: datetime

class ApiKey(Base):
    __tablename__ = "api_keys"

    id: UUID
    organization_id: UUID       # FK → organizations
    name: str                   # 密钥名称（如 "Production Key"）
    key_hash: str               # 哈希后的密钥（不存明文）
    key_prefix: str             # 前缀用于展示（如 "sk-...abc"）
    scopes: list[str]           # 权限范围 ["extract", "templates:read"]
    rate_limit: int             # 每分钟调用上限
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

# === 模板 ===

class Template(Base):
    __tablename__ = "templates"

    id: UUID
    name: str
    description: str
    category: str               # invoice | receipt | contract | customs | bank_statement | other
    language: str               # zh-CN | en-US | ja-JP | ko-KR
    base_schema: dict           # 默认 JSON Schema
    base_prompt: str            # 默认 Prompt
    sample_files: list[str]     # 示例文件 S3 keys
    parent_id: UUID | None      # FK → templates（继承）
    is_official: bool           # 官方 vs 社区
    usage_count: int            # 使用次数
    rating: float               # 平均评分
    created_by: UUID | None     # FK → users（社区模板）
    created_at: datetime
    updated_at: datetime

# === Prompt 版本 ===

class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: UUID
    api_definition_id: UUID | None  # FK → api_definitions
    version_number: int
    prompt_text: str            # 完整 Prompt 文本
    schema_snapshot: dict       # 当时的 Schema 快照
    change_description: str     # 变更说明
    source: str                 # user_correction | system_init | manual_edit
    created_by: UUID            # FK → users
    created_at: datetime

# === 用量记录 ===

class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: UUID
    organization_id: UUID       # FK → organizations
    api_definition_id: UUID     # FK → api_definitions
    api_key_id: UUID            # FK → api_keys
    document_id: UUID           # FK → documents
    status: str                 # success | error
    tokens_used: int
    processing_time_ms: int
    error_type: str | None
    error_message: str | None
    request_ip: str
    created_at: datetime
```

### 2.2 ER 关系图

```
Organization ──1:N── User
Organization ──1:N── ApiDefinition
Organization ──1:N── ApiKey
Organization ──1:N── UsageRecord
User ──1:N── Document
User ──1:N── Conversation
Document ──1:N── ProcessingResult
Document ──1:N── Annotation
Document ──1:1── Conversation
Conversation ──1:N── Message
ApiDefinition ──N:1── PromptVersion (current)
ApiDefinition ──N:1── Template (optional)
PromptVersion ──N:1── ApiDefinition
ProcessingResult ──N:1── PromptVersion (optional)
Message ──N:1── PromptVersion (optional)
Template ──self── Template (parent_id 继承)
```

---

## 三、API 端点设计

### 3.1 认证 (`/api/v1/auth`)

```
POST   /api/v1/auth/register          # 注册
POST   /api/v1/auth/login             # 登录 → 返回 JWT
POST   /api/v1/auth/refresh           # 刷新 Token
POST   /api/v1/auth/logout            # 登出（可选：服务端黑名单）
GET    /api/v1/auth/me                # 获取当前用户信息
PUT    /api/v1/auth/me                # 更新用户信息
POST   /api/v1/auth/change-password   # 修改密码
```

**关键请求/响应**：

```python
# POST /api/v1/auth/register
class RegisterRequest(BaseModel):
    email: str
    password: str               # 最少 8 位
    name: str
    organization_name: str      # 自动创建组织

class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int             # 秒
    user: UserResponse
```

### 3.2 文档管理 (`/api/v1/documents`)

```
POST   /api/v1/documents/upload       # 上传文档（multipart/form-data）
GET    /api/v1/documents              # 文档列表（分页、筛选）
GET    /api/v1/documents/:id          # 文档详情
GET    /api/v1/documents/:id/preview  # 获取文档预览 URL（S3 presigned）
GET    /api/v1/documents/:id/results  # 获取处理结果列表（多版本）
POST   /api/v1/documents/:id/reprocess  # 重新处理
DELETE /api/v1/documents/:id          # 删除文档
```

**关键请求/响应**：

```python
# POST /api/v1/documents/upload
# Content-Type: multipart/form-data
# Fields: file (binary), template_id? (UUID), processor_type? (str)

class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    file_type: str
    file_size: int
    status: str
    preview_url: str | None
    created_at: datetime

class ProcessingResultResponse(BaseModel):
    id: UUID
    version: int
    processor_type: str
    model_name: str
    structured_data: dict       # 提取的结构化数据
    inferred_schema: dict       # 推断的 JSON Schema
    tokens_used: int
    processing_time_ms: int
    created_at: datetime
```

### 3.3 对话矫正 (`/api/v1/conversations`)

```
POST   /api/v1/conversations                    # 创建对话（关联文档）
GET    /api/v1/conversations                    # 对话列表
GET    /api/v1/conversations/:id                # 对话详情（含消息历史）
POST   /api/v1/conversations/:id/messages       # 发送矫正消息（SSE 流式响应）
GET    /api/v1/conversations/:id/schema         # 获取当前 Schema
PUT    /api/v1/conversations/:id/schema         # 手动修改 Schema
GET    /api/v1/conversations/:id/schema/history # Schema 版本历史
POST   /api/v1/conversations/:id/rollback/:ver  # 回滚到指定版本
DELETE /api/v1/conversations/:id                # 删除对话
```

**关键请求/响应**：

```python
# POST /api/v1/conversations/:id/messages
class SendMessageRequest(BaseModel):
    content: str                # 用户自然语言指令

# 响应为 SSE 流：
# event: message_start
# data: {"message_id": "uuid"}
#
# event: text_delta
# data: {"delta": "已将 amount 字段"}
#
# event: schema_update
# data: {"schema": {...}, "diff": {...}}
#
# event: result_update
# data: {"structured_data": {...}, "version": 3}
#
# event: message_end
# data: {"prompt_version_id": "uuid"}
```

### 3.3.1 画框专项 OCR (`/api/v1/documents/:id/region-ocr`)

```
POST   /api/v1/documents/:id/region-ocr       # 框选区域专项 OCR
```

**请求/响应**：

```python
class RegionOcrRequest(BaseModel):
    page: int                   # 文档页码（从 1 开始）
    x: float                    # 框选区域左上角 x（归一化 0-1）
    y: float                    # 框选区域左上角 y（归一化 0-1）
    width: float                # 框选区域宽度（归一化 0-1）
    height: float               # 框选区域高度（归一化 0-1）
    action: str                 # "new_field" | "correct_field" | "context"
    target_field_path: str | None  # action=correct_field 时，要矫正的字段路径（如 "items[0].name"）

class RegionOcrResponse(BaseModel):
    ocr_text: str               # OCR 识别的原文
    suggested_field: dict | None  # action=new_field 时，AI 建议的字段定义
    correction_result: dict | None  # action=correct_field 时，矫正后的结果
    prompt_version_id: UUID | None  # 若触发了 Prompt 优化，新版本 ID
    auto_research_rounds: int   # 实际执行的 auto-research 轮数
```

### 3.3.2 逆向定位 (`/api/v1/documents/:id/highlights`)

```
GET    /api/v1/documents/:id/highlights        # 获取字段 → 文档区域映射
```

```python
class FieldHighlight(BaseModel):
    field_path: str             # 字段路径（如 "invoice_no", "items[0].name"）
    field_group: str            # 字段组名（如 "基本信息", "货物明细"）
    group_color: str            # 分组颜色（如 "#3B82F6"）
    bounding_box: BoundingBox | None  # 文档中的位置（计算值为 None）
    is_derived: bool            # 是否为计算/衍生值

class BoundingBox(BaseModel):
    page: int
    x: float                    # 归一化 0-1
    y: float
    width: float
    height: float

class HighlightsResponse(BaseModel):
    highlights: list[FieldHighlight]
    ocr_full_text: str | None   # 文档全文 OCR（用于前端模糊匹配兜底）
```

### 3.3.3 标注管理 (`/api/v1/documents/:id/annotations`)

标注 API 用于保存用户对识别结果的手动编辑（字段名、值、文档区域位置），供后续模型训练使用。

```
POST   /api/v1/documents/:id/annotations           # 新增标注（手动添加字段）
GET    /api/v1/documents/:id/annotations            # 获取文档所有标注
PATCH  /api/v1/documents/:id/annotations/:ann_id    # 更新标注（编辑字段名/值/区域）
DELETE /api/v1/documents/:id/annotations/:ann_id    # 删除标注
POST   /api/v1/documents/:id/annotations/batch      # 批量保存标注（首次 AI 识别后批量创建）
```

```python
# === Request ===

class CreateAnnotationRequest(BaseModel):
    field_name: str                     # 字段名称
    field_value: str | None = None      # 字段值
    field_type: str = "string"          # string | number | date | array
    bounding_box: BoundingBox | None = None  # 文档区域（可选，用户可后续画框）
    source: str = "manual"              # ai_detected | manual
    processing_result_id: UUID | None = None  # 关联的 ProcessingResult 版本

class UpdateAnnotationRequest(BaseModel):
    field_name: str | None = None       # 修改字段名（重命名）
    field_value: str | None = None      # 修改字段值
    field_type: str | None = None       # 修改字段类型
    bounding_box: BoundingBox | None = None  # 修改文档区域位置/大小

class BatchAnnotationRequest(BaseModel):
    annotations: list[CreateAnnotationRequest]
    processing_result_id: UUID          # 基于哪个处理结果版本

# === Response ===

class AnnotationResponse(BaseModel):
    id: UUID
    document_id: UUID
    field_name: str
    field_value: str | None
    field_type: str
    bounding_box: BoundingBox | None
    source: str                         # ai_detected | manual
    is_corrected: bool                  # 是否被修正过
    original_value: str | None          # 修正前原始值
    created_by: UUID
    created_at: datetime
    updated_at: datetime

class AnnotationListResponse(BaseModel):
    annotations: list[AnnotationResponse]
    document_id: UUID
    total: int
    correction_rate: float              # 修正率 = 被修正字段数 / 总字段数
```

**标注保存逻辑**：
1. AI 首次识别完成后，系统自动调用 `POST /annotations/batch` 将所有 AI 识别结果创建为 `source=ai_detected` 标注
2. 用户双击编辑字段并保存时，调用 `PATCH /annotations/:ann_id`，系统自动将 `is_corrected=true` 并记录 `original_value`
3. 用户通过 [+] 手动添加字段时，调用 `POST /annotations`，`source=manual`
4. 标注数据独立于 ProcessingResult 和 PromptVersion，不影响 API 生成逻辑
5. 训练导出时按 `correction_rate` 排序，高修正率文档优先用于微调

### 3.4 API 定义管理 (`/api/v1/api-definitions`)

```
POST   /api/v1/api-definitions                 # 创建 API（从对话确认后生成）
GET    /api/v1/api-definitions                 # API 列表（分页、筛选）
GET    /api/v1/api-definitions/:id             # API 详情
PUT    /api/v1/api-definitions/:id             # 更新 API 配置
PATCH  /api/v1/api-definitions/:id/status      # 更改状态（activate/deprecate）
GET    /api/v1/api-definitions/:id/versions    # 版本历史
GET    /api/v1/api-definitions/:id/docs        # 自动生成的 API 文档
GET    /api/v1/api-definitions/:id/stats       # 调用统计
DELETE /api/v1/api-definitions/:id             # 删除 API
```

**关键请求/响应**：

```python
# POST /api/v1/api-definitions
class CreateApiDefinitionRequest(BaseModel):
    name: str
    description: str
    api_code: str               # 用户自定义或系统建议
    conversation_id: UUID       # 从哪个对话创建
    processor_type: str = "gemini"
    model_name: str = "gemini-2.5-flash"

class ApiDefinitionResponse(BaseModel):
    id: UUID
    name: str
    api_code: str
    description: str
    status: str
    response_schema: dict
    endpoint_url: str           # 完整调用 URL
    version: int
    stats: ApiStatsResponse | None
    created_at: datetime
    updated_at: datetime

class ApiStatsResponse(BaseModel):
    total_calls: int
    calls_today: int
    calls_this_month: int
    success_rate: float
    avg_latency_ms: float
    error_count: int
```

### 3.5 模板市场 (`/api/v1/templates`)

```
GET    /api/v1/templates                       # 模板列表（分页、分类、语言筛选）
GET    /api/v1/templates/:id                   # 模板详情
POST   /api/v1/templates/:id/use               # 使用模板（创建文档+预填Prompt）
POST   /api/v1/templates/:id/fork              # 复制并创建自定义模板
POST   /api/v1/templates                       # 创建自定义模板（社区）
PUT    /api/v1/templates/:id                   # 更新自己的模板
POST   /api/v1/templates/:id/rate              # 评分
```

**关键请求/响应**：

```python
class TemplateListResponse(BaseModel):
    id: UUID
    name: str
    description: str
    category: str
    language: str
    field_count: int            # Schema 字段数
    is_official: bool
    usage_count: int
    rating: float
    rating_count: int

class UseTemplateRequest(BaseModel):
    document_id: UUID | None    # 可选：关联已上传的文档
    # 若不提供 document_id，跳转到工作台上传页

class ForkTemplateRequest(BaseModel):
    name: str                   # 新模板名称
    description: str | None
```

### 3.6 API 密钥管理 (`/api/v1/api-keys`)

```
POST   /api/v1/api-keys                       # 创建密钥
GET    /api/v1/api-keys                       # 密钥列表
PUT    /api/v1/api-keys/:id                   # 更新密钥（名称、限流等）
DELETE /api/v1/api-keys/:id                   # 吊销密钥
POST   /api/v1/api-keys/:id/rotate            # 轮换密钥
```

**关键请求/响应**：

```python
class CreateApiKeyRequest(BaseModel):
    name: str
    scopes: list[str] = ["extract"]
    rate_limit: int = 60        # 每分钟
    expires_in_days: int | None # None = 永不过期

class CreateApiKeyResponse(BaseModel):
    id: UUID
    name: str
    key: str                    # ⚠️ 仅在创建时返回完整密钥！
    key_prefix: str
    scopes: list[str]
    rate_limit: int
    expires_at: datetime | None
    created_at: datetime
```

### 3.7 公有云文档提取 API (`/api/v1/extract`) — 对外

这是客户通过 API Key 调用的核心端点：

```
POST   /api/v1/extract/:api_code               # 提取文档数据
```

**请求/响应**：

```python
# POST /api/v1/extract/:api_code
# Headers: X-API-Key: sk-xxxx
# Content-Type: multipart/form-data
# Fields: file (binary)
#   或
# Content-Type: application/json
# Body: { "file_url": "https://..." }
#   或
# Body: { "file_base64": "data:application/pdf;base64,..." }

class ExtractResponse(BaseModel):
    request_id: UUID            # 请求追踪 ID
    api_code: str
    api_version: int
    data: dict                  # 结构化提取数据（符合 response_schema）
    metadata: ExtractMetadata

class ExtractMetadata(BaseModel):
    processor: str              # gemini | openai
    model: str                  # gemini-2.5-flash
    tokens_used: int
    processing_time_ms: int
    confidence: float | None    # 可选置信度

class ExtractErrorResponse(BaseModel):
    request_id: UUID
    error: ErrorDetail

class ErrorDetail(BaseModel):
    code: str                   # invalid_api_key | quota_exceeded | processing_error | ...
    message: str
    details: dict | None
```

**错误码**：

| HTTP 状态 | error.code | 说明 |
|-----------|------------|------|
| 401 | `invalid_api_key` | API Key 无效或已吊销 |
| 403 | `insufficient_scope` | 密钥权限不足 |
| 404 | `api_not_found` | api_code 不存在 |
| 410 | `api_deprecated` | API 已废弃 |
| 413 | `file_too_large` | 文件超过限制（默认 20MB） |
| 415 | `unsupported_file_type` | 不支持的文件类型 |
| 422 | `processing_error` | AI 处理失败 |
| 429 | `rate_limit_exceeded` | 超过调用频率限制 |
| 429 | `quota_exceeded` | 超过月度配额 |
| 500 | `internal_error` | 内部错误 |

### 3.8 用量与统计 (`/api/v1/usage`)

```
GET    /api/v1/usage/summary                   # 用量概览（本月/配额）
GET    /api/v1/usage/daily                     # 每日调用统计
GET    /api/v1/usage/by-api                    # 按 API 细分统计
GET    /api/v1/usage/logs                      # 调用日志明细（分页）
GET    /api/v1/usage/logs/:id                  # 单条调用详情
```

### 3.9 WebSocket 端点

```
WS    /api/v1/ws/documents/:id/status          # 文档处理进度推送
```

**消息格式**：

```json
// 服务端 → 客户端
{"type": "status_update", "status": "processing", "progress": 0.45, "message": "正在提取文本..."}
{"type": "status_update", "status": "completed", "result_id": "uuid"}
{"type": "status_update", "status": "failed", "error": "处理超时"}
```

---

## 四、分页、筛选、排序约定

所有列表接口统一使用以下查询参数：

```python
class PaginationParams(BaseModel):
    page: int = 1               # 页码，从 1 开始
    page_size: int = 20         # 每页条数，最大 100
    sort_by: str = "created_at" # 排序字段
    sort_order: str = "desc"    # asc | desc

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int
```

筛选参数按端点不同而异，例如：
- `/documents`: `status`, `file_type`, `created_after`, `created_before`
- `/api-definitions`: `status`, `search`（名称/编码模糊搜索）
- `/templates`: `category`, `language`, `is_official`, `search`

---

## 五、安全设计

### JWT 认证流程

```
登录 → 返回 access_token (15min) + refresh_token (7d)
  → 前端请求带 Authorization: Bearer <access_token>
  → access_token 过期 → 用 refresh_token 换新 token
  → refresh_token 过期 → 重新登录
```

### API Key 安全

- 密钥生成：`sk-` 前缀 + 32 字节随机 + Base62 编码
- 存储：只存 SHA-256 哈希，创建时仅展示一次完整密钥
- 传输：仅通过 HTTPS + `X-API-Key` Header
- 限流：每个密钥独立限流（默认 60 次/分钟）
- 过期：支持设置过期时间，过期自动失效

### 多租户隔离

- 所有数据查询自动注入 `organization_id` 过滤
- 使用 FastAPI Dependencies 在请求级别注入租户上下文
- API Key 绑定到 Organization，不能跨租户调用

---

## 六、异步任务设计（Celery）

```python
# 文档处理任务
@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def process_document_task(self, document_id: str, processor_type: str, model_name: str, prompt: str | None, schema: dict | None):
    """
    1. 从 S3 下载文档
    2. 调用 ProcessorFactory 创建处理器
    3. 执行文档处理
    4. 存储 ProcessingResult
    5. 通过 WebSocket 通知前端
    """

# 矫正重处理任务
@celery_app.task
def reprocess_with_correction_task(document_id: str, prompt_version_id: str):
    """矫正后使用新 Prompt 重新处理"""

# 画框专项 OCR + Prompt 自优化任务
@celery_app.task(bind=True, max_retries=0)
def region_ocr_and_optimize_task(self, document_id: str, region: dict, action: str, target_field_path: str | None):
    """
    1. 裁切文档图像中的框选区域
    2. 对裁切区域执行专项 OCR
    3. 根据 action 执行不同逻辑:
       - new_field: LLM 推断字段名/类型/位置 → 更新 Prompt
       - correct_field: 对比框选内容与当前值 → 调整 Prompt
       - context: 附加上下文，不触发优化
    4. Auto-Research 循环（最多 3 轮）:
       a. 生成/修改 Prompt
       b. 用新 Prompt 重新处理文档
       c. 对比结果与用户期望
       d. 不满意 → LLM 自我反思，调整策略，继续
    5. 保存最终 PromptVersion（仅 Prompt 文本，不存坐标）
    6. 通过 WebSocket 通知前端更新
    """

# 用量统计聚合任务（定时）
@celery_app.task
def aggregate_daily_usage():
    """每日凌晨聚合用量数据"""
```

---

## 七、目录结构与模块依赖

```
backend/app/
├── main.py                # FastAPI app 创建、中间件、路由挂载
├── core/
│   ├── config.py          # Settings (Pydantic BaseSettings)
│   ├── security.py        # JWT 编解码、密码哈希、API Key 验证
│   ├── deps.py            # get_db, get_current_user, get_api_key_auth
│   └── exceptions.py      # AppException 及全局异常处理器
├── models/                # SQLAlchemy ORM（上述所有模型）
├── schemas/               # Pydantic Request/Response（上述所有 Schema）
├── api/v1/                # 路由（薄层，只做参数解析和调用 Service）
├── services/              # 业务逻辑（核心层）
├── engine/                # AI 处理引擎（移植代码 + 新增矫正引擎）
├── tasks/                 # Celery 异步任务
└── utils/                 # 工具函数
```

**依赖方向**（严格单向）：
```
api/v1 → services → engine + models
              ↓
           tasks（异步调用 engine）
```

禁止：api 直接调用 engine、services 之间循环依赖、engine 依赖 services。
