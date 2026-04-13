# ApiAnything

> **把任意文档变成结构化 API** — 上传 PDF/图片，AI 自动提取字段，一键生成可调用的 REST 端点。

---

## 功能特性

- 📄 **文档上传** — 支持 PDF、PNG、JPG、XLSX，最大 50 MB
- 🤖 **AI 提取** — Gemini / OpenAI / Mock 多处理器，自动识别结构化字段
- 🖥️ **三栏工作台** — 文档预览 + 字段视图 + JSON 输出，实时联动高亮
- 🔗 **API 生成** — 一键生成带 API Key 鉴权的 REST 提取端点
- 📊 **校验 & 统计** — 置信度分级、字段完整性校验、统计分析视图
- 🤖 **AI 矫正对话** — 底部对话面板（UI 已就绪，P4 接入后端）

---

## 快速启动

### 环境要求

| 工具 | 版本 | 安装方式 |
|------|------|---------|
| Python | 3.11+ | `uv python install 3.12` |
| Node.js | 18+ | `nodeenv ~/.local/nodeenv --node=20.18.0` |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

---

### 1. 后端启动

```bash
cd backend

# 安装依赖
uv sync

# 启动开发服务器
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 API 文档：http://localhost:8000/docs

---

### 2. 前端启动

```bash
cd frontend

# 安装依赖（需要 Node.js 在 PATH 中）
export PATH="$HOME/.local/nodeenv/bin:$PATH"
npm install

# 启动开发服务器
npm run dev
```

访问前端：http://localhost:5173

---

### 3. Mock 模式（无需 AI API Key）

后端默认使用 Mock 处理器，无需配置任何 API Key：

```bash
# backend/.env（可选，已有默认值）
DEFAULT_PROCESSOR=mock
DATABASE_URL=sqlite:///./data/apianything.db
UPLOAD_DIR=./data/uploads
```

Mock 处理器返回固定的发票结构化数据，适合开发调试。

---

### 4. 接入真实 AI

```bash
# backend/.env
DEFAULT_PROCESSOR=gemini
GEMINI_API_KEY=your-gemini-api-key

# 或
DEFAULT_PROCESSOR=openai
OPENAI_API_KEY=your-openai-api-key
```

---

## 项目结构

```
ApiAnything/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 实例 + CORS + 路由挂载
│   │   ├── core/
│   │   │   ├── config.py        # Pydantic BaseSettings
│   │   │   ├── database.py      # SQLAlchemy / SQLite
│   │   │   └── deps.py          # 依赖注入
│   │   ├── models/              # ORM 模型
│   │   ├── schemas/             # Pydantic 请求/响应 Schema
│   │   ├── services/            # 业务逻辑层
│   │   ├── api/v1/              # HTTP 路由
│   │   └── processors/          # AI 处理器（mock/gemini/openai）
│   ├── data/uploads/            # 上传文件存储（自动创建）
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── pages/               # 路由页面
│   │   │   ├── DocumentList.tsx # 文档列表 + 上传
│   │   │   ├── Workspace.tsx    # 三栏工作台（主界面）
│   │   │   ├── ApiList.tsx      # API 管理
│   │   │   └── Settings.tsx     # API Key 管理
│   │   ├── components/
│   │   │   ├── workspace-v2/    # 深色主题工作台组件
│   │   │   ├── upload/          # 上传组件
│   │   │   └── api/             # API 配置组件
│   │   ├── stores/              # Zustand 状态管理
│   │   └── lib/                 # 工具函数（axios, toast, utils）
│   └── package.json
│
└── testing/
    ├── test_e2e.py              # E2E 测试脚本
    └── test1_honor/             # 测试文件
```

---

## API 接口

### 核心端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/documents/upload` | 上传文档，触发 AI 提取 |
| `GET`  | `/api/v1/documents` | 文档列表（分页） |
| `GET`  | `/api/v1/documents/:id` | 文档详情 + 最新结果 |
| `GET`  | `/api/v1/documents/:id/preview` | 文档预览 URL |
| `POST` | `/api/v1/documents/:id/reprocess` | 重新处理文档 |
| `POST` | `/api/v1/api-definitions` | 创建 API 定义 |
| `GET`  | `/api/v1/api-definitions` | API 定义列表 |
| `POST` | `/api/v1/api-keys` | 创建 API Key |
| `GET`  | `/api/v1/api-keys` | API Key 列表 |
| `POST` | `/api/v1/extract/:api_code` | **调用提取端点**（需 X-API-Key） |

### 调用示例

```bash
# 1. 创建 API Key
curl -X POST http://localhost:8000/api/v1/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name": "my-key"}'
# → {"id": "...", "key": "sk-...", "key_prefix": "sk-abc"}

# 2. 创建 API 定义（api_code 必须全小写）
curl -X POST http://localhost:8000/api/v1/api-definitions \
  -H "Content-Type: application/json" \
  -d '{"name":"发票提取","api_code":"invoice-v1","description":""}'

# 3. 激活 API
curl -X POST http://localhost:8000/api/v1/api-definitions/invoice-v1/activate

# 4. 调用提取（上传文件）
curl -X POST http://localhost:8000/api/v1/extract/invoice-v1 \
  -H "X-API-Key: sk-..." \
  -F "file=@invoice.pdf"
```

---

## 运行测试

```bash
# 启动后端后执行
python testing/test_e2e.py

# 指定服务器地址
python testing/test_e2e.py --base-url http://localhost:8000

# 保留测试数据（不自动清理）
python testing/test_e2e.py --no-cleanup
```

测试覆盖：健康检查 → 上传文档 → 获取结果 → 创建 Key → 创建 API 定义 → 激活 → 调用提取 → 标注 CRUD → 清理

---

## 开发说明

### 添加新处理器

1. 在 `backend/app/processors/` 创建 `my_processor.py`，继承 `BaseProcessor`
2. 实现 `extract(file_bytes, filename, config) → list[dict]` 方法
3. 在 `factory.py` 注册
4. 设置环境变量 `DEFAULT_PROCESSOR=my_processor`

### 前端组件扩展

工作台核心组件位于 `frontend/src/components/workspace-v2/`：
- `DarkDocumentViewer.tsx` — 文档预览 + Bbox 标注
- `DarkFieldViewer.tsx` — 字段视图 / 校验规则 / 统计分析
- `DarkJsonViewer.tsx` — JSON 输出 + API 端点
- `AiChat.tsx` — AI 矫正对话面板

### 数据库迁移

```bash
cd backend
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

---

## 路线图

- [x] P0 — 项目骨架（FastAPI + React + SQLite）
- [x] P1 — 上传与提取（文档 → AI → 结构化 JSON）
- [x] P2 — 工作台三栏 UI（深色主题，原型还原）
- [x] P3 — 标注编辑与联动（Bbox 拖拽、字段编辑、三栏联动）
- [x] P5 — API 生成与调用（API 定义、API Key、提取端点）
- [x] P6 — 收尾串联（错误处理、测试脚本、README）
- [ ] P4 — 对话矫正（SSE 流式对话，自然语言矫正字段）

---

## License

MIT
