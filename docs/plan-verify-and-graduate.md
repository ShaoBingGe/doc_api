# 分级开发计划 · 收尾验证 + ADR-002 灰度 + 前端 B3 拆分

> 状态：L0 ✅ 完成（2026-07）｜ L1/L2 待执行
> L0 成果：黄金 CLI key 形态存量 bug 修复（`8937b13`，修复前 strict 恒 0、
> 黄金门从未真正工作过）→ qwen strict 0.65 基线建立；preview 冒烟路径 1
> 端到端实证 + 路径 2/3 控件验证，零 console 错误。
> 注：L0 期间 Gemini SSL 持续中断，黄金基线口径为 **qwen**；L1 灰度若
> Gemini 仍断，llm_provider 同样走 qwen 或等网络恢复。
> 原则沿用前几轮：每步独立提交、可随时停、失败有回退。

---

## 0. 三项工作的性质与排序理由

| 级 | 内容 | 性质 | 预估 | 依赖 |
|---|---|---|---|---|
| **L0** | 多行明细收尾验证 | 纯验证（不写代码，除非发现 bug） | 0.5d | Gemini/qwen 连通 |
| **L1** | ADR-002 typed-edit 优化器灰度 | 已完成功能的 flag 上线观察 | 1-2d（含观察） | L0 的黄金基线 + 同一 LLM 环境 |
| **L2** | 前端 B3 大文件拆分 | 纯结构重组（零行为变更） | 1d | 无（可在 L1 观察期穿插） |

**排序理由**：L0 最先——它验证的是已交付给用户的功能，且黄金回归顺手给
L1 一个准确率基线；L1 需要与 L0 相同的真实 LLM 环境，连着跑省环境切换；
L2 不需要 LLM 环境、与前两者零耦合，适合填 L1 的观察等待期。

---

## L0 · 多行明细收尾验证（~0.5d，plan-line-items.md 留下的两项）

### L0.1 黄金集回归（模板数组路径不回归）

```bash
cd backend && python -m app.ocr_optimizer.eval.run_golden_batch \
  --country MY --candidate my-invoice-abf4f0 --size 10
```

- **判据**：`strict_overall_accuracy` 不低于上次实测基线（数组根修复后
  ~0.725@5docs 口径；size=10 会略有波动，关注 `eval_valid=true` 且无骤降）。
  多行明细四阶段**没动模板路径**（P0-P3 只影响客户新增数组与列编辑），
  预期零变化——这一步是「确认没踩到」而非「验证新功能」。
- **失败处理**：分数骤降 → 先看 `ocr_error_docs`（网络抖动 ≠ 回归）；
  真回归 → 对比 `deviations` 定位到字段，回溯 P0-P3 提交逐个 revert 验证。

### L0.2 Preview 冒烟（新功能三条路径，真实工作区数据）

前置：后端 `uvicorn app.main:app --port 8000` + 前端 `npm run dev`，
用现有 MY 工作区（有已审视样本的 ApiDef）。

| # | 路径 | 验证点 |
|---|---|---|
| 1 | **新增带列数组**：字段区「加一行」→ 格式选 array → 列编辑器加 2-3 列 → 保存生成 | overlay `added_fields[].columns` 落库；触发定制后新版本模块 json_path=`$[*].{name}[*]`、composed_schema 行结构完整；OCR 后 ArrayTable 出现该表 |
| 2 | **列编辑**：既有明细表列头悬浮 ✎ 改名 / ✕ 删列 / 表尾 + 列 | 改名后**所有样本**该列标注即时改名（切换文档核对）；删列后列消失；触发定制后 items schema 与 prompt 列集一致 |
| 3 | **行编辑**：表底「补一行」逐列填值 / 行尾 ✕ 删行 | 补行入 GT（字段列表可见 manual 标注）；删行后行号连续无空洞；触发定制后 evaluator 对漏行样本产「漏提取」diff（看轮次 per_sample_results） |

- **判据**：三条路径全通 + 无 console 错误 + 定制 job 正常完成。
- **失败处理**：单路径失败开 bug 修复（前端交互问题当场修；链路问题
  对照 `test_array_field_add / test_array_column_edit / test_array_row_and_reflection`
  的单测断言定位断点）。

**L0 DoD**：plan-line-items.md 最后两个 checkbox 勾掉；发现的 bug 修完并有回归测试。

---

## L1 · ADR-002 typed-edit 优化器灰度（P-E，1-2d 含观察）

**背景**：功能已完成（`SKILL_TYPED_EDITS` 默认 OFF）——优化器不再整段重写
`ocr_prompt`，改产**类型化有界 edit**（append/replace/delete）演化每字段
「规则段」`rule_edits_text`，正文冻结；被拒 edit 入 buffer 下轮过滤。
集成测试通过（零 token），"真实灰度待 greenlight"（ADR-002 §P-E）。

### L1.0 灰度前置核查（已在本会话核过的交互，执行时复认一遍）

| 交互点 | 状态 |
|---|---|
| typed 分支走 `verify_module_fix` → **判官现已 fail-closed**（批次3） | ✅ 兼容，但注意：LLM 抖动时 typed edits 会被 reject 入 buffer——灰度期 reject 率天然比 ADR 写作时预期**偏高**，判读时区分「判官拒绝」vs「判官不可用拒绝」（看 reasoning 是否含 `verifier unavailable`） |
| 保留性守护（批次6 `customer_feedback_preserved`）只挂非 typed 分支 | ✅ typed 正文冻结天然满足，无冲突 |
| 评测有效性门（批次2）：无效轮不进优化 | ✅ typed 同样被门住 |
| `_merge_round_suggestions` 不 clobber `_field_rule`（批次5） | ✅ typed 的 `rule_edits_text` 走独立列，互不干扰 |
| flag OFF 行为逐字节不变 | ✅ 有既有集成测试护航 |

### L1.1 灰度执行（单 API）

1. 选灰度对象：**非生产关键**的 MY 测试 ApiDef（建议新 fork 一个，
   避免污染 my-invoice-abf4f0 的版本链）；
2. 进程级开 flag（不改 .env 默认）：
   ```bash
   cd backend && SKILL_TYPED_EDITS=true uvicorn app.main:app --port 8000
   ```
3. 在工作区做 2-3 个字段编辑 → 触发定制 → 完整跑 3 轮；
4. 期间不开 `SKILL_META_MEMORY`（**隔离变量**：先验证 typed 主链，
   meta 记忆注入放 L1.3 第二阶段）。

### L1.2 观察指标与判据

| 指标 | 看哪里 | 通过判据 |
|---|---|---|
| 规则段演化 | 目标字段 `OcrModule.rule_edits_text` + composed_prompt 里「# 规则补充（迭代沉淀）」块 | 有界增长（每轮 ≤ clip 上限），无正文改写 |
| accept/reject op | `run.metrics.edit_ops.accepted/rejected` + `rejected_edits` buffer | reject 有记录且下轮不重提；`verifier unavailable` 型 reject 占比 < 1/3（更高说明 LLM 环境不稳，先修环境再判功能） |
| 准确率单调 | 各轮 `overall_accuracy` + 最终激活版本 | 单调守护不触发回退（或触发但保住旧版=守护正常）；最终 acc ≥ 起始 |
| 正文冻结 | diff 新旧版本模块 `ocr_prompt` | 逐字节相同（typed 模式正文只能由 reconciler/fork 改） |
| token 成本 | `run.metrics.total_llm_calls` vs 非 typed 同规模 run | 增幅 ≤ ~1 次/字段/轮（typed 多一次 verify 组合体） |

### L1.3 决策点与推进

- **通过** → 第二个 API 灰度 + 叠加 `SKILL_META_MEMORY=true` 再跑一轮
  （验证 accept/reject 记忆注入下轮 optimizer prompt）；两个 API 无回归
  → 提议置 `SKILL_TYPED_EDITS: bool = True` 默认开（单独提交，含 as-built 更新）。
- **不通过** → 关 flag 即回退（ADR-002 §7：正文未被动过，规则段留在
  DB 不渲染即失效）；问题记录进 ADR-002 开放问题节。

**L1 DoD**：as-built.md §9 的 P-E 从「待监督执行」改为结论（通过/不通过+证据）；
默认开关的决策显式记录（开/不开都行，但要写下来）。

---

## L2 · 前端 B3 大文件拆分（~1d，B2 打法复制）

**取证**（2026-07）：两文件内部已是多组件结构，无隐式共享状态，
与 B2 拆 DarkFieldViewer 前的形态一致：

```
OptimizationProcessPanel.tsx (1574 行, 10 组件)
  :210 FieldAccuracyHeatmap   :355 FieldDiffComparison
  :553 主组件(≈380 行)         :931 VersionChips      :979 RunStatusBar
  :1091 ModuleList             :1172 ModuleDetail     :1459 Collapsible
  :1483 EmptyPhase             :1491 FinalizeModal

OcrOptimizer.tsx (1058 行, 9 组件)
  :141 JsonBlock  :149 Section  :169 Field   :180 主组件(≈480 行)
  :657 ActiveVersionPanel  :694 VersionDetailPanel  :775 ModuleDetailPanel
  :812 RunDetailPanel      :922 RoundDetailPanel
```

### L2.1 OptimizationProcessPanel → `workspace-v2/optimization-panel/`

分组拆（B2 结论：分组比单组件文件的导入边界风险低得多）：

| 文件 | 内容 |
|---|---|
| `charts.tsx` | FieldAccuracyHeatmap + FieldDiffComparison（纯展示，数据经 props） |
| `run-views.tsx` | VersionChips + RunStatusBar + ModuleList + ModuleDetail |
| `shared.tsx` | Collapsible + EmptyPhase + FinalizeModal + 文件头类型/常量 |
| `OptimizationProcessPanel.tsx` | 薄入口：主组件 + default export，import 方零改动 |

### L2.2 OcrOptimizer → `pages/settings/ocr-optimizer/`

| 文件 | 内容 |
|---|---|
| `primitives.tsx` | JsonBlock + Section + Field |
| `detail-panels.tsx` | ActiveVersionPanel + VersionDetailPanel + ModuleDetailPanel + RunDetailPanel + RoundDetailPanel |
| `OcrOptimizer.tsx` | 薄入口（主组件 ≈480 行——若内部有清晰分节可再切，执行时判断） |

**执行纪律**（同 B2）：每拆一组 `npx tsc -b`，全部完成后
`npm run build` + eslint 新文件 + preview 打开优化面板/设置页各点一遍；
一组一 commit 可单独 revert。**纯搬移不改 JSX 结构、不顺手改逻辑。**

**L2 DoD**：两个入口文件各 ≤ 主组件本体大小（≈400/500 行）；
build/lint 绿；repository-structure.md §七 补一行记录。

---

## 风险与回退汇总

| 风险 | 级 | 缓解/回退 |
|---|---|---|
| Gemini/qwen 网络不稳（本机历史上 SSL 间歇中断） | L0/L1 | `eval_valid`/`ocr_error_docs` 区分传输失败与真回归（批次2/6 已内建）；qwen key 可作备胎 |
| 灰度期判官 fail-closed 推高 reject 率 | L1 | 判读时按 reasoning 分型；`verifier unavailable` 占比高 → 环境问题非功能问题 |
| 灰度污染生产版本链 | L1 | 用测试 ApiDef；typed 只写 `rule_edits_text` 列，关 flag 即不渲染，无需数据回滚 |
| L2 无测试兜底 | L2 | B2 已验证的打法：tsc 严格 + 分组拆 + 一组一 commit + preview 点检 |
| L1 观察期空转 | — | L2 与 L1 零耦合，观察期穿插执行 |

## 总排期

```
Day 1 上午  L0.1 黄金回归 + L0.2 preview 冒烟（发现 bug 则当场修）
Day 1 下午  L1.0 前置复认 + L1.1 灰度启动（跑 3 轮）
Day 2       L1.2 判读 ←→ 穿插 L2.1 OptimizationProcessPanel 拆分
Day 3 上午  L1.3 决策（二号 API + META_MEMORY 或回退）
Day 3 下午  L2.2 OcrOptimizer 拆分 + 文档收官
```

约 **2.5-3 天**。L0 发现真回归则 L1 顺延（同一环境、先修地基）。
