# 代码库结构地图（六大"库" + 样本角色 + 命名约定）

> 接手定位用的「东西在哪、为什么在那」的单页索引。配套架构红线见 [CLAUDE.md](../CLAUDE.md)。
> 最近核对：2026-06-26。

---

## 一、六大领域"库"映射

| 领域 | 物理位置 | 关键说明 |
|---|---|---|
| **国家模板库** | 仓库根 `<COUNTRY>_invoice_prompt.yaml`（MY/JP）；加载器 `ocr_optimizer/service/template_loader.py` | 经 `_REPO_ROOT = parents[4]` 在**仓库根** glob，部署镜像到 `/opt/docapi/*.yaml`。顶层 `locked_fields:` 声明国家锁定字段。**见 §三根目录耦合** |
| **反思路由库** | `ocr_optimizer/reflection/skills/*.yaml`（7 个）+ `reflection/skills_loader.py` | 内部反思能力，按 `edit_intent` 路由反思提示词。静态、PR 维护、不入库。**"skill" 的第①义，见 §二** |
| **技能库（OcrSkill）** | `ocr_optimizer/service/skill_service.py` / `skill_render.py` / `ocr_optimizer/models.py` 的 `OcrSkill` 表 | 面向客户/管理员、存 DB、可挂 module 的可复用规则。`api_definition_id` NULL=全局/非空=私有。**"skill" 的第②义** |
| **测试样本库** | 见 §四（三类角色，分散是**有意的**） | Japan-inv（开发基准）/ golden_set（冻结回归）/ runtime uploads（运行时） |
| **交互页面模板库** | `frontend/src/components/{workspace-v2,templates,fields,document,api,admin}` + `pages/` | 现行工作区是 `workspace-v2`（10 处引用）；`workspace/`（旧）疑似死代码（0 外部路径引用，待确认后清） |
| **opt skill 与优化测试库** | `ocr_optimizer/skilltrain/`（11 机制文件）+ `ocr_optimizer/eval/`（基准/golden 工具）+ `backend/tests/skill_opt/`（54 用例） | ReflACT 纪律机制 + Japan-inv 基准。详见 [skill-optimization-as-built.md](./skill-optimization-as-built.md) |
| **租户 api 库** | `models/api_definition.py`（API 定义）+ `OcrSkill.api_definition_id` 非空（私有技能） | 多租户：每个 API 私有版本 + 私有技能；优化作用在私有层 |

---

## 二、"skill" 的两义（命名消歧，勿混）

代码里 `skill` 一词有**两个无关概念**，已在源码加交叉引用注释（`skills_loader.py` / `skill_service.py`）：

| | 反思路由（reflection skill） | 技能库（OcrSkill） |
|---|---|---|
| 类名 | `reflection/` 包里的**裸 `Skill`** | 永远带前缀的 **`OcrSkill`** |
| 位置 | `reflection/skills_loader.py` + `reflection/skills/*.yaml` | `service/skill_service.py` + `models.py` 表 |
| 是什么 | 「**怎么反思**」——按 `edit_intent` 路由一段反思提示词 | 「**可复用识别规则**」——客户/管理员策展的内容 |
| 谁维护 | 产品/技术 PR（静态、不演化） | 客户/管理员 CRUD（优化器**禁止写**） |
| 是否入库 | 否（代码内 yaml） | 是（DB，有 `api_definition_id`） |
| 是否客户可见 | 否（纯内部） | 是 |

> **判别口诀**：带 `Ocr` 前缀 / 有 `api_definition_id` / 在 `service/` → 技能库；
> 裸 `Skill` / 在 `reflection/` → 反思路由。
>
> 历史背景：曾考虑把 `reflection/skills/` 改名为 `routes/` 彻底消歧，但其词汇深嵌生产反思
> 核心（5 代码 + 2 测试 + CLAUDE.md 11 处），纯命名收益、却有生产回归风险（同 ADR Tier 2 取舍）→
> **选择文档 + 代码交叉引用消歧，不做运行时改名。**

---

## 三、根目录耦合（国家模板，勿乱动）

`template_loader.py`：
```python
_REPO_ROOT = Path(__file__).resolve().parents[4]      # = 仓库根
_REPO_ROOT.glob("*_invoice_prompt.yaml")              # MY/JP...
_REPO_ROOT / f"{country.upper()}_invoice_prompt.yaml" # 单个加载
```
- **后果**：国家模板**必须**放在仓库根，且文件名严格 `<COUNTRY>_invoice_prompt.yaml`（`.bak` / `_templet` 等不匹配、不会被加载）。
- **部署**：服务器镜像到 `/opt/docapi/<COUNTRY>_invoice_prompt.yaml`（与 `backend/` 同级）；
  或设 `COUNTRY_TEMPLATE_DIR` 环境变量直接指定模板目录（backend 单独打包时不必复刻仓库层级）。
- **移动代价**：搬到 `templates/` 需同时改 `parents[4]`、服务器布局、rsync 目标 → 风险 > 收益，**当前不动**。

---

## 四、测试样本的三类角色（分散是有意的，别合并）

| 角色 | 位置 | git | 用途 |
|---|---|---|---|
| **开发基准** | `Japan-inv/`（根） | gitignore | 363 对 ML 划分（train/val/test），开发期 bench harness 用，**不进生产路径** |
| **冻结回归** | `ocr_optimizer/eval/golden_set/{MY,JP}/` | GT 答案入库、`docs/` 原始扫描 gitignore | 零容忍严格回归，发版前跑 |
| **运行时** | `backend/data/`、`backend/static/uploads/` | gitignore | 客户上传 + SQLite，运行时产物 |
| **本地手测语料** | `testing/`（根） | **gitignore**（含真实发票 PII，勿入库） | 开发者本地手测，**不入库** |

> **PII 约定**（全库统一）：真实发票扫描**一律不入库**；golden_set 只留 GT 答案 key + manifest +
> 可由 `build_golden_set.py` 再生。`testing/` 曾违规跟踪 48 个 PDF，已于 2026-06-26 取消跟踪（Tier 0）。

---

## 五、`archive/`

被取代但保留出处的源工件（不参与运行时），见 [archive/README.md](../archive/README.md)。

---

## 六、服务层依赖方向（app/services ↔ app/ocr_optimizer）

**目标方向**（新代码必须遵守）：

```
api/v1 ──► app/services ──► app/ocr_optimizer/service ──► processors
                │                     │
                └──── app/models ◄────┘
```

- `app/services`（文档/标注/租户等通用服务）**可以** import `ocr_optimizer`；
- `ocr_optimizer` **不得**新增对 `app/services` 的依赖——它是被编排的引擎层，
  不该反过来了解上层业务服务。

**现存反向依赖（已知技术债，靠函数内延迟 import 苟着，勿再扩大）**：

| ocr_optimizer 侧 | 依赖的 services | 原因 |
|---|---|---|
| `customer_iteration`（8 处）、`run_orchestrator`、`field_constraints` | `pending_edits_service`（overlay） | overlay（客户字段草稿）本质是优化域概念，却住在 services |
| `customer_iteration` | `document_service._rewrite_structured_data_keys` 等 | re-OCR / 数据同步借用了文档服务的纯函数 |

**还债方向**（择机做，不阻塞特性）：把 overlay 读写抽成两侧都可依赖的中立
域模块（如 `app/domain/overlay.py`），`document_service` 的 OCR 后处理纯函数
（`_flatten_hierarchical` / `_normalize_structured_data` / `_rewrite_structured_data_keys`
等 ~480 行零 I/O 代码）拆成 `extraction_pipeline.py`——反向依赖即可归零，
`app/models/__init__.py` ↔ `ocr_optimizer/__init__.py` 的懒加载破环 hack 也可拆除。

## 七、迭代引擎的模块拆分（结构审查 1.1，2026-07 落地）

`customer_iteration.py` 曾是 2449 行的巨模块（9 个职责块混住）；已按职责
facade 式拆出以下模块（`customer_iteration` 保留重导出，调用方/测试零改动）：

| 模块 | 职责 | 为何独立 |
|---|---|---|
| `version_selection.py` | 单调守护（红线④）：`_best_evaluated_version` / `_confirm_version_accuracy` / `_tie_band` | 「准确率永不下降」的实现，必须能被单测直接打靶 |
| `reflection_context.py` | 反思语料：跨样本对照 + 全文检索输出（纯读 DB） | 与迭代主逻辑无关的证据收集 |
| `doc_sync.py` | 定制/迭代后文档同步（Phase 23.3 rename sweep + Phase 25 re-OCR） | 纯 DB / 文档同步 |
| `customize_fork.py` | 版本 bump / 模块克隆 / 新增字段 LLM 扩展（约 770 行，最大一块） | fork 构造逻辑，不驱动迭代 |

`run_orchestrator._run_one_round`（曾 600 行单函数）已按 Step 切成
`_evaluate_round` / `_optimize_failing_modules` / `_compose_next_version` +
提出的 `_optimize_and_verify` / `_typed_optimize`（原嵌套闭包，可单测）。

**评分单一事实源**：轮内评分不再手写循环，统一走 `eval/harness.score_outputs`
（此前 orchestrator 与 harness 各写一份刻意相同的评分逻辑，改评分要两边同步）。

**刻意留在 `customer_iteration`**（拆出反而增加循环依赖，收益低）：
样本门禁常量（`MIN_SAMPLES_FOR_ITERATION` / `required_samples()` / 置信度阈值，
被流水线/job 状态机/resume 到处共享）、job 状态机（`submit`/`run`/`resume`/`reap`
与 `_execute_pipeline` 深度纠缠）。
