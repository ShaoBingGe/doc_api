# CLAUDE.md — Doc API

> 接管本项目前请读完本文。Doc API 是一个**文档结构化提取平台**：从一份国家模板出发，
> 用 prompt 自迭代不断打磨「字段识别规则」，把任意票据/文档变成稳定可调用的提取 API。
> 本文给出**业务架构 + 技术架构 + 工程红线**，违反红线即视为 bug。

---

## 一、一句话定位

**全球机器 + 国别皮肤**：一台全球共享的「prompt 优化机器」，配上按国家维护的「识别知识」。
加一个新国家只写它的增量（模板 + 领域知识 + 黄金集），机器本身不分叉、全球复用。

输入：文档（PDF/图片）+ 客户对字段的编辑。
输出：一份稳定的 JSON 提取契约 + 一个可用 `X-API-Key` 调用的提取端点。

---

## 二、业务架构

### 2.1 核心心智模型：国家模板 → 定制 → 3 轮迭代

```
国家模板 (<COUNTRY>_invoice_prompt.yaml)
   │  客户在工作区编辑 / 新增 / 删除字段
   │  + 上传 ≥3 个样本并逐一「已审视」（人工核验为 Ground Truth）
   ▼
定制 customize ──► 在该 API 上创建新 prompt 版本 (OcrPromptVersion)
                │   • 复制现有字段模块 + 应用客户编辑（rename / add / delete）
                │   • 新字段用 LLM 反思扩展 description / ocr_prompt
                │   • 旧版本置 archived，API 指向新版本；api_code 不变（调用方无感）
                ▼
[最多 3 轮迭代] 每轮 = 拆分 → 局部验证 → 重组
                │   1. 用当前版本 OCR 所有「已审视」样本 → 逐字段评估准确率
                │   2. overall_accuracy ≥ 99.9% → 早停，复用当前版本
                │   3. 仅对未达标字段调 module_optimizer 改 prompt
                │   4. 判官 verify_module_fix：accept 才采纳，reject 保留旧 prompt
                │   5. composer 组装出新版本
                ▼
最终激活版本（API 指向它，工作区刷新即生效；并用最终 prompt 回跑全部样本统一输出）
```

**字段自迭代是产品内核**：客户只需「改字段 + 给样本」，机器负责把识别规则迭代到稳定。

### 2.2 架构基石：国别层 vs 全球层

不存在一份覆盖全球的「识别准则」——语言、票面、税号/日期/货币规则因国而异；
但**「找到值之后怎么拼成合法 JSON」是 schema 级、语言无关的**，必须全球统一。
所以：**全球标准存在于「输出形状」，不存在于「输入识别」**。

| 层 | 内容 | 归属 | 代码位置 |
|---|---|---|---|
| 国家事实 | 票据分类 / 语言 / 货币 / 日期 / 税号规则 | **按国家** | `<COUNTRY>_invoice_prompt.yaml`（Part 1） |
| 字段语义 | 每个字段「在哪找、找什么」 | **按国家** | 同上（Part 2） |
| 领域易错点 | 税号/双货币等国别坑 | **按国家** | `reflection/country_agents/<COUNTRY>/` |
| 黄金种子 | 验证文档 + 人工 GT + 参考 prompt | **按国家** | `eval/golden_set/<COUNTRY>/` |
| 输出契约 | 去千分位 / qty×price 校验 / 一行一项 / 缺字段处理 | **全球统一** | `assets/global_output_contract.yaml`（Part 3） |
| 优化机器 | composer / reconciler / harness / 反思 skill | **全球共享** | `service/`、`reflection/skills/` |

**准则**：国别知识只进国别层；机器永远全球共享（不按国家分叉）；分区主轴是**国家**不是纯语种；
比国家更细的差异（发行方/行业）交给客户自己的定制回路（用其样本拟合），不单独建层。

### 2.3 两条回路（务必分清，不互通）

| | 客户回路 | 黄金回路 |
|---|---|---|
| 触发 | 客户改字段 + 给样本 | 平台改机器（composer / skill / reconciler / 输出契约）时离线跑 |
| 数据 | 客户自己「已审视」样本 | 平台冻结的黄金集（每个字段都有人工 GT，无空值） |
| 评分 | **模糊**（容收敛、单调不降） | **零容差严格**（仅平台 A/B 比两版机器） |
| 优化标的 | 客户自己的 API prompt | **国家规范模板**（抬高所有客户起点 + 守机器，不动任何客户 API） |
| 红线 | — | **绝不接进客户路径、绝不按客户数烧 token**；只 gate 平台 PR，拦不下客户迭代 |

### 2.4 角色与权限（多租户）

| 角色 | 入口 | 能做什么 |
|---|---|---|
| 超级管理员 super_admin | 管理员入口（账号+密码，安装创建） | 建系统管理员、维护用户管理员、进模板优化平台 |
| 系统管理员 system_admin | 管理员入口 | 维护用户管理员、进模板优化平台 |
| 用户管理员 tenant_admin | 用户入口（邮箱+密码，平台核发） | 管理本租户普通用户、改自己密码、用产品 |
| 普通用户 normal_user | 用户入口（邮箱+验证码） | 仅使用客户产品 |

「模板优化平台」（国家模板 / 黄金种子 / 优化迭代）**只对平台管理员开放**。

---

## 三、技术架构

### 3.1 技术栈 & 目录

- **后端**：FastAPI + SQLAlchemy 2.0 + SQLite（原型；生产换 `DATABASE_URL` 即 PostgreSQL），端口 8000。
- **前端**：React + TypeScript + Zustand + TailwindCSS + Vite，端口 5173。
- **LLM**：生产 OCR 用 Gemini；反思/优化用 `LLM_FALLBACK_CHAIN` 失败链（失败静默降级到 `mock`，不阻塞）。

```
backend/app/
  api/v1/            HTTP 路由（auth / documents / api_defs / extract / ocr-optimizer …）
  core/              config / database / security(JWT+bcrypt+API Key) / deps / exceptions
  models/            ORM：Document Annotation ApiDefinition ApiKey User Tenant + OCR 优化表
  services/          业务服务层
  ocr_optimizer/
    assets/global_output_contract.yaml   Part 3 全球输出契约
    service/composer.py        组装 prompt（纯字符串拼接，永不调 LLM）
    service/template_loader.py 读国家模板 yaml → 字段模块
    service/reconciler.py      跨轮矛盾消解
    reflection/                反思 skill（yaml）+ 国别 agent + 公共基底
    eval/                      评测 harness + 黄金集 + CLI
frontend/src/        pages / components / stores / lib/api-client.ts
<COUNTRY>_invoice_prompt.yaml   国家模板（仓库根，只读）
```

### 3.2 数据模型（不变量）

| 模型 | 关键约束 |
|---|---|
| `ApiDefinition` | 一个可调用提取 API。`api_code` 唯一；状态见 §3.6 |
| `OcrPromptVersion.composed_prompt` | 非空，含 GLOBAL_PREAMBLE 前缀 |
| `OcrPromptVersion.composed_schema` | dict，`type:"object"` + 至少 1 个 property |
| `OcrPromptVersion.country_global_text` | 国家全局规则（Part 1+2）；新版本**原样沿用、不改写** |
| `OcrModule` | 一个字段模块。`json_path` 用 jsonpath 语法；`"$"`/`""` 表示全局 |
| `OcrModule.skill_ids` | 只读硬拷贝，optimizer 不能写 |
| `Annotation.is_corrected` | True = Ground Truth；**仅客户显式「已审视」/编辑触发，不允许自动批量置 True** |
| `User` / `Tenant` | 角色 + 可空 tenant_id；停用用 `is_active`，**不物理删**（审计资产） |

历史模块/版本/用户一律**软删除**（`status='archived'` / `is_active=False`），**永不 SQL DELETE**。

### 3.3 Prompt 四段平台契约（design 核心）

渲染顺序固定：

```
GLOBAL_PREAMBLE                 ← 任务说明 + 输出格式约束（写死在 composer.py）
country_global_text             ← Part 1 国家事实 + Part 2 字段识别要点（存 DB 列）
GLOBAL_OUTPUT_CONTRACT_DETAILS  ← Part 3 输出装配契约（assets/global_output_contract.yaml）
## 1..N 字段模块                ← 各 OcrModule 的识别指令
GLOBAL_SELF_CHECK               ← 输出前自检（写死在 composer.py）
```

- 三个 `GLOBAL_*` 段 + Part 3 资产是**平台契约**：客户 / 反思 agent / optimizer **不可改写**；
  仅平台工程师改源码 / yaml，且需 PR 审查 + 进程重启生效。
- Part 3 由 composer 每次组装时**从平台资产重新注入**，不快照、不可被某国特例污染。
- 反思只能改 **Part 2**（字段 schema 的 description / ocr_prompt），**不得动 Part 3 装配规则**。

### 3.4 核心引擎

- **composer**（`service/composer.py`）：把字段模块 + 四段契约拼成最终 prompt/schema。
  纯字符串拼接 + JSON merge，**永不调 LLM**；json_path 冲突 → `raise ValueError`，不静默覆盖；
  模块顺序由 `order_index` 决定，不在 composer 内重排。
- **反思 skill**（`reflection/skills/*.yaml`）：一个 diff 可命中多个 skill，各产一段 fix_suggestion；
  同字段多条反馈**累积不覆盖**；公共基底（泛化教义 + 输出 schema）放 `reflection/base/`，薄变体引用之。
- **reconciler**（`service/reconciler.py`）：当某字段 prompt 已含累积反馈、本轮又有新建议且**矛盾**时，
  调 LLM 协调成单一自洽 prompt，**冲突以最新客户意图为准**；fail-open（失败回退到累积追加）。
- **评测 harness**（`eval/harness.py`）：OCR + 逐字段打分；GT 与 json_path 根对齐
  （`ground_truth.align_for_path`，数组根路径下把 dict GT 包成 `[gt]`，避免假分）。

### 3.5 字段泛化（避免过拟合到固定坐标）

反思/新字段扩展时，必须用**相对锚点**（邻近标签/区块）描述位置，**禁用绝对坐标/固定行列号**；
跨样本观测要归纳一条「覆盖全部样本」的规则（`generalization{rule, evidence_per_sample, holds_for_all}`）。

### 3.6 API 状态机

```
pending_first_doc ─ 保存生成 ─► pending_review(待验证) ─ 激活发布 ─► active(已发布)
                                         ▲                              │ 停用
                                         └──────────  deprecated(已停用) ◄┘
```

### 3.7 认证

- **管理 UI / 角色管理**：`Authorization: Bearer <JWT>`（HS256，`core/security.py` 签发）。
- **公有提取端点** `/api/v1/extract/`：`X-API-Key`（只存 SHA-256 哈希，明文仅创建时返回一次）。
- 密码用 bcrypt 哈希。普通用户走「邮箱+验证码」，邮箱须由用户管理员预先开通。

---

## 四、工程红线（违反任一条 = bug）

### ① Prompt 四段契约不可侵犯
客户 / 反思 / optimizer 一律**不可改写** GLOBAL_PREAMBLE / Part 3 / GLOBAL_SELF_CHECK；
国家全局规则进 `country_global_text` 列，**不要**塞进 OcrModule 表；
输出契约（去千分位、qty×price 校验等）改 `global_output_contract.yaml`，走 PR。

### ② 字段 meta 必须保留，客户路径禁用 meta_optimizer
客户定制/迭代路径**全程 `enable_meta=False`**：不允许迭代器 add/remove/rename 字段（客户已手工拍板字段集）；
取源版本时选**模块最多**的版本（不是简单取 active，防历史损坏版本）；
per-module optimizer **只能改** prompt / description / suggestions，不能碰 skill_ids；
任何模块行只**归档**不删除。

### ③ composer 可回退、不调 LLM
schema 冲突 `raise ValueError`，调用方 `try/except`：迭代末尾回退到 `current_version.id`（合法的「无变化」信号），
定制时把失败暴露到前端；composer 内**绝不**引入 LLM 整理逻辑。

### ④ 每轮先「门口认证」+ 准确率单调不降
每轮入口先 OCR+eval：`overall ≥ 0.999` 立即早停复用，**早停必须在调 optimizer 之前**；
版本级回退守护：若新版本 `acc < 上一已激活版本 acc` → **丢弃新版本、保留旧版本**（记「无提升」，不算 failed），
**准确率永不下降**。前提：评分必须是 GT 根对齐后的真实值。

### ⑤ 反思按 skill 分流、可累积、矛盾才协调
新字段的 LLM 扩展必须在**建新版本前**完成（给第 1 轮一个完整起点）；
同字段多条反馈默认累积；仅当**跨轮矛盾**时才调 reconciler，产出协调后的 ocr_prompt（composer 仍渲染 ocr_prompt）。

---

## 五、失败处理：降级而非 500

- 上传是原子的；OCR 失败仅标 `doc.status=failed`，job 继续。
- LLM 失败走 `LLM_FALLBACK_CHAIN` → 最终 `mock`，job 继续。
- compose 失败 → 复用上一版本；该轮标 failed 但 job 不挂。
- **永不**自动把 OCR 输出标成 GT；**永不**让上传后 OCR 出错抛 500。

---

## 六、运行 / 调试

```bash
# 后端（端口 8000）
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000      # 启动即建表 + bootstrap 超级管理员

# 前端（端口 5173）
cd frontend && npm install && npm run dev

# 测试
cd backend && pytest -q                         # 独立 test DB，不污染 dev 库
```

| 任务 | 方法 |
|---|---|
| API 文档 | `http://localhost:8000/docs` |
| 看某 job 的反思摘要 | `GET /api/v1/api-definitions/customize-jobs/{job_id}` |
| 看某轮失败原因 | `OcrModuleIteration.optimization_suggestion`（含判官 reject 注释） |
| 反思没生效 | 查 `skill_count > 0`；为 0 多半是 LLM 不通或 match 谓词没命中 |
| 平台改机器后回归 | 在冻结黄金集上跑 `python -m app.ocr_optimizer.eval.run_golden_batch --country MY` |

---

## 七、改动前自检

1. 改迭代主循环：这条路径会被客户触发吗？→ 是 → 必须一路 `enable_meta=False`。
2. 改取源版本：选错会怎样？→ 跑「模块最多」的选择逻辑，别退回简单 active。
3. 改 composer：这次字符串改动会让老 prompt 解析不出 schema 吗？→ 跑 round-trip。
4. 改国别知识：是不是误塞进了全球层（Part 3 / 全局 skill）？→ 国别只进国别层。
5. 改机器：先在黄金集 A/B 验证「严格分不下降」，再合 PR；客户回路零影响。
