# 设计与开发计划 · 结构优化第二轮（服务层还债 + 前端结构）

> 状态：待执行 ｜ 前置：结构第一轮已完成（见 repository-structure.md §七）
> 原则延续第一轮：**facade 重导出、调用方零改动、逻辑零变更、每步全量验证独立提交**。

---

## 0. 背景与目标

第一轮结构审查遗留两块高价值债务：

1. **服务层双向依赖**：`app/services ↔ app/ocr_optimizer` 互相 import 10+ 处，
   靠函数内延迟 import 苟活；`app/models/__init__.py ↔ ocr_optimizer/__init__.py`
   还有一个懒加载破环 hack。耦合枢纽是 **overlay**（客户字段草稿）——优化域
   概念住在 `app/services/pending_edits_service.py`（586 行）。
2. **前端巨型文件**：`DarkFieldViewer.tsx` 2431 行（14 个组件挤一个文件）+
   `workspace-store.ts`/组件内 10 处裸 URL 调用绕过 api-client。

目标：**依赖方向单向化**（api/v1 → services → ocr_optimizer → processors，
domain 为两侧共享底座）+ **前端调用面收敛与文件拆分**。全程不改行为。

```
目标依赖图：
api/v1 ──► app/services ──► app/ocr_optimizer/service ──► processors
                │                     │
                ├──── app/domain ◄────┤   ← 新增中立层（overlay / 提取后处理纯函数）
                └──── app/models ◄────┘
```

---

## 1. 工作流 A · 服务层还债（backend）

### A0（先行）依赖方向防回归测试 —— 先立网，再还债

**为什么先做**：还债的成果没有防回归机制就会被下一个「顺手 import」吃掉。

**设计**：`tests/test_dependency_direction.py`，用 `ast` 扫描
`app/ocr_optimizer/**/*.py` 的全部 import 语句（含函数内延迟 import——遍历
所有 `Import`/`ImportFrom` 节点即可覆盖）：

```python
ALLOWED_REVERSE = {
    # 现存债务白名单（还债过程中逐条删除，删空即毕业）
    "app/ocr_optimizer/service/run_orchestrator.py": {"app.services.pending_edits_service"},
    "app/ocr_optimizer/service/customer_iteration.py": {"app.services.pending_edits_service"},
    "app/ocr_optimizer/service/customize_fork.py": {"app.services.pending_edits_service"},
    "app/ocr_optimizer/service/field_constraints.py": {"app.services.pending_edits_service"},
    "app/ocr_optimizer/service/doc_sync.py": {"app.services.document_service"},
}
# 断言：ocr_optimizer 内任何对 app.services.* 的 import 必须在白名单内
```

**DoD**：测试进主干；白名单与现状精确一致（跑一次全扫校准）。
**工作量**：~1 小时。风险：低。

---

### A1（核心刀）overlay 抽中立域模块 `app/domain/overlay.py`

**现状**（`app/services/pending_edits_service.py`，586 行）：

| 函数 | 性质 | 去向 |
|---|---|---|
| `_empty` / `_normalize` / `get_overlay` / `get_overlay_by_doc` / `_save_overlay` / `clear_overlay` | 纯 overlay CRUD（只依赖 models） | → domain |
| `record_field_feedback` / `record_rename` / `record_added_field` / `record_modification` / `record_deleted_field` | 记录客户编辑（只依赖 models） | → domain |
| `record_field_constraint` | 依赖 `_locked_set` | → domain（锁定集参数化，见下） |
| `compute_required_field_set` / `_observed_top_level_keys_from_confirmed` | 读标注推导字段集（只依赖 models） | → domain |
| `cascade_rename_annotations` | 级联改 Annotation（只依赖 models） | → domain |
| `_locked_set` | **反向依赖 ocr_optimizer.field_constraints** | 留在 facade（见下） |

**唯一的结** —— `_locked_set`（line 96）调 `field_constraints.locked_fields_for_api`
（引擎层）。domain 不得依赖引擎，两个解法选 **参数化**：

```python
# domain/overlay.py — 不查锁定集，由调用方注入
def record_field_constraint(db, api_def_id, *, field, constraint,
                            locked: set[str] | frozenset[str] = frozenset()) -> dict: ...
```

`app/services/pending_edits_service.py` **保留为兼容 facade**（沿用第一轮模式）：
重导出全部公开函数；仅 `record_field_constraint` 包一层——先查 `_locked_set`
再委托 domain。这样：
- ocr_optimizer 侧的 10+ 处 `from app.services import pending_edits_service`
  逐一改为 `from app.domain import overlay`（**反向依赖就地消失**，白名单删行）；
- api/v1 与 services 侧调用方零改动（facade 不动）。

**迁移步骤**（每步跑全量 pytest）：
1. 建 `app/domain/__init__.py` + `overlay.py`，函数体原样搬移 + `record_field_constraint` 参数化；
2. `pending_edits_service.py` 改为 facade（重导出 + locked 包装）；
3. ocr_optimizer 侧 5 个文件的延迟 import 改指 `app.domain.overlay`，同步删 A0 白名单对应行；
4. 尝试拆除 `app/models/__init__.py` 的懒加载 hack（若 conftest 冷启动循环随之消失；不消失则记录原因、不硬拆）。

**DoD**：全量 pytest 绿；A0 白名单只剩 `doc_sync → document_service` 一行。
**工作量**：0.5–1 天。风险：中（触碰 10+ 文件，但每处是单行 import 改写；
测试覆盖：test_pending_edits.py 586 行专测 overlay 行为）。

---

### A2 提取后处理纯函数 → `app/domain/extraction_pipeline.py`

**现状**：`document_service.py`（1079 行）中 ~480 行**零 I/O 纯函数**，且被
引擎侧反向借用（`doc_sync.py` 借 `_rewrite_structured_data_keys`）：

| 函数（行号） | 依赖 | 去向 |
|---|---|---|
| `_infer_field_type`(162) `_normalize_bbox`(467) `_is_leaf_field`(500) `_flatten_hierarchical`(515) `_normalize_structured_data`(566) `_field_top_level`(630) `_project_to_field_set`(641) `_pad_with_required_keys`(724) `_rewrite_structured_data_keys`(757) `_infer_schema`(786) | **零依赖**（纯数据变换） | → domain |
| `_apply_field_constraints`(690) | 调 ocr_optimizer.field_constraints | **留在 document_service**（它是编排不是纯函数） |

**步骤**：10 个纯函数搬 domain → document_service 顶部 re-import（内部调用零改动）
→ `doc_sync.py` 改 import `app.domain.extraction_pipeline`（删白名单最后一行）。

**附带收益**：这批纯函数首次变得可直接单测——补一个
`tests/test_extraction_pipeline.py`（flatten/normalize/rewrite 的快照用例，
~10 个，保护未来改动）。

**DoD**：白名单删空（`ocr_optimizer → app.services` 反向依赖归零）；纯函数有单测。
**工作量**：0.5 天。风险：低（纯函数搬移）。

---

### A3 `commit_draft_to_overlay` 下沉（第一轮 F5 遗留）

**现状**：`api/v1/api_defs.py:160-259`——100 行 6-case 业务分发写在路由
（rename+级联 / add / modification / delete / field_constraint / field_feedback）。

**设计**：下沉为 `app/domain/overlay.apply_draft(db, api_def_id, body, *, locked) -> dict`
（正好落进 A1 建好的 domain 模块）；路由留 10 行编排（解析 body → 查 locked →
调 apply_draft → 返回 overlay）。

**DoD**：路由函数 ≤ 15 行；行为逐 case 与现状一致（test_pending_edits 全绿 +
针对 6 个 case 各补一个 apply_draft 单测）。
**工作量**：0.5 天。风险：低-中（分发逻辑逐字搬移，新增单测护住）。

---

## 2. 工作流 B · 前端结构（frontend）

> 验证手段：无测试套件 → 每步 `npm run build`（tsc -b + vite）+ `npm run lint`
> + preview 冒烟（字段编辑/新增字段/定制保存三条主交互路径）。

### B1 API 调用面收敛：补 api-client wrapper

**现状**：10 处裸 URL 绕过 `lib/api-client.ts`（~80 个 wrapper 里恰好缺这几个）：

| 端点 | 裸调用位置 |
|---|---|
| `POST …/pending-edits/commit-draft` | workspace-store.ts :1148 :1177 :1185 :1200 :1228 :1284 |
| `DELETE …/pending-edits` | workspace-store.ts :1241、DarkFieldViewer.tsx :1358 |
| `POST /documents/{id}/annotations` | DarkFieldViewer.tsx :947 |
| `POST …/customize-jobs/{id}/resume` | DarkFieldViewer.tsx :1338 |
| commit-draft（组件内直调，绕过 store 同名动作） | DarkFieldViewer.tsx :985 |

**设计**：api-client 增 4 个 wrapper——`commitDraftToOverlay(apiDefId, body)`、
`clearPendingEdits(apiDefId)`、`saveAnnotations(docId, payload)`、
`resumeCustomizeJob(jobId)`；10 处逐一替换。DarkFieldViewer:985 那处改调
workspace-store 的既有动作（消除「同一端点两条调用路径」）。

**DoD**：`grep -rn "api-definitions.*pending-edits" src/ | grep -v api-client` 为空。
**工作量**：0.5 天。风险：低。

### B2 DarkFieldViewer 拆分（2431 行 → 目录）

**现状对拆分非常有利**：文件内部已按 `───` 分节组织成 14 个独立组件 +
模块级常量，无隐式共享状态（状态都在 workspace-store / 各组件自身 hooks）。

**设计**：新目录 `components/workspace-v2/field-viewer/`，按既有分节一刀一文件：

```
field-viewer/
  shared.ts            ← FORMAT_OPTIONS / FIELD_TYPES / ARRAY_LABEL_RE / 类型（:28-92）
  TypeSelector.tsx     (:95)      FieldRow.tsx        (:167)
  NewFieldRow.tsx      (:388)     ArrayTable.tsx      (:450)
  PendingFieldsBar.tsx (:627)     FieldEditPanel.tsx  (:693)
  MissingFieldsList.tsx(:904, 含 FIELD_LIST_STYLE)
  AddFieldList.tsx     (:1103)    WaitingForSamplesBanner.tsx (:1223, 含 MIN/MAX_NEW_SAMPLES)
  CustomizeBar.tsx     (:1573)    FieldsView.tsx      (:1740)
  RulesView.tsx        (:2305)    StatsView.tsx       (:2357)
DarkFieldViewer.tsx    ← 薄入口：主组件(:2423) + re-export，import 方零改动
```

**顺序**：先切叶子组件（TypeSelector/FieldRow/ArrayTable，无相互依赖）→
组合组件（FieldEditPanel/FieldsView）→ 每切 2-3 个跑一次 build。B1 先行，
避免拆分把裸 URL 复制进新文件。

**DoD**：`DarkFieldViewer.tsx` ≤ 150 行；build + lint 绿；preview 冒烟三条
主交互路径截图确认。
**工作量**：1 天。风险：中（无测试兜底 → 靠 tsc 严格类型 + 冒烟；纯搬移不改 JSX 结构）。

### B3（本轮不做，挂账）

`OptimizationProcessPanel.tsx`（1574 行）与 `OcrOptimizer.tsx`（1058 行）：
偏大但内部无重复封装、改动频率低——等 B2 的拆分模式验证后再决定是否套用。

---

## 3. 执行顺序与提交切分

依赖关系：A0 → A1 → A2 → A3（A3 依赖 A1 的 domain 模块）；B1 → B2；A/B 两线独立可交错。

| # | 步骤 | 预估 | 提交 |
|---|---|---|---|
| 1 | A0 依赖方向防回归测试 | 1h | `test(arch): 依赖方向防回归（白名单制）` |
| 2 | A1 overlay → domain | 0.5-1d | `refactor(domain): overlay 抽中立域模块，ocr_optimizer 反向依赖 -5` |
| 3 | A2 extraction_pipeline → domain | 0.5d | `refactor(domain): 提取后处理纯函数下沉 + 补单测（反向依赖归零）` |
| 4 | A3 commit_draft 下沉 | 0.5d | `refactor(api): commit_draft_to_overlay 业务分发下沉 domain` |
| 5 | B1 api-client wrapper 收敛 | 0.5d | `refactor(frontend): API 调用面收敛到 api-client（消 10 处裸 URL）` |
| 6 | B2 DarkFieldViewer 拆分 | 1d | `refactor(frontend): DarkFieldViewer 按既有分节拆目录（2431→150 行）` |

总计 **3.5–4 天**。每步：全量 pytest（backend）/ build+lint+冒烟（frontend）→ 独立提交 → 可随时停在任意步。

## 4. 风险与回退

| 风险 | 缓解 |
|---|---|
| A1 触碰 10+ 文件的 import | 每处单行改写；test_pending_edits（586 行）+ 全量回归护航；facade 保证 api/v1 侧零感知 |
| domain 引入新循环（domain ← models ← ocr_optimizer.models 注册链） | domain 只 import `app.models.*` 具体模块，不碰 `app.models.__init__` 聚合口 |
| B2 无测试兜底 | 纯文件搬移不改 JSX；tsc 严格模式挡引用错误；preview 三路径冒烟；一刀一 commit 可 revert |
| `_locked_set` 参数化改变 record_field_constraint 语义 | facade 层保持原签名原行为，参数化仅存在于 domain 层 |
| 懒加载 hack 拆不掉 | A1 步骤 4 是「尝试」：拆不掉就记录原因保持现状，不阻塞 |

## 5. 完成定义（整轮）

- [ ] `test_dependency_direction.py` 白名单 = ∅（反向依赖归零）
- [ ] `app/domain/` 建立：overlay.py + extraction_pipeline.py，均有直接单测
- [ ] `commit_draft_to_overlay` 路由 ≤ 15 行
- [ ] 前端零裸 URL（api-client 外无 `/api/v1` 字符串，代码示例除外）
- [ ] `DarkFieldViewer.tsx` ≤ 150 行
- [ ] 全量 pytest 绿 + 前端 build/lint 绿 + 冒烟通过
- [ ] repository-structure.md §六/§七 更新（债务表清零、新增 domain 层说明）
