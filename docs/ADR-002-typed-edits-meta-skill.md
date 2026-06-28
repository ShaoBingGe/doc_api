# ADR-002: 优化器改产「类型化有界编辑」(typed FieldEdit) — 接通 meta_skill 与完整 ReflACT 纪律

**Status:** **Accepted —— P-A/P-B/P-D 已实现（flag `SKILL_TYPED_EDITS`/`SKILL_META_MEMORY` 默认 OFF）**；P-C 大部被冻结正文架构吸收；P-E 真实灰度待监督执行。
**Date:** 2026-06-28
**Deciders:** 项目负责人
**前置:** [ADR-001](./ADR-001-skill-optimization-reflact.md)（P1 纪律 / P3 meta_skill）、[as-built](./skill-optimization-as-built.md)

> 📌 **实现进度（2026-06-28）**：决策按我的判断落地——规则段载体用新增 `OcrModule.rule_edits_text` 列、
> accept/reject 字段集合粒度。已分阶段实现并 flag-gated（默认 OFF，OFF 路径字节不变）：
> - **P-A** 契约：`ModuleOptimizerOutput.edits` + flag-on typed 指令。✅
> - **P-B.1** 载体：`rule_edits_text` 列（prod 已 ALTER）+ composer 渲染规则段。✅
> - **P-B.2** 应用链：轮循环 `build_rule_update`（filter→aggregate→clip→apply）→ 规则段（正文冻结）+ 被拒缓冲跨轮持久。✅
> - **P-D** meta：accepted/rejected op → `run.metrics.meta_memory`；`render_meta_hint` 前置进 optimize prompt（`SKILL_META_MEMORY` 门控）。✅
> - **P-C** 缺陷/失误：被拒缓冲已做；`kind` 已捕获并入 meta；**显式 lapse→附录路由延后**（典型场景已被「typed 模式正文冻结、只演化规则段」吸收）。
> - **P-E** 真实灰度：**整轮集成测试已通过（零 token：mock OCR/optimizer/verify，证明 edits→规则段+meta 端到端）**；真实 OCR 灰度待你 greenlight（开全局 flag + 烧 token）。
> - 验证：typed 相关 ~24 单测/集成（含整轮）+ 全量 258 passed。两 flag 默认 OFF、prod 已部署。

---

## 1. Context（为什么要做）

ADR-001 P1 落地时，`skilltrain/` 的纪律机制**大部分建好了但没接进 live 优化器**：

| 机制（已建+已测） | 现状 |
|---|---|
| `types.FieldEdit{op,target,content,support_count,source_type,kind}` | 定义好，live 优化器**不产出** |
| `aggregate.aggregate_edits`（按签名合并 + 支持度） | 未接 |
| `clip.rank_and_select` / `decide_L`（top-L 裁剪） | 未接 |
| `apply.apply_edits`（有界应用，`## [field:X]` 分节） | 未接 |
| `classify`（SKILL_DEFECT vs EXECUTION_LAPSE） | 未接 |
| `buffer.RejectedEditBuffer`（被拒不再提） | 未接 |
| `meta_skill.summarize_edit_outcomes` / `render_meta_hint` | 未接（**本 ADR 目标**） |

**live 优化器现状**（`module_optimizer.optimize_module` + `run_orchestrator` 轮循环 L894–962）：
- LLM 返回 `ModuleOptimizerOutput{aggregate_diff, optimization_suggestion, new_ocr_suggestions, new_description, **new_ocr_prompt**}` —— **整段重写**该字段 prompt，不是有界编辑。
- `verify_module_fix` 在该字段上**整体**判好坏；变差→`out["rejected"]`→保留旧 prompt。
- P1 的 `disciplined_targets` 目前**只收窄要优化哪些字段**，没改"怎么改"。

**后果**：accept/reject 是「整段重写」粒度、无 op 类型，与 `meta_skill` 的 op 模型（按 append/replace/delete 统计被拒率）**阻抗不匹配** → meta 无法接线；`aggregate/clip/buffer/classify` 也都悬空。

**目标**：让优化器产出**类型化有界 edit**，从而 (a) 接通 meta_skill，(b) 激活整条 P1 纪律链（有界步长、支持度、缺陷/失误、被拒缓冲），(c) 守住 prompt 不再膨胀。

---

## 2. 关键设计阻抗（必须先解决）

### 2.1 自由文本 prompt vs 分节文档（最大阻抗）
`apply.apply_edits` 假设文档是 `## [field:X]\n- 规则…` 分节结构；但 **live `OcrModule.ocr_prompt` 是自由文本**（国家模板初始化 + 历轮整段重写积累而来）。

**决策（推荐 B）**：
- **A. 全量结构化**：把所有 module 的 ocr_prompt 迁移成分节结构。代价大、影响所有国家模板、回归面广。✗
- **B. 渐进双轨（推荐）**：保留 ocr_prompt 自由正文不动；**新增一个分节的「规则附加段」**（`OcrModule.rule_edits_text` 或复用 composer 的附录块），typed edit 只作用在这个分节段上、由 composer 拼到字段正文后。优化器对自由正文**只读引用**、不再整段重写它。→ 有界、可回退、不动存量正文。
- **C. 自由文本 diff**：让 edit 直接 patch 自由文本（按行/锚点）。脆弱、不确定。✗

> B 的副作用：字段最终 prompt = 「国家模板正文（冻结）⊕ 分节规则段（typed edit 演化）⊕ 技能库块（P2）⊕ 守护段（P3）」。正文不再被 LLM 重写 → 天然防膨胀 + 防"反思把客户/国别规则改飞"。

### 2.2 编辑粒度
- 起步 **per-module**：每轮每个未达标字段产 ≤K 条 edit（op 作用在该字段的规则段）。target=字段名。
- 不做跨字段 edit（留后续）。

### 2.3 accept/reject 粒度
- 候选 = 该字段本轮的 edit 集合 → 应用到规则段 → `verify_module_fix`（复用）在该字段上判好坏。
- **accept**：该字段整体变好 → edits 标记 accepted（各 op 计入 meta「accepted」）。
- **reject**：变差 → 回退 + edits 入 `RejectedEditBuffer`（各 op 计入「rejected」）+ 下轮 `buffer.filter` 过滤。
- （进阶：逐 edit 留一验证可后置；起步用「字段集合」粒度即可喂 meta。）

---

## 3. 目标架构

```
round step（每个未达标字段）:
  reflect(minibatch diffs) ──► 候选 typed edits（带 source_type/kind）
        │  classify: SKILL_DEFECT(改规则段) / EXECUTION_LAPSE(只进附录提醒)
        ▼
  buffer.filter(过滤已拒) ─► aggregate_edits(合并+支持度) ─► clip.rank_and_select(top-L)
        ▼
  apply_edits(规则段) ─► verify_module_fix
        ├─ accept ► 落库 + meta「accepted[op]++」
        └─ reject ► 回退 + buffer.add + meta「rejected[op]++」
  ──────────────────────────────────────────────
finalize / 跨轮:
  summarize_edit_outcomes(accepted, rejected) ─► run.metrics["meta_memory"]
  render_meta_hint ─► 注入下一轮 optimize_module 的反思提示（flag SKILL_META_MEMORY）
  buffer.to_list ─► run.metrics["rejected_edits"]（跨轮持久 + P5 可视）
```

---

## 4. 分阶段实施（每阶段独立可上线、flag-gated、bench 验证）

### P-A：LLM 契约改造（产 typed edits）—— ~3 天
- `ModuleOptimizerOutput` 增 `edits: list[{op,target,content,source_type,kind}]`（保留 `new_ocr_prompt` 作 fallback，flag 切换）。
- 改 `optimize_module` 的 system prompt：要求输出**有界 edit 列表**（append 优先、replace 谨慎、delete 罕见），并解释规则段语义。
- 新 flag `SKILL_TYPED_EDITS`（默认 OFF → 走旧整段重写，零回归）。
- 单测：契约解析、extra='forbid' 仍挡 skill 字段。

### P-B：规则段 + 应用链 —— ~3 天
- 决策 2.1-B：新增规则段载体（`OcrModule.rule_edits_text` 或 composer 附录），composer 拼装时渲染（`assemble_prompt` 已是拼装中心）。
- 轮循环接 `buffer.filter → aggregate_edits → clip.rank_and_select → apply_edits`，产出新规则段（不动自由正文）。
- `verify_module_fix` 改在「正文⊕新规则段」上判；reject 回退规则段。
- 断言 `diff_line_count` 受限（不再整段重写）。

### P-C：缺陷/失误 + 被拒缓冲 —— ~2 天
- `classify` 给每条 edit 标 SKILL_DEFECT / EXECUTION_LAPSE（不确定默认 LAPSE）；LAPSE 只进附录提醒、不进规则段正文。
- `RejectedEditBuffer` 持久到 `run.metrics["rejected_edits"]`；轮间 `from_list`/`filter`。

### P-D：meta_skill 接线（本 ADR 主目标）—— ~2 天
- 收集本 run 的 accepted/rejected typed edits → `summarize_edit_outcomes` → `run.metrics["meta_memory"]`。
- `render_meta_hint` 注入下一轮 `optimize_module` 反思提示（flag `SKILL_META_MEMORY`，已存在）。
- P5：`skill-insights` 端点 + 洞察面板加「本轮 edit（含被拒+原因）+ meta 提示」展示（补 ADR-001 P5 的"被拒 edit 展示"待办）。

### P-E：bench 验证 + 灰度 —— ~2 天
- `bench_japan_inv` 跑 typed-edit ON vs OFF：**test 精度不降、train-test 裂隙不升、prompt 体积不增、调用数不超预算**（§ADR-001 §2.3）。
- 真实单 API 灰度 1–2 个，确认无回归后再考虑默认开。

---

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 动**生产优化器核心**，回归面大 | 全程 flag-gated（`SKILL_TYPED_EDITS` 默认 OFF）；旧整段重写路径保留作 fallback；bench 前后对比硬门 |
| LLM 不稳定产坏 edit（op/target 乱填） | 严格 schema + 校验（target 必须是已知字段、content 非空）；坏 edit 丢弃不应用 |
| 规则段与自由正文语义冲突 | 决策 2.1-B 让正文冻结、只在规则段演化；composer 渲染顺序明确 |
| accept/reject 粒度粗（字段集合非逐 edit） | 起步够喂 meta；逐 edit 留一验证作后续增强 |
| prompt 反而更长（正文+规则段+技能+守护） | `diff_line_count` 断言 + clip top-L 限量 + 附录折叠（复用 `_fold`） |

## 6. 测试计划
- 纯函数：edit 契约解析、apply 在规则段上的 append/replace/delete 幂等、clip/aggregate/buffer/classify（已有）。
- 集成：flag-on 一轮，断言 edits 落规则段、reject 入 buffer、meta_memory 写入 run.metrics。
- bench：Japan-inv ON/OFF 三数对比（test↑/裂隙↓/调用数不超）。
- 回归：MY/JP golden_set 不降；country-lock / 客户 override 仍生效。

## 7. 回退
任一阶段：`SKILL_TYPED_EDITS=false` → 立即回旧整段重写路径（字节级等价）。规则段载体为加性字段，关闭即不渲染。

## 8. 工期与依赖
P-A→P-E 约 **2–2.5 周**，关键路径 P-A→P-B（契约+应用链）。P-D（meta）依赖 P-A/B/C。建议先 P-A+P-B 上线验证有界编辑收益，再续 P-C/D/E。

## 9. 开放问题（需拍板）
1. 规则段载体：**新增 `OcrModule.rule_edits_text` 列**（需轻量迁移）vs **复用 composer 现有附录块**（无迁移、但需区分"客户反馈附录"与"typed-edit 规则段"）？建议前者（语义清晰）。
2. accept/reject 起步用「字段集合」粒度即可，是否够？（我判断够喂 meta；逐 edit 留一后置。）
3. 默认开启时机：bench 通过 + 几个真实 API 灰度无回归后，由你决定置 `SKILL_TYPED_EDITS=true`。
