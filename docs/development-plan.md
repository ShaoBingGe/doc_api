# ApiAnything 开发计划

> 基于 `design-v2.md`，将原型目标拆解为可执行的开发任务。
> 每个 Task 粒度控制在 **0.5-1 天**，可独立验证。

> **⛔ 范围调整（2026-04-04）**：以下内容**暂不开发**，相关任务已注释：
> - 所有**前端**任务（P0 T0.5-T0.7、P1 T1.4-T1.6、P2 全部、P3 T3.1-T3.3/T3.5-T3.6、P4 T4.4-T4.6、P5 T5.4-T5.6、P6 全部）
> - **对话矫正** API（P4 T4.1-T4.3）及对应的 8 个 conversation 端点

---

## 总览

| 阶段 | 内容 | 任务数 | 预估工时 |
|------|------|--------|---------|
| **P0 — 项目骨架** | 前后端脚手架、数据库、引擎移植 | 7 | 3 天 |
| **P1 — 上传与提取** | 文档上传 → AI 提取 → 返回结果 | 6 | 3 天 |
| **P2 — 工作台三栏 UI** | A 栏文档预览 + B 栏字段编辑 + C 栏 JSON | 8 | 4 天 |
| **P3 — 标注编辑与联动** | bbox 拖拽、字段编辑保存、三栏联动 | 6 | 3 天 |
| **P4 — 对话矫正** | SSE 对话、矫正引擎、版本管理 | 6 | 4 天 |
| **P5 — API 生成与调用** | API 定义、API Key、公有云提取端点 | 6 | 3 天 |
| **P6 — 收尾串联** | 完整流程串通、边界处理、调试优化步骤 | 5 | 2 天 |
| **合计** | | **44** | **22 天** |

---

## P0 — 项目骨架（3 天）

搭建前后端脚手架，移植引擎代码，确保基础设施可运行。

### 后端

#### T0.1 FastAPI 项目初始化 ✅
```
创建 backend/ 目录结构
- app/main.py: FastAPI 实例、CORS 中间件、路由挂载
- app/core/config.py: Pydantic BaseSettings（DATABASE_URL, UPLOAD_DIR, GEMINI_API_KEY 等）
- app/core/database.py: SQLAlchemy 引擎 + Session 工厂（SQLite）
- app/core/deps.py: get_db 依赖注入
- pyproject.toml: 依赖声明（fastapi, uvicorn, sqlalchemy, alembic, python-multipart）
- alembic/ 初始化

验收: uvicorn app.main:app --reload 启动成功，GET /health 返回 200
```

#### T0.2 接口抽象层 ✅
```
创建 app/abstractions/
- storage.py: StorageBackend ABC + LocalStorage（save/load/delete → ./data/uploads/）
- task_runner.py: TaskRunner ABC + SyncRunner（直接同步调用）
- auth.py: AuthProvider ABC + SimpleApiKeyAuth（SHA-256 校验）

在 deps.py 中注册工厂函数，通过 config 环境变量选择实现

验收: 单元测试 — LocalStorage 存取文件成功，SyncRunner 同步执行函数成功
```

#### T0.3 全部 ORM 模型 + 初始迁移 ✅
```
创建 app/models/
- document.py: Document + ProcessingResult（含 status enum, version, structured_data JSON）
- conversation.py: Conversation + Message（含 role enum）
- annotation.py: Annotation（含 bounding_box JSON, source enum, confidence）
- api_definition.py: ApiDefinition（含 status enum, api_code unique）
- api_key.py: ApiKey（含 key_hash, key_prefix, is_active）

生成 alembic 迁移脚本并执行

验收: alembic upgrade head 成功，SQLite 中 6 张表创建完成
```

#### T0.4 引擎代码移植 ✅
```
创建 app/engine/
- 从 label-studio-ml-backend/invoice_extractor/ 复制:
  processors/base.py (原样)
  processors/gemini.py (原样)
  processors/openai.py (原样)
  processors/piaozone.py (原样)
  processors/mock.py (原样)
  processors/factory.py (去掉 label_studio_ml 导入，约改 5 行)
  config/manager.py (去掉 IP 白名单相关方法，约删 50 行)
  config/models.yaml (原样)
  analyzers/excel.py (原样)

- 调整 factory.py 的 import 路径

验收: Python 交互式测试 — factory.create("mock") 返回 MockProcessor 实例，
      factory.create("gemini") 不报 import 错误（实际调用需 API Key）
```

### 前端

#### T0.5 React + Vite 项目初始化 ✅
#### T0.6 路由 + 页面骨架 ✅
#### T0.7 Zustand Store 骨架 ✅

---

## P1 — 上传与提取（3 天）

实现「上传文档 → AI 提取 → 返回结构化 JSON」的最小闭环。

### 后端

#### T1.1 Pydantic Schemas ✅
```
创建 app/schemas/:
- document.py: DocumentUploadResponse, DocumentDetail, ProcessingResultResponse
- annotation.py: AnnotationCreate, AnnotationUpdate, AnnotationResponse
- common.py: ErrorResponse

验收: Schema 类型定义完整，IDE 无类型错误
```

#### T1.2 DocumentService + 上传端点 ✅
```
创建 app/services/document_service.py:
- upload(file, processor_key): 存文件 → 创建 Document → 调用引擎 → 创建 ProcessingResult
- get(document_id): 返回 Document + 最新 ProcessingResult
- list(): 分页列表

创建 app/api/v1/documents.py:
- POST /api/v1/documents/upload (multipart)
- GET /api/v1/documents
- GET /api/v1/documents/:id

创建 app/api/v1/router.py: 汇总所有路由

验收: curl 上传 PDF → 返回 Document(status=completed) + ProcessingResult(version=1)
      mock 处理器模式下端到端成功
```

#### T1.3 Schema 推断引擎 ✅
```
创建 app/engine/schema_generator.py:
- infer(structured_data: dict) → JSON Schema
- 类型检测: string/number/date/boolean
- 嵌套对象和数组支持
- 字段描述自动生成

验收: 传入 mock 发票 JSON → 返回合法 JSON Schema，
      包含 properties + types + required
```

### 前端

#### T1.4 UploadStep 组件 ✅
#### T1.5 ProcessingOverlay 组件 ✅
#### T1.6 上传 → 提取端到端串通 ✅

---

<!-- SKIP: P2 全部为前端任务，暂不开发

## P2 — 工作台三栏 UI（4 天）

构建核心交互界面：A 栏文档预览 + B 栏字段编辑 + C 栏 JSON 输出。

### A 栏

#### T2.1 DocumentCanvas — 文档渲染 ✅
```
创建 src/components/document/DocumentCanvas.tsx:
- 白色文档背景 + 圆角 + 阴影
- PDF: 集成 react-pdf 渲染第一页
- 图片: <img> 直接渲染
- 文档容器 position: relative，铺满 A 栏

安装 react-pdf: pnpm add react-pdf

验收: 上传 PDF/图片后 A 栏正确渲染文档内容
```

#### T2.2 DocumentCanvas — Bbox 高亮叠加 ✅
```
在 DocumentCanvas 上叠加 bbox 层:
- 遍历 results，每个字段渲染绝对定位 div
- 位置: left/top/width/height 百分比
- 边框颜色按置信度分级 (≥95% 绿 / ≥90% 橙 / <90% 红)
- 左上角标签牌: keyName（白字 + 同色背景）
- 点击框 → setSelectedId
- 点击空白 → setSelectedId(null)
- 选中态: 靛蓝边框 + 15% 靛蓝背景

验收: 文档上叠加显示所有字段的高亮框，颜色按置信度分级，点击选中/取消
```

### B 栏

#### T2.3 FieldEditorPanel 框架 ✅
```
创建 src/components/fields/FieldEditorPanel.tsx:
- 固定宽度 340px，左边框分隔
- 顶部: [字段] [API] Tab 切换按钮
- Tab 内容区 flex: 1，overflow: auto

创建 src/components/fields/NlCorrectionBar.tsx:
- 标题 "自然语言矫正"
- 输入框 + [矫正] 按钮（先占位，P4 实现实际功能）
- 矫正历史列表（10px ✓ 绿色）

验收: 右侧面板结构完整，Tab 切换正常，矫正输入框可输入
```

#### T2.4 FieldCard 组件 ✅
```
创建 src/components/fields/FieldCard.tsx:
- keyName (11px 600) + 置信度标签（颜色药丸）
- value 输入框（可直接编辑，onChange → updateFieldValue）
- 位置信息 "(x%, y%) | w×h" (9px textDim)
- 选中态: primaryBg 背景 + borderActive 边框
- 点击 → setSelectedId

在 FieldEditorPanel 中渲染 FieldCard 列表:
- 标题 "识别字段 (N)"
- 遍历 results 渲染 FieldCard

验收: B 栏显示所有字段卡片，值可编辑，点击选中高亮
```

#### T2.5 AddFieldForm 组件 ✅
```
创建 src/components/fields/AddFieldForm.tsx:
- [+ 添加识别字段] 按钮
- 点击展开行内表单: 名称 | 值 | 类型下拉(string/number/date/array) | [保存] [取消]
- 保存 → addField → B 栏新增一行（置信度标记"手动"）
- 取消 → 折叠表单

验收: 可添加新字段，新字段在列表和 C 栏 JSON 中同步出现
```

### C 栏

#### T2.6 ApiPreviewPanel 组件 ✅
```
创建 src/components/api/ApiPreviewPanel.tsx:
- 格式切换: [扁平] [详细] [分组] 三按钮
- 自然语言格式调整输入框 + [调整] 按钮（先实现关键词匹配：分组→grouped 等）
- JSON 输出区: <pre> 代码块，#a5f3fc 代码色

实现三种格式生成函数:
- flat: { data: { key: value } }
- detailed: { fields: [{ key, label, value, confidence, position }] }
- grouped: { data: { vendor: {...}, financial: {...}, payment: {...} } }

验收: 三种格式切换正确，JSON 随字段编辑实时更新
```

### 布局

#### T2.7 三栏布局组装 ✅
```
在 Workspace.tsx 中组装步骤 1-4 的三栏布局:
- 左侧 A 栏: DocumentCanvas (flex: 1, padding 12px)
- 右侧: FieldEditorPanel/ApiPreviewPanel (width: 340px)
- Tab 切换逻辑: 步骤 1-2 → 字段 Tab, 步骤 3 → API Tab, 步骤 4 → 字段 Tab
- A 栏顶部标题 "📄 文档预览 — 点击/拖动调整识别区域"
- 步骤 4 独有: [🔄 重新上传同类文档 (N 次调试)] 按钮

验收: 步骤 1-4 三栏布局完整，Tab 按步骤自动切换，可手动点击切换
```

#### T2.8 ApiConfigStep 组件 ✅
```
创建 src/components/api/ApiConfigStep.tsx (步骤 5):
- 🎉 居中标题 "API 已就绪"
- API 编码卡片 + [复制] 按钮
- Endpoint 卡片 + [复制] 按钮
- cURL 示例代码块
- Python 示例代码块
- [下载 SDK 配置文件] 渐变按钮
- [复制] 点击 → "✓ 已复制"（2 秒恢复）

验收: 步骤 5 页面视觉还原原型，复制功能正常
```

-->

---

## P3 — 标注编辑与联动（3 天）

实现 bbox 拖拽调整、字段编辑保存、三栏联动。

<!-- SKIP: T3.1-T3.3、T3.5-T3.6 为前端任务，暂不开发 -->

<!-- SKIP_FRONTEND_START

#### T3.1 Bbox 拖拽移动 ✅
```
在 DocumentCanvas 中实现:
- 选中态 bbox → cursor: grab
- mousedown 记录起始位置和原始 bbox
- mousemove 计算 dx/dy 百分比偏移 → updateFieldBbox
- mouseup 结束拖拽
- 拖拽中: cursor: grabbing, transition: none
- 释放后: transition: all 0.15s ease

验收: 选中框后拖拽可移动位置，B 栏位置信息实时更新
```

#### T3.2 Bbox 缩放手柄 ✅
```
选中态 bbox 右下角添加 8×8px 缩放手柄:
- 靛蓝色方块，cursor: nwse-resize
- mousedown 记录手柄类型 "br"
- mousemove 计算 dw/dh → 更新 bbox 宽高（最小值: w=5%, h=2%）
- mouseup 结束

验收: 拖拽右下角手柄可调整框大小，B 栏尺寸信息实时更新
```

#### T3.3 三栏联动选中 ✅
```
完善 selectedId 联动:
- 点击 A 栏 bbox → B 栏对应 FieldCard 高亮 + 滚动到可视区
- 点击 B 栏 FieldCard → A 栏对应 bbox 选中态
- 点击空白/Escape → 全部取消选中
- 未选中框: 保持置信度颜色，不 dim（原型行为）

B 栏滚动定位:
- selectedId 变化时，对应 FieldCard ref.scrollIntoView({ behavior: 'smooth', block: 'nearest' })

验收: A↔B 栏点击联动正确，Escape 取消，滚动定位流畅
```

#### T3.4 标注 CRUD 后端 ✅
```
创建 app/services/annotation_service.py:
- create(document_id, field_name, field_value, field_type, bounding_box, source, confidence)
- update(annotation_id, field_name?, field_value?, bounding_box?)
- delete(annotation_id)
- list_by_document(document_id)

创建 app/api/v1/annotations.py:
- GET /api/v1/documents/:id/annotations
- POST /api/v1/documents/:id/annotations
- PATCH /api/v1/documents/:id/annotations/:fid
- DELETE /api/v1/documents/:id/annotations/:fid

验收: curl 测试 CRUD 四个端点均正常
```

#### T3.5 字段编辑保存前端 ✅
```
FieldCard 编辑后调用后端:
- value 输入框 onChange → 本地实时更新（workspace-store）
- value 输入框 onBlur → 调用 PATCH annotation API 保存

AddFieldForm 保存:
- 保存 → 调用 POST annotation API
- 同时创建新 ProcessingResult(version+1, source=manual_edit)

api-client.ts 新增:
- createAnnotation(docId, data)
- updateAnnotation(docId, fieldId, data)
- deleteAnnotation(docId, fieldId)

验收: 编辑字段值 → 失焦保存到后端 → 刷新页面数据不丢失
      添加新字段 → 后端 Annotation 记录正确创建
```

#### T3.6 VersionBar 版本管理 ✅
```
创建 src/components/workspace/VersionBar.tsx:
- 左侧: 版本圆点 (v1 ● v2 ○ v3 ○)，当前版本实心
- 右侧: [保存并生成 API] 渐变按钮
- 点击历史版本 → 加载该版本 ProcessingResult → 更新 results

后端:
- GET /api/v1/documents/:id/results 返回全部版本

前端:
- 点击版本 → getDocumentResults → 切换 store 中 results

验收: 矫正产生新版本 → 底栏新增圆点 → 点击旧版本可回溯
```

SKIP_FRONTEND_END -->

---

<!-- SKIP: P4 全部暂不开发（对话矫正后端 T4.1-T4.3 + 前端 T4.4-T4.6）

## P4 — 对话矫正（4 天）

实现自然语言矫正引擎和 SSE 流式对话。

### 后端

#### T4.1 对话矫正引擎
```
创建 app/engine/correction.py:
- build_correction_prompt(user_instruction, current_data, current_schema, current_prompt, history)
  → 构造发给 LLM 的 correction prompt
- parse_correction_result(llm_output)
  → 解析 LLM 返回，提取修改后的 structured_data + schema + prompt
- apply_correction(document_id, user_instruction)
  → 完整矫正流程: build prompt → 调用 LLM → parse → 创建新 ProcessingResult

验收: 单元测试 — 给定 mock 指令和数据，build_correction_prompt 输出合理
```

#### T4.2 对话 Service + SSE 端点
```
创建 app/services/conversation_service.py:
- get_or_create_conversation(document_id)
- send_message(conversation_id, content):
  → 创建 user Message
  → 调用 correction.apply_correction
  → 创建 assistant Message(result_version=N+1)
  → 返回 SSE 事件流

创建 app/api/v1/conversations.py:
- GET /api/v1/documents/:id/conversation
- POST /api/v1/documents/:id/conversation/messages → StreamingResponse (SSE)

SSE 事件序列:
  yield text_delta: {"content": "理解您的指令..."}
  yield text_delta: {"content": "正在重新提取..."}
  yield result_update: {"version": N+1, "structured_data": {...}}
  yield done: {}

验收: curl 发送矫正指令 → 收到 SSE 事件流 → 数据库新增 Message + ProcessingResult
```

#### T4.3 SSE 流式对话（简化版）
```
原型阶段简化实现:
- correction.py 内部先同步调用处理器，不做真正的 LLM streaming
- 将结果拆成几个 text_delta 事件模拟流式效果
- 最后发送 result_update + done

后续扩展:
- 接入 LLM 真正的流式 API（Gemini/OpenAI streaming）

验收: 前端可收到 SSE 事件流，虽然不是真正逐字流式，但协议格式完整
```

### 前端

#### T4.4 SSE 客户端
```
创建 src/lib/sse.ts:
- sendCorrectionMessage(docId, content):
  → fetch POST with SSE
  → 解析 event: text_delta / result_update / done
  → 返回 AsyncIterable 或 callback 模式

- 事件处理:
  text_delta → 累积 AI 回复文字
  result_update → 更新 workspace-store results + 版本
  done → 结束

验收: 调用 sendCorrectionMessage → 正确接收并分发所有 SSE 事件类型
```

#### T4.5 NlCorrectionBar 对话功能
```
完善 NlCorrectionBar.tsx:
- 输入矫正指令 → 调用 sse.sendCorrectionMessage
- 显示 AI 流式回复（文字逐步累积）
- 完成后:
  → 矫正历史新增 "✓ 时间 — 指令"
  → B 栏字段列表更新为新版本数据
  → C 栏 JSON 同步更新
  → 底栏版本 +1
- 矫正中输入框禁用 + 按钮显示 loading

验收: 输入 "Payment Method 应该是 Wire Transfer" → AI 回复 → 字段更新 → 版本递增
```

#### T4.6 API 格式自然语言调整
```
完善 ApiPreviewPanel 的自然语言格式调整:
- 输入 → 调用后端 correction（带格式调整 context）
- 简化版先用关键词匹配:
  "分组/group" → grouped
  "简单/flat/扁平" → flat
  "详细/detail" → detailed
- SSE 回复 → 更新 apiFormat

验收: 输入 "按供应商和财务信息分组" → 切换到分组格式
```

-->

---

## P5 — API 生成与调用（3 天）

实现 API 定义创建、API Key 管理、公有云提取端点。

#### T5.1 ApiDefinition Service + 端点 ✅
```
创建 app/services/api_definition_service.py:
- create(document_id, name, api_code, description):
  → 获取最新 ProcessingResult
  → 创建 ApiDefinition(schema_definition, prompt_template, processor_key, status=active)
- list(), get(id), update(id, data)

创建 app/api/v1/api_defs.py:
- POST /api/v1/api-definitions
- GET /api/v1/api-definitions
- GET /api/v1/api-definitions/:id
- PATCH /api/v1/api-definitions/:id

验收: curl 创建 API 定义 → 返回完整 ApiDefinition（含 schema + prompt）
```

#### T5.2 ApiKey Service + 端点 ✅
```
创建 app/services/api_key_service.py:
- create(name): 生成 sk- 前缀 + 32 字节 Base62 → 存 SHA-256 哈希 → 返回明文（仅一次）
- list(): 返回 key_prefix + name + is_active
- delete(id): 设 is_active=False
- verify(raw_key): SHA-256 比对

创建 app/api/v1/api_keys.py:
- POST /api/v1/api-keys → 返回 { id, name, key_prefix, raw_key }（raw_key 仅此一次）
- GET /api/v1/api-keys
- DELETE /api/v1/api-keys/:id

验收: 创建 Key → 拿到明文 → 用明文调用 verify 成功 → 列表只显示前缀
```

#### T5.3 Extract 提取端点 ✅
```
创建 app/services/extract_service.py:
- extract(api_code, file, api_key):
  → auth.authenticate(api_key)
  → 查 ApiDefinition(api_code, status=active)
  → storage.save(file) 临时存储
  → processor = factory.create(definition.processor_key)
  → result = processor.extract(file, prompt=definition.prompt_template, schema=definition.schema_definition)
  → 返回 { request_id, api_code, data, schema }

创建 app/api/v1/extract.py:
- POST /api/v1/extract/{api_code} (X-API-Key 认证)

验收: 用 API Key + 上传文件调用提取端点 → 返回结构化数据
```

<!-- SKIP: T5.4-T5.6 为前端任务，暂不开发

#### T5.4 API 生成前端流程 ✅
```
Workspace.tsx 步骤 5 触发:
- 点击 [保存并生成 API] → 调用 POST /api/v1/api-definitions
- 成功 → setStep(5)，显示 ApiConfigStep
- ApiConfigStep 展示: api_code, endpoint, cURL, Python 示例

api-client.ts 新增:
- createApiDefinition(data)

验收: 点击生成 → 看到 API 就绪页 → 代码示例中的 api_code 正确
```

#### T5.5 API 管理页 ✅
```
完善 src/pages/ApiList.tsx:
- 列表: 调用 GET /api/v1/api-definitions
- 卡片展示: name, api_code, status, created_at
- 点击卡片 → 弹窗/展开详情（endpoint + schema + 代码示例）

验收: 已生成的 API 在管理页正确展示
```

#### T5.6 Settings 页 — API Key 管理 ✅
```
完善 src/pages/Settings.tsx:
- API Key 列表: 调用 GET /api/v1/api-keys
- 每行: name, key_prefix(sk-...abc), created_at, [删除]
- [创建新 Key] 按钮 → 弹窗显示明文 Key + 警告"仅显示一次"
- [复制] + 确认关闭

验收: 创建 Key → 显示明文 → 关闭后只看到前缀 → 删除后列表更新
```

-->

---

<!-- SKIP: P6 以前端交互为主，暂不开发

## P6 — 收尾串联（2 天）

完整流程串通、边界处理、调试优化步骤。

#### T6.1 步骤 4 调试优化
```
步骤 4 交互:
- A 栏顶部显示 [🔄 重新上传同类文档 (N 次调试)] 按钮
- 点击 → 弹出文件选择 → 上传新文档
- 后端用当前 prompt_template 重新处理 → 返回新结果
- 前端更新 A 栏文档和 B 栏字段
- 可继续矫正
- 调试计数器递增

后端: POST /api/v1/documents/:id/reprocess
  body: { prompt_override: "..." }（可选）

验收: 步骤 4 上传新文档 → 用已有 Prompt 处理 → 结果正确展示
```

#### T6.2 步骤流转完整串通
```
完善步骤导航:
- TopBar [下一步] / [上一步] 按钮逻辑
- 步骤 0 → 上传成功自动跳到 1
- 步骤 1-3 → 自由前后跳转
- 步骤 4 → [确认生成 API] 文字变化
- 步骤 4 → 5 → 调用生成 API
- 步骤 5 → 无返回按钮，只有导航到 /apis

验收: 从上传到生成 API 的 6 步完整走通，无断链
```

#### T6.3 错误处理 + 边界情况
```
前端:
- 上传失败 → 显示错误 toast，重置到步骤 0
- AI 处理失败 → ProcessingOverlay 显示错误信息 + 重试按钮
- 网络错误 → 统一 toast 提示
- SSE 断开 → 自动重试一次
- 空文件/不支持格式 → 上传前校验

后端:
- 统一错误格式: {"error": {"code": "xxx", "message": "xxx"}}
- 文件大小限制（20MB）
- 不支持的文件类型 → 415
- 处理器异常 → 优雅降级，返回 partial result

验收: 故意制造各种错误场景，确认均有合理的错误提示
```

#### T6.4 Mock 模式全流程测试
```
确保 DEFAULT_PROCESSOR=mock 模式下全流程可跑:
- mock.py 返回固定发票 JSON（含 bbox 信息）
- 所有端点在 mock 模式下不依赖外部 API
- 编写简易测试脚本:
  1. 上传文件
  2. 获取结果
  3. 发送矫正
  4. 编辑标注
  5. 生成 API
  6. 用 API Key 调用提取

验收: 全脚本跑通，不调用任何外部 AI API
```

#### T6.5 README + 启动文档
```
编写 README.md:
- 项目简介
- 快速启动（后端 + 前端）
- 环境变量说明
- Mock 模式开发指南
- API 文档地址（FastAPI /docs）

验收: 新开发者按 README 可在 5 分钟内启动项目
```

-->

---

## 任务依赖图

```
P0 骨架
  T0.1 ─┬→ T0.2 ─→ T0.3 ─→ T0.4 (后端可用)
        └→ T0.5 ─→ T0.6 ─→ T0.7 (前端可用)

P1 上传与提取 (依赖 P0)
  T1.1 ─→ T1.2 ─→ T1.3 (后端上传闭环)
  T1.4 ─→ T1.5 ─→ T1.6 (前端上传闭环，依赖 T1.2)

P2 三栏 UI (依赖 P1)
  T2.1 ─→ T2.2 (A 栏)
  T2.3 ─→ T2.4 ─→ T2.5 (B 栏)
  T2.6 (C 栏)
  T2.7 (布局组装，依赖 T2.2 + T2.4 + T2.6)
  T2.8 (独立)

P3 标注与联动 (依赖 P2)
  T3.1 ─→ T3.2 (拖拽)
  T3.3 (联动，依赖 T2.2 + T2.4)
  T3.4 ─→ T3.5 (标注后端→前端)
  T3.6 (版本管理)

P4 对话矫正 (依赖 P3)
  T4.1 ─→ T4.2 ─→ T4.3 (后端)
  T4.4 ─→ T4.5 (前端)
  T4.6 (独立)

P5 API 生成 (依赖 P1，可与 P3/P4 并行)
  T5.1 ─→ T5.3 (API 定义 → 提取)
  T5.2 (API Key，独立)
  T5.4 (前端，依赖 T5.1)
  T5.5 ─→ T5.6 (管理页)

P6 收尾 (依赖全部)
  T6.1 → T6.2 → T6.3 → T6.4 → T6.5
```

---

## 并行策略

如果有 2 名开发者，推荐分工：

| 工作日 | 开发者 A（后端为主） | 开发者 B（前端为主） |
|--------|---------------------|---------------------|
| D1 | T0.1 + T0.2 | T0.5 |
| D2 | T0.3 + T0.4 | T0.6 + T0.7 |
| D3 | T1.1 + T1.2 | T1.4 + T1.5 |
| D4 | T1.3 | T1.6（联调） |
| D5 | T3.4 | T2.1 + T2.2 |
| D6 | T5.1 | T2.3 + T2.4 |
| D7 | T5.2 + T5.3 | T2.5 + T2.6 |
| D8 | T4.1 | T2.7 + T2.8 |
| D9 | T4.2 + T4.3 | T3.1 + T3.2 |
| D10 | T3.5（联调） | T3.3 + T3.6 |
| D11 | T4.6 | T4.4 + T4.5 |
| D12 | T5.4（联调）| T5.5 + T5.6 |
| D13 | T6.1 + T6.3 | T6.2 |
| D14 | T6.4 + T6.5 | Bug fix + 视觉微调 |

**14 个工作日 ≈ 3 周**（两人并行）。

---

## 里程碑检查点

| 检查点 | 时间 | 标准 |
|--------|------|------|
| **M1: 能跑起来** | D2 结束 | 前后端分别启动成功，GET /health 和 localhost:5173 可访问 |
| **M2: 能上传** | D4 结束 | 上传文件 → 加载动画 → 返回 mock 结果 → 步骤跳到 1 |
| **M3: 能看到** | D8 结束 | 三栏布局完整，A 栏文档+bbox，B 栏字段列表，C 栏 JSON 输出 |
| **M4: 能编辑** | D10 结束 | bbox 拖拽、字段编辑、三栏联动、标注保存、版本回溯 |
| **M5: 能对话** | D11 结束 | 自然语言矫正 → SSE 回复 → 字段更新 → 版本递增 |
| **M6: 能调用** | D12 结束 | 生成 API → 用 API Key + curl 调用提取端点成功 |
| **M7: 全流程** | D14 结束 | 从上传到 API 调用的完整 6 步走通，Mock 模式全自动测试通过 |
