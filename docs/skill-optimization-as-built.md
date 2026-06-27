# 技能优化（ReflACT / Option B）— As-Built 实现参考

> **这是「线上实际跑的是什么」的单一事实源。** 配套设计见 [ADR-001](./ADR-001-skill-optimization-reflact.md)
> 与 [开发计划](./skill-optimization-plan.md)（两者为决策/规划口径，本文为落地口径）。
> 状态：**P0/P0'/P1/P2 已实现并上线**（invoicase.cn / 47.121.179.253）；P3/P4/P5 部分延后（见 §9）。
> 最近核对：2026-06-26。

---

## 1. 总览：四个特性开关

全部能力由 4 个 flag 门控，**代码默认 `False`（关）**，**线上 `.env` 全部 `true`（开）**。
默认关确保「不回归现有线上行为」——任何环境不显式打开即退化为旧链路。

| Flag（`backend/app/core/config.py`） | 代码默认 | 线上值 | 作用 |
|---|---|---|---|
| `SKILL_HELDOUT_GATE` | False | **true** | 留出验证门：在 val 子集上判版本好坏，复用既有单调 finalize |
| `SKILL_NOISE_GATE` | False | **true** | 噪声样本门：迭代前要求 3 锚点 + 9 噪声 = 12 张已确认样本 |
| `SKILL_EDIT_DISCIPLINE` | False | **true** | 编辑纪律：类型化有界 edit、Clip top-L、缺陷/失误判别、被拒缓冲 |
| `SKILL_LIBRARY_RENDER` | False | **true** | 技能库渲染：composer 把全局/私有技能拼进 module body |
| `SKILL_HELDOUT_VAL_FRAC` | 0.25 | 0.25 | 留出门 val 占比（数值参数，非开关） |

> 线上核对（2026-06-26）：`ssh 47.121.179.253 grep SKILL_ /opt/docapi/backend/.env` → 四项 `true`；`systemctl is-active docapi.service` → active。

---

## 2. 噪声样本门（`SKILL_NOISE_GATE`）

**目的**：3 张精挑干净样本上优化 = 在训练集上自评 = 过拟合。引入「噪声样本」作为留出验证集。

- **门槛**：迭代启动前需 **3 锚点 + 9 噪声 = 12 张已确认样本**。
  常量在 `skilltrain/noise_gate.py`：`ANCHORS_DEFAULT=3`、`NOISE_DEFAULT=9`、`required_total()=12`。
- **锚点（3）**：客户精选、逐张复核确认 GT → 训练信号。
- **噪声（9）**：故意多样（不同开票方/版式/税率/扫描质量）→ held-out 验证集。
- **噪声 GT 策略**（用户拍板）：**自动以当前 OCR 为基线，不逐张复核，上传即启动**。
  Gate 在噪声上退化为「优化锚点时不让这 9 张回归」的稳定性守护。

### 2.1 前端交互（已上线）

`frontend/src/components/workspace-v2/`：

1. **`DarkFieldViewer.tsx`** `WaitingForSamplesBanner`：3 锚点已审视后，横幅提示
   「即将启动自动迭代优化，请额外上传 N 份多样化噪声样本」+ 绿色「批量上传 N 份噪声样本 →」按钮
   （条件 `remaining > 0 && required > 3 && confirmed >= 3`）。
2. **`NoiseSampleModal.tsx`**（新）：一次性多选文件 + 「已选 X/9」计数器（差/多都提示）+ 进度条；
   **上传按钮仅在正好 9 张时激活**。上传逐张 `addSampleDocument` → `confirmSampleGT(id, true)`（自动基线 GT）→
   凑满 12 → 若定制 job 处于 `waiting_for_samples` 则 `pollCustomizeJob()` 自动续跑，否则 `triggerOptimization()`。

### 2.2 后端门（已上线）

- `api/v1/api_defs.py` 的 `samples-review` 接口：`SKILL_NOISE_GATE` 开时返回 `required_for_iteration = required_total()`（=12）
  + `noise_gate_active` / `anchors` / `noise_target`。
- `ocr_optimizer/service/customer_iteration.py` 的 `required_samples()`：开门=12 / 关门=3；
  定制流全部 gate（sample_confidence、submit、resume、auto-resume、`_execute_pipeline`）统一走它。
- 确认样本即触发后端 `maybe_auto_resume_for_api`（`api_defs.py` background task），凑满自动续跑。

---

## 3. 留出验证门（`SKILL_HELDOUT_GATE`）

**核心巧思：不新增任何 OCR 调用，靠「改变 `overall_accuracy` 度量谁、optimize 谁」复用既有单调 finalize。**

- `run_orchestrator.py` 在 `SKILL_HELDOUT_GATE` 开时：
  - 把已确认样本按 round 切 **train / val**（`skilltrain/heldout.py` `val_ids` / `split_accuracy`，val 占比 `SKILL_HELDOUT_VAL_FRAC=0.25`，少样本走滚动留一）；
  - **只在 train 上 optimize**module；
  - 把 `rnd.overall_accuracy` 设为 **val 分** → 既有单调 `_best_evaluated_version` 选择天然变成「按泛化分选版本」。
- **零额外 token**：每轮 step-1 本来就把 12 张全 OCR 一遍；Gate 只是把其中 ~3 张划为 val（不参与 optimize，仅 accept/reject），复用同一批 OCR 结果（§见计划 5.2）。
- **少样本对策**：留出地板 `n >= 8` 才启用真切分（否则样本太少 val 噪声盖过信号）。

> 验证：flag-on 的 `_run_one_round` 集成测试（off=0.6 / on=0.0，证明确实改成 val 计分）；
> 真实 Japan-inv 全链路跑（9 train / 3 val、val_acc=0.4545、edit-discipline 把 3→2 个目标收窄、paused_for_review、无崩溃）。

---

## 4. 编辑纪律（`SKILL_EDIT_DISCIPLINE`）

把「整段重写 module prompt」换成有界、可追溯的类型化编辑。`skilltrain/` 包：

| 机制 | 文件 | 说明 |
|---|---|---|
| 类型化 Edit | `types.py` | `FieldEdit{op∈append/replace/delete, target, content, support_count, source_type}`、`RolloutScore{hard,soft,fail_reason}` |
| Gate 判定 | `gate.py` | `decide` / `rolling_leave_one_out` / `score` |
| Clip / 自主 LR | `clip.py` | `rank_and_select`（按支持度取 top-L）、`decide_L`（L 随未达标严重度自适应） |
| 聚合 | `aggregate.py` | 跨样本合并 + 支持度计数 |
| 缺陷/失误判别 | `classify.py` | `SKILL_DEFECT`（改正文）vs `EXECUTION_LAPSE`（只进「# 客户反馈补充」附录）；**不确定默认 LAPSE** |
| 被拒缓冲 | `buffer.py` | `RejectedEditBuffer`：记住被拒 edit，下轮不再提 |
| 目标收窄 | `targeting.py` | `disciplined_targets`：只对未达标字段开优化，缩小「学习率」 |
| 驱动循环 | `driver.py` | `optimize_skill` ReflACT 循环 |
| 应用 | `apply.py` | `apply_edits` / `diff_line_count`（断言不再整段重写） |

接线点：`run_orchestrator.py` 的 edit-discipline 收窄（`targeting.disciplined_targets`）。

---

## 5. 字段治理三层（与本会话同期落地，技能优化的前置纪律）

优先级：**国家锁定 > 客户 override > 默认**。

### 5.1 国家锁定字段（country-lock）

- 声明在 `<COUNTRY>_invoice_prompt.yaml` 顶层 `locked_fields:` 列表（**运行时读、无 DB 列** → 改国策即时对该国所有 API 生效）。
- 当前 MY 与 JP 各锁 **4 个**：`invoiceNumber`（发票号码）、`invoiceDate`（开票日期）、`billFromName`（开票方名称）、`billFromTaxIdentificationNumber`（开票方税号）。
- 行为：识别规则钉死国家 spec、排除 Part-2 反思/优化、自定义面板**不允许增删改**。
- 代码：`template_loader.locked_fields_for(country)`、decompose 给 module 打 `locked` 标、`field_constraints.pin_locked_modules`、`pending_edits_service._locked_set` 守卫拒绝对锁定字段 add/rename/delete/constraint。

### 5.2 客户 override（field_constraints）

- 客户对单字段的类型/格式覆盖（如 `type=number` + 去空格/`-`/`_`/`*` 特殊符）**持久化、跨反思存活、压过 Part-1 通用常识**。
- 代码：`field_constraints.py`（`load`/`apply_to_modules`/`normalize_value`/`enforce`）；`document_service._apply_field_constraints` 在抽取后确定性 strip+coerce；`pending_edits_service.record_field_constraint` 落 overlay。
- `load()` **排除锁定字段**（锁定层优先）。

### 5.3 字段集投影

- `document_service._project_to_field_set`：模型自由发挥（VLM 不绑 schema）会吐超集；按 API 的 `compute_required_field_set` 投影 → JP（8 字段）丢掉多余 89 个，MY（全字段）= no-op。

---

## 6. 一等技能库（`SKILL_LIBRARY_RENDER`）

- **存储**：`OcrSkill` 表，`api_definition_id` NULL=全局（管理员策展、共享）/ 非空=私有。
- **渲染**：`skill_render.resolve(db, api_def_id, modules)`（flag 门控）→ `{module_key: 技能正文}`；`composer.assemble_prompt(..., skill_content=)` 追加「# 技能库补充（可复用规则）」块。
- **CRUD**：`skill_service.py`（list 私有+全局 / create / soft delete / attach_skill_to_module）；`router.py` 已接活原 501 端点。
- **前端**：`SkillLibraryModal.tsx` + `WorkspaceHeader.tsx`「技能库」按钮；`api-client.ts` 的 `OcrSkill` 类型 + fetch/create/delete/attach。
- **硬约束**：**优化器被严格禁止写技能**。技能只能由管理员策展或（未来 P4）经 golden_set 门 + 管理员确认晋级。当前全局库为空（待 P4 填充机制）。

---

## 7. 代码地图（快速定位）

```
backend/app/ocr_optimizer/
  skilltrain/                      # 纪律机制（开发期可单测、生产路径接线）
    types.py aggregate.py clip.py gate.py buffer.py classify.py
    targeting.py heldout.py noise_gate.py driver.py apply.py
  service/
    run_orchestrator.py            # 留出门 + edit-discipline + 噪声门 接线
    field_constraints.py           # 客户 override + 国家锁定 helper
    template_loader.py             # locked_fields_for / decompose 打 locked 标
    skill_service.py skill_render.py composer.py   # 技能库 CRUD/渲染/拼装
    customer_iteration.py          # required_samples() 与定制流各 gate
  eval/
    bench_japan_inv.py             # Japan-inv 基准 harness（train/val/test 三数）
    skill_demo_jp.py               # 真实 OCR+反思 demo
    baseline_jp_init8.json         # JP 基线 ~0.598
backend/app/services/
  document_service.py              # _project_to_field_set / _apply_field_constraints
  pending_edits_service.py         # overlay field_constraints + _locked_set 守卫
backend/app/api/v1/api_defs.py     # samples-review(=12) / required-fields(locked)
backend/app/core/config.py         # 4 flags + SKILL_HELDOUT_VAL_FRAC
backend/tests/skill_opt/           # 11 文件 / 54 用例
frontend/src/components/workspace-v2/
  NoiseSampleModal.tsx DarkFieldViewer.tsx SkillLibraryModal.tsx WorkspaceHeader.tsx
frontend/src/lib/api-client.ts     # OcrSkill 类型 + fetch/create/delete/attach
*_invoice_prompt.yaml              # 顶层 locked_fields: 列表（MY/JP 各 4）
```

---

## 8. 验证证据（核对 2026-06-26）

- **单测**：`pytest tests/skill_opt/` → **42 passed, 12 skipped**（skipped = 需真实 OCR/网络的用例，按设计跳过）。
- **留出门集成**：flag-off=0.6 / flag-on=0.0，证明 `overall_accuracy` 确实切到 val 计分。
- **真实 Japan-inv 全链路**：9 train / 3 val、val_acc=0.4545、edit-discipline 把目标从 3 收窄到 2、paused_for_review、无崩溃。
- **真实 skill-opt demo**：`billFromTaxIdentificationNumber` 0→100%、`billFromName` 0→50%，Gate 接受 round1 / 拒绝 round2。
- **基准诚实化**：bench 评分用 `fair_fields`（模板 ∩ GT），JP 诚实基线 ~0.598。

---

## 9. 与计划的偏差 & 未做项

**偏差（实现时的合理调整）**：
- 噪声 GT 用**自动基线**（不逐张复核）而非计划 §1.2 的「轻复核」——用户明确选择，Gate 退化为稳定性守护。
- 留出门**复用既有单调 finalize**（改度量对象），而非新建独立 gate 模块——零额外 OCR、零回归面。
- N 固定 **9**（总 12），未跑完整 §2.4-B 噪声扫描定肘点（扫描为开发期可选，未阻塞上线）。

**进行中 / 延后**：
- **P4** 迭代→技能晋升 ✅ 全链路打通：采收 → 候选(admin tab) → LLM 起草/管理员编辑确认 → 写全局库 →
  `SkillLibraryModal`「挂到字段」(attach→`module.skill_ids`) → composer 渲染注入识别。
  门槛 = 管理员确认(硬门) + 跨租户>5 自动推荐(可越级)。当前全局库仍为空(线上未实际 promote)。
  **⚠️ 关键修复(attach 静默失效)**:抽取用静态 `composed_prompt`，attach 仅改 `skill_ids` 不重组 →
  挂的技能不进模型。已修:`attach_skill_to_module` 后调 `recompose_version_prompt` 重组（复用 compose seam）。
  回归单测 + 生产 E2E 验证（JP_invoice_test1 挂「日元金额取整」→ total_amount，prompt 真渲染出技能块）。
  **唯一线上未跑的链节 = promote 写全局库**（避免污染策展库；draft 已冒烟、promote 由测试保证）。
  〔以下为该项历史细节，保留备查〕**步骤①采收 + 步骤②后端端点已落地**——`service/skill_promotion.py`（只读）
  把反思 `skill_feedback` 按 `(国家,字段)` 聚成候选 + 跨租户计数；`GET /api/v1/platform/skill-promotion/candidates`
  （平台 admin 门）暴露之，生产端到端冒烟通过、无 token→401。门槛 = **管理员确认（唯一硬门）+ 跨租户>5
  作自动推荐信号**（管理员可越级晋升低于阈值者；golden_set 不回归作参考、不硬卡）。生产 11 候选 / 0 推荐
  （仅 1 租户）。**步骤③后端已落地**：`draft`（qwen-plus 起草、不写库）+ `promote`（管理员确认→
  `create_skill(NULL)` 写全局库），draft 生产冒烟通过。技能正文来源拍板 = **LLM 起草 + 管理员编辑确认**。
  **前端已落地**：admin 控制台「技能晋升」tab（`pages/admin/SkillPromotion.tsx`）——候选列表 + 推荐徽标 +
  反思原文 + LLM 起草/编辑/晋升弹窗。**唯一剩余 = 步骤④ attach-UI**（把晋升的全局技能挂到字段 module 才渲染；
  后端基建已就绪，`SkillLibraryModal` 缺 attach-to-field 交互）。当前全局技能库仍为空（promote 未在线上触发）。
- **P3** 🔶 `slow_update` 已接线（`SKILL_SLOW_UPDATE`，默认 OFF）：按每字段跨轮准确率轨迹确定性产出
  受保护守护段（pin/caution），compose 时拼入、step 编辑碰不到；`meta_skill` 纯机制就绪未接线（待优化器产 typed edits）。
- **P5** 🔶 技能洞察已上线（`GET .../ocr-optimizer/skill-insights` + 工作区「洞察」按钮 + `SkillInsightsModal`）：
  每字段轨迹 + 守护徽标（复用 P3 `compute_guardians`）+ 已挂技能，一处显性；生产实跑验证。被拒 edit 展示待 meta 接线。

---

## 10. 部署 runbook（本会话所用）

后端：
```bash
git commit ...
rsync -az backend/<file> root@47.121.179.253:/opt/docapi/<path>
ssh root@47.121.179.253 systemctl restart docapi.service
```
前端：
```bash
cd frontend && npm run build
rsync -az --delete frontend/dist/ root@47.121.179.253:/opt/docapi/frontend/dist/
ssh root@47.121.179.253 nginx -t   # 校验；bundle hash 应与本地一致
```
- 服务：`WorkingDirectory=/opt/docapi/backend`，`.env` 在 `/opt/docapi/backend/.env`，DB 在 `backend/data/apianything.db`（文档 ID 为 32 位无连字符 hex）。
- 开关：上线特性靠 `.env` 的 4 个 `SKILL_*=true` + restart；回退即改 `false` restart（默认关，安全）。
- **数据驻留**：仅境内合规模型（qwen3-vl-plus / qwen-plus），真实数据**禁** Gemini/OpenAI。
- 临时脚本避坑：先 `import app.services / app.core.database` 再 import `app.ocr_optimizer.service.*`（否则循环导入崩）。
