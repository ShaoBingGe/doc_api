# ADR-001: 引入 SkillOpt（ReflACT）「技能即可训练文档」纪律，重构 OCR 反思与技能优化

**Status:** Accepted —— **P0/P0'/P1/P2 已实现并上线**（2026-06-26，invoicase.cn）；P3/P4/P5 延后。
**Date:** 2026-06-26
**Deciders:** 项目负责人
**参考:** Microsoft SkillOpt（`skillopt/SkillOpt`，arXiv:2605.23904）— *Executive Strategy for Self-Evolving Agent Skills*

> 📌 **本文为决策口径（为何这么做）。线上「实际跑的是什么」以
> [skill-optimization-as-built.md](./skill-optimization-as-built.md) 为单一事实源**
> （含 4 个特性开关、噪声门 3+9=12、留出门、字段治理三层、技能库、代码地图、验证证据、部署 runbook）。

---

## Context（现状与受力分析）

### 本项目当前的「反思 + 技能」设计

OCR 优化器目前在 `OcrModule.ocr_prompt`（逐字段提示词）上做逐轮演化，链路是：

```
round: 评估每个 module(对 GT 算 field_accuracy)
     → reflect_on_diffs(逐 diff 反思)
     → optimize_module(重写该字段 prompt) → verify_module_fix(变差则拒)
     → meta(add/remove/rename) → compose 下一版本 → 评估 → 单调 finalize
```

存在**两套都没长开的「skill」概念**：

1. **反思路由 YAML**（`reflection/skills/*.yaml`：value_mismatch / normalize / retarget / case_normalize / empty_value / format_mismatch / new_field）。`match_spec` + `render` 把一条 diff 按 `edit_intent` 路由到一段反思提示词。**静态、人工编写、不演化、不被验证、不训练**——它只决定「怎么反思」，不是可优化的产物。

2. **`OcrSkill` 数据模型**（`ocr_skills` 表）：已有 `api_definition_id`（NULL=全局共享库 / 非空=该 API 私有）、`name` / `description` / `content` / `status`。但代码注释直言 **「no service currently reads or writes it，所有端点返回 501」**——**一等公民的「技能库」schema 已经在，但完全休眠**。`OcrModule.skill_ids` 也只是硬拷贝占位。

### 用户的判断（准确）

> 「本项目的 OCR 内容反思步骤和 skill 的设计，不太明显。」

确实：真正在被优化的是「逐字段 prompt」，而「skill」既没有作为可训练产物被对待，也没有跨 API/国家沉淀成库。

### 当前优化循环缺失的纪律（对照 SkillOpt）

| 维度 | 本项目现状 | 后果 |
|---|---|---|
| **验证集** | `verify_module_fix` / 单调门都在**同一批样本**上判好坏 | **过拟合**这几张样本，泛化无保证（之前已分析过：泛化范围无 metric 保证） |
| **编辑粒度** | 每轮可**整段重写** module prompt | 「学习率」过大、不稳定、来回震荡 |
| **反思单位** | 逐 diff / 逐样本独立反思 | 噪声大，单个一次性 OCR 失误就改 prompt |
| **聚合** | 无跨样本聚合/支持度计数 | 无法区分「3 张都错」与「1 张偶发」 |
| **被拒编辑记忆** | verify 拒了就丢 | 下一轮可能**重复提同一个被拒方案** |
| **缺陷 vs 执行失误** | 任何 diff 都改 prompt | prompt 持续膨胀（靠 composer `_fold` 事后救） |
| **纵向/慢更新** | 无 | 无跨轮/跨 epoch 的稳定守护段 |
| **技能沉淀** | `OcrSkill` 休眠 | 每个 API 孤立迭代，零复用，零跨国学习 |

### SkillOpt 提供的纪律（我们要借的「术」）

SkillOpt 把**技能文档当作冻结模型的可训练状态**，用神经网络训练的纪律去训它（epoch / batch / 学习率 / 验证门），推理期**零额外模型调用**。核心机制：

- **ReflACT 6 阶段循环**：Rollout → Reflect → Aggregate → Select → Update → **Evaluate(Gate)**。
- **类型化、有界的 Edit**（`append/insert_after/replace/delete`）带 `support_count`（多少个失败/成功支持它）、`source_type`（failure/success）、`merge_level`。
- **Minibatch 反思**：把 M 条轨迹**成组**分析（类比 minibatch SGD vs 逐样本），降噪、防过拟合单例。
- **Aggregate**：跨 minibatch 层级合并 + 支持度计数 + 去重。
- **Clip（梯度裁剪）+ 自主学习率**：对候选 edit 排序取 top-L、由优化器决定本轮更新多少条 → 控制有效步长。
- **Validation Gate（关键）**：候选技能**只有在留出验证集分数严格变好时才接受**（hard / soft / mixed 三种 metric）。
- **Slow update（epoch 级）**：比较相邻 epoch 同样本表现，把守护性指引写进技能文档的**受保护段**（step 级编辑不可改）。
- **Meta-skill memory**：不改技能文档，但给优化器自身积累「怎么提/合/排 edit」的元记忆。
- **Skill-aware 反思（SKILL_DEFECT vs EXECUTION_LAPSE）**：判别「规则错/缺（→改正文）」还是「规则对、执行没遵守（→只加附录提醒，不动正文）」；**不确定时默认 LAPSE**，保护正文不被一次性失误删规则。
- **Rejected-edit buffer**：记住被拒的 edit，不再重复提。
- **Sleep（离线自进化）**：夜间回放复发任务，把通过留出门的有效技能**固化进库**。

### 约束

- 生产系统（invoicase.cn / qwen3-vl-plus，**数据驻留：仅境内合规模型**，禁 Gemini/OpenAI）。
- 已有可复用基建:**scored rollouts**（`OcrModuleIteration.per_sample_results.field_accuracy`，hard+soft 雏形）、**golden_set 评测**（MY/JP 零容忍严格评分）、刚落地的**字段治理三层**（国家锁定 / 客户 override）+ 单调 finalize。
- LLM 成本/时延敏感（并发已调过；逐轮再加阶段会放大调用数）。
- 样本极少（常 3–5 张）→ 留出验证集会很噪。
- **不得回归**现有线上行为。

### 领域映射（为何能优雅嫁接）

| SkillOpt | 本项目已存在的对应物 |
|---|---|
| Rollout（执行一次得轨迹） | OCR 一张样本 → structured_data |
| Score（hard/soft） | `field_accuracy`（精确匹配=hard；部分=soft），**已在算** |
| Skill document（可训练正文） | `OcrSkill.content`（休眠）/ `OcrModule.ocr_prompt`（在用） |
| Skill library（全局/私有） | `OcrSkill.api_definition_id`（NULL=全局 / 非空=私有），**schema 已就绪** |
| Reflect/Aggregate/Select/Update | reflector + module_optimizer + meta_optimizer（缺纪律） |
| Validation Gate | golden_set 评测 + 留出样本（缺接线） |
| Sleep 离线固化 | golden_set + 跨 API 的已确认 GT（缺离线作业） |

**结论:基建九成都在，缺的是「把技能当可训练产物 + ReflACT 纪律」的接线。**

---

## Decision（决策）

采用 **Option B（首选）**：在现有基建上，分阶段引入 ReflACT 纪律并**激活 `OcrSkill` 为一等可训练技能产物**：

1. 把逐轮优化重构为 **Reflect(minibatch + 缺陷/失误判别) → Aggregate(支持度) → Clip/LR(有界 edit) → Update(类型化 edit + 受保护慢更新段) → 留出 Gate**；
2. 引入 **rejected-edit buffer** 与 **slow/meta epoch 更新**；
3. **激活技能库**：技能 = 按 `(国家, 字段)` 维度的可版本化、可验证技能文档，分**全局库 + 私有覆盖**；接活休眠的 skill 端点与 `skill_ids`；
4. **离线 sleep 固化**：把跨 API 已确认 GT 蒸馏出的有效技能，经 **golden_set 留出门**晋级进全局库（与既有「字段晋升进国家模板」的设想同源）。

执行顺序为 **A 先行**（先拿纪律，最高杠杆的 Gate 优先），再做库与离线，详见 Action Items。

---

## Options Considered

### Option A — 最小嫁接（只移植纪律）
在**现有逐字段循环**上加：留出 Gate + 编辑预算/Clip + 缺陷/失误判别 + rejected-edit buffer。产物仍是 `OcrModule.ocr_prompt`，**不**激活技能库。

| 维度 | 评估 |
|---|---|
| 复杂度 | 低 |
| 成本 | 低（几处 seam，复用现有 round 循环） |
| 杠杆 | 高（Gate 直接消灭过拟合，是单点最大收益） |
| 团队熟悉度 | 高（不引入新模型） |

**Pros:** 快、风险小、80% 的质量提升一次到位；不破坏现有 UX。
**Cons:** 「skill」仍隐式（=逐字段 prompt），库不沉淀，跨国学习缺位——没真正回应「skill 不明显」。

### Option B — 一等技能库 + ReflACT（推荐）
A 的全部 + 激活 `OcrSkill` 为 `(国家,字段)` 可训练技能（全局库 + 私有覆盖）+ 接活 skill 端点 + 离线 sleep 经 golden_set 固化进全局库。

| 维度 | 评估 |
|---|---|
| 复杂度 | 中–高（分期可控） |
| 成本 | 中（新增 LLM 阶段→并发+缓存摊薄；新增状态表） |
| 杠杆 | 高且**持久**（技能成为跨 API/国家的复用资产） |
| 团队熟悉度 | 中（需建技能版本/缓冲/元记忆） |

**Pros:** 「技能」变显性且**累积**；与「字段晋升进国家模板」「golden_set」「country-lock」形成闭环；prompt 膨胀被技能复用替代。
**Cons:** 工程量大；少样本下留出门噪声需对策；需要技能 UX。

### Option C — 直接移植/包裹 SkillOpt
`pip install skillopt`，写一个 OCR `EnvAdapter` 让其训练循环驱动。

| 维度 | 评估 |
|---|---|
| 复杂度 | 高 |
| 成本 | 高 |
| 杠杆 | 中（拿到论文级保真，但水土不服） |
| 团队熟悉度 | 低 |

**Pros:** 最高保真复现论文。
**Cons:** 其循环面向 agent/benchmark（异步多 backend、自带 state/ckpt），与**逐字段 module 模型 + 实时 workspace 迭代 UX** 阻抗失配；**多 backend 触碰数据驻留红线**；重集成、难维护。**纪律本身简单清晰，移植「术」远胜包裹「框架」。**

---

## Trade-off Analysis（权衡）

- **Gate 是单点最高杠杆**：当前最大弱点是「在训练样本上自评好坏→过拟合」。留出验证门（少样本用 soft 指标 + 留一交叉验证，全局技能用 golden_set 作真留出）一招制敌。**无论选 A 还是 B，Gate 都先做。**
- **A vs B**：A 用最小代价拿走大部分质量提升；B 把技能层做成**持久资产**（跨 API/国家学习），正面回应用户诉求，并与既有 golden_set / 国家模板晋升设想合流。→ **选 B，但按 A→库→离线 分期**，每期独立可上线、可回退。
- **C 不取**：纪律可在本项目语境内用几百行实现，且不踩数据驻留与 UX。SkillOpt 作为**参考实现 + 设计圣经**，不作为运行时依赖。
- **少样本风险**：3–5 张时 hard 留出门近乎二值噪声 → 用 **soft（字段级部分分）+ 留一法**；同时**全局技能的真验证放在 golden_set**（几十张、零容忍），把「过拟合单客户样本」的风险隔离在私有层。

---

## Consequences

**变容易：**
- 迭代稳定、不再过拟合；技能可跨 API/国家复用；prompt 膨胀被技能引用替代；休眠的 `OcrSkill` / `skill_ids` / golden_set 终于产生价值；与 country-lock / 字段晋升闭环。

**变难 / 新增负担：**
- 需要 train/val 切分逻辑（少样本对策）；逐轮新增 Aggregate/Clip/Gate → **LLM 调用数与时延上升**（用并发 + 反思结果缓存 + 「无未达标字段就跳过」早退 摊薄）；新增状态（技能版本、rejected buffer、meta memory）；需要技能 UX 才能让「skill 显性」。

**需复盘：**
- 少样本下 Gate 指标的稳健性（soft vs hard vs mixed 权重）；技能粒度（`(国家,字段)` vs 跨字段共性技能）；全局库晋级阈值（沿用之前讨论的「跨租户覆盖 + 真人确认 + 频率」三与门）。

---

## Action Items（分阶段开发计划）

> 原则：每期**独立可上线、可回退**，先纪律后资产，先私有后全局，先在线后离线。
>
> **实现进度（2026-06-26）：✅ P0' / P0 / P1 / P2 已落地并上线（4 个 `SKILL_*` flag 线上全开）；
> ⏳ P3 / P4 / P5 延后。** 逐项落地见 [as-built 参考](./skill-optimization-as-built.md)。

### P0 — 地基与对齐（~0.5 周）　✅ 已实现
- [ ] 抽象 `RolloutScore{hard, soft, fail_reason}`：把 `OcrModuleIteration.per_sample_results` 规整成统一打分（hard=精确匹配率、soft=字段级部分分），作为后续所有门的输入。
- [ ] 抽象类型化 `FieldEdit{op∈append/replace/delete, target, content, support_count, source_type}`，落到 `ocr_optimizer/skilltrain/types.py`（不动现有 models）。
- [ ] 写 1 页《术语映射表》进 `docs/`（SkillOpt↔本项目），团队对齐。

### P1 — 纪律嫁接到现有循环（最高杠杆，~1–1.5 周）　✅ 已实现（`SKILL_HELDOUT_GATE`/`SKILL_NOISE_GATE`/`SKILL_EDIT_DISCIPLINE`）
- [ ] **留出 Gate**：`evaluation/gate.py`——把已确认样本按 round 切 train/val（少样本走留一法）；候选版本**只有 val soft 分严格 > 当前才接受**，否则回退并把该 edit 入 rejected buffer。接到 `run_orchestrator` compose 之后、finalize 之前。
- [ ] **编辑预算 / Clip / 自主 LR**：`optimize_module` 产出**类型化有界 edit** 而非整段重写；按支持度排序取 top-L（L 由「未达标严重度」自适应）。
- [ ] **Minibatch 反思**：`reflect_on_diffs` 改为**按字段把多样本 diff 成组**反思（降噪），产出带 `support_count` 的 edit。
- [ ] **缺陷/失误判别**：反思输出标 `SKILL_DEFECT`（改 prompt 正文）/`EXECUTION_LAPSE`（只进「# 客户反馈补充」附录、不动正文，复用现有 `_fold`）；不确定默认 LAPSE。
- [ ] **Rejected-edit buffer**：新表或 `OcrOptimizationRun.metrics` 内挂；reflect/clip 提案前过滤已拒。
- [ ] 回归：MY/JP golden_set 跑分**不降**；country-lock 字段仍被排除/钉死。

### P2 — 激活一等技能库（~1.5–2 周）　✅ 已实现（`SKILL_LIBRARY_RENDER`；全局库待 P4 填充）
- [ ] 用 `OcrSkill`：技能 = `(country, field)` 维度的 `content`（正文规则）+ 受保护慢更新段；`api_definition_id` NULL=全局库 / 非空=私有覆盖。
- [ ] composer 渲染：module body = 「全局技能 ⊕ 私有覆盖 ⊕ 本轮 edit」，技能引用而非内联复制（治 prompt 膨胀）。
- [ ] 接活 501 端点：列出/绑定/编辑技能 + `OcrModule.skill_ids` 真正生效。
- [ ] P1 的 edit 改为作用在「私有技能」上；私有技能成为可训练产物。

### P3 — Slow / Meta epoch 更新（~1 周）　⏳ 延后
- [ ] `slow_update`：迭代收尾比较相邻版本同样本表现，写守护段（step 级不可改）。
- [ ] `meta_skill`：优化器侧元记忆（怎么提/合/排 edit），跨轮复用、不入技能正文。

### P4 — 离线 Sleep 固化进全局库（~1.5 周）　⏳ 延后（需 golden_set 门 + 管理员确认策略）
- [ ] 夜间作业：扫同国家各 API 的**已确认 GT**，蒸馏候选技能 edit（带跨租户支持度）。
- [ ] **golden_set 留出门**：候选只有在该国 golden_set 上严格不降才晋级进全局 `OcrSkill`（NULL）。
- [ ] 与「字段晋升进国家模板」的三与门（跨租户覆盖 + 真人确认 + 频率）共用一套晋级策略；晋级走版本化 + 管理员确认（不全自动改库）。

### P5 — 让「技能」显性（UX，~1 周）　🔶 部分（技能库面板已上线；val 分曲线/被拒 edit/晋级视图待做）
- [ ] workspace「优化过程」面板增「技能」标签：展示每字段挂的技能、本轮 edit（含被拒+原因）、val 分曲线、晋级状态。
- [ ] 平台管理员「全局技能库」视图（候选/已晋级/版本 diff）。

---

## 开放问题（需你拍板）

1. **投入档位**：先只做 **P1（纪律嫁接，最高杠杆、~1–1.5 周、独立上线）**，还是直接排到 **P2 技能库**？（我建议 P1 先上、验证收益，再决定 P2/P4。）
2. **技能粒度**：技能按 `(国家,字段)` 起步，还是也要「跨字段共性技能」（如「所有金额类去千分位」）作为一类全局技能？
3. **少样本 Gate 指标**：默认 **soft + 留一法**（我推荐），可接受吗？还是私有层就不设硬门、只靠 golden_set 在全局层把关？
