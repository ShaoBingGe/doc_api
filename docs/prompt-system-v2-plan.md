# Prompt System v2 — 结构化重构与 Skill 体系迭代计划

> 状态：规划中（尚未落地代码）。本文件是「将 skill-creator 的设计原则融入本项目 prompt
> 体系」的下一阶段路线图。每个阶段以 Phase 0 的离线评测为闸门，准确率回退即不合并。
>
> 配套约束：本计划严格遵守 `CLAUDE.md` 五大原则，尤其是 §① 平台契约不可改、
> §③.4 composer 不调 LLM、§⑤.3 单 diff 多视角累积。任何与之冲突处在阶段说明中显式标注。

---

## 0. 背景：为什么要做这次重构

客户「编辑字段 → customize → 3 轮迭代」会反复改写每个字段的 `ocr_prompt`。当前的 prompt
与 skill 体系是**自由文本驱动**的：反思产出自由文本 `fix_suggestion`，被当作 prompt 后缀
累加；optimizer 再读自由文本。由此带来四类问题，直接对应用户提出的 5 条要求：

1. composed_prompt 层次不分明（只有 Part 3 有显式标题，模块体是自由 blob）。
2. 泛化能力弱：跨样本对照只喂国家 agent，且 prompt 仍鼓励「按位置/坐标」推理。
3. 各环节之间靠自由文本传递，下一环要重新解析意图。
4. 文本重复、术语不统一、不够精简。
5. 多轮累加会让同一字段的 prompt 堆叠**互相矛盾**的指令，无检测、无按最新意图收敛。

---

## 1. skill-creator 原则提炼（设计依据）

| 原则 | 含义 | 映射 |
|---|---|---|
| 渐进式披露 | 元数据→正文(<500行)→按需资源；逼近上限加层级 + 指针 | prompt 分层；skill 公共基底 + 薄变体 |
| 结构即契约 | 固定输出模板、Input/Output 示例、清晰小标题 | 每字段 ocr_prompt 统一骨架 |
| 解释 Why，不堆 MUST | 全大写 MUST/ALWAYS/NEVER 是黄旗 | 反思/optimizer prompt 改写 |
| 泛化而非过拟合 | 服务百万次调用；少用位置死规则，多用模式 | 要求 2 |
| 精简 | 删不出力文本；读 transcript 找浪费 | 要求 4 |
| 复用沉淀为脚本 | 重复工作固化成 script | composer 确定性装配 |
| 变体目录化 | 多领域按 variant 组织，只读相关文件 | 国家 agent / skill |
| 评测驱动迭代 | draft→with/without→断言→benchmark→人审→改 | **当前缺失，最大短板** |
| 内部自洽 | skill 不能自相矛盾 | 要求 5 |

---

## 2. 现状诊断

**composed_prompt 实际装配顺序**（`service/composer.py:assemble_prompt`）：

```
GLOBAL_PREAMBLE
country_global_text (Part 1 + 瘦身 Part 2)
"# 整体输出 Schema" + schema JSON
GLOBAL_OUTPUT_CONTRACT_DETAILS (Part 3, §3.1-3.9)
"# 模块识别指令" + 各模块 blob (## N. name + ocr_prompt)
GLOBAL_SELF_CHECK
```

| 要求 | 缺口（含文件位置） |
|---|---|
| 1 层次 | P1/P2 是 `country_global_text` 裸文本；schema 块夹在 P2/P3 之间割裂阅读流；模块体是自由 blob（`composer.py:79-81`） |
| 2 泛化 | 跨样本对照 `reflector._build_cross_doc_block` 只喂国家 agent（`reflector.py:109-138`）；prompt 仍提 `bbox 位置`；泛化产物是自由文本 |
| 3 手递手 | `ReflectionResult.fix_suggestions: list[str]` 自由文本 → prompt 后缀 → optimizer 再解析（`reflector.py:28-37`） |
| 4 精简 | 4 个 skill + 国家 agent 的「4 问」重复（`reflection/skills/*.yaml`、`country_agents/**`） |
| 5 矛盾 | §⑤.3 要求累积不覆盖 → 多轮堆叠矛盾后缀，**无检测/收敛** |

**不可违反的约束**：§① P3 + GLOBAL_* 仅平台源码可改、渲染顺序与 `country_global` keyword-only 不变；
§③.4 composer 纯字符串拼接、不调 LLM；§⑤.3 单 diff 多 skill 累积；§② 客户路径 `enable_meta=False`、
module meta 守护、不 SQL 删 module 行。

> **「融入 composer」的边界**：确定性能力（层次、骨架渲染、结构 linter）进 composer；
> 需 LLM 的能力（泛化、矛盾消解）放 composer **上游**（反思/fork/round 管线）。

---

## 3. 目标架构

### 3.1 FieldRule —— 贯穿全链路的结构化中间表示（解决要求 3）

```
FieldRule {
  semantic          # 业务语义 + 别名
  anchors[]         # 相对锚点（邻近标签文本/区块），而非绝对坐标
  format_rule       # 类型/单位/去千分位/null 规则
  disambiguation[]  # 易混字段 + 区分依据
  generalization { rule, evidence_per_sample[], holds_for_all: bool }   # 要求 2
  provenance[]      # 每条规则来自哪轮/哪个 diff（审计 + 矛盾消解依据）
}
```

反思、fork 扩展、optimizer、verifier 全读写同一对象；composer 确定性渲染成统一骨架。
自由文本退为 `rationale`（仅供人看），不进 prompt 主体。

### 3.2 composed_prompt 目标层次（要求 1）

```
# 导航（TOC：本 prompt 由 Part1/2/3 + 模块指令组成）
# Part 1 · 国家事实        （事实/推断/默认值，固定小节编号）
# Part 2 · 字段语义总览    （字段清单 + schema 引用合并到此，紧贴 P1 事实）
# Part 3 · 输出装配契约    （平台 §3.1-3.9，原样注入，不动）
# 模块识别指令
  ## N. <field>            固定骨架：· 语义 · 取值锚点 · 格式 · 排歧 · 跨样本规则
# 输出前自检
```

要点：schema 块从「夹在 P2/P3 中间」移入 **Part 2**（与字段清单同处），让 P3 紧跟 P1 事实；
每个模块体由 FieldRule 骨架渲染，LLM 逐字段同构解析。

### 3.3 composer 内的确定性「结构 linter」（合规 §③.4）

渲染前对每个 module 的 FieldRule 做**确定性**矛盾检测（互斥指令、format 与 schema type 冲突、
provenance 新旧规则文本对冲）→ 命中则标记 `needs_reconciliation=True` 抛给上游。语义消解由上游
LLM 步骤（reconciler）完成，composer 不调 LLM。

---

## 4. 分阶段迭代计划

> 排序遵循「先有度量再改」。每阶段验收都包含「Phase 0 benchmark 不回退」。

### Phase 0 — 评测底座（基础，先行）
- **目标**：离线 prompt 评测 harness。每国一组 golden 文档 + GT → 跑 composed_prompt OCR →
  逐 module accuracy → with/without（旧 vs 新结构）benchmark（mean±std、delta）。
- **新增**：`backend/app/ocr_optimizer/eval/{golden_set/, run_eval.py, benchmark.py}`；
  复用 `ocr_runner`/`evaluator`/`ground_truth`。
- **验收**：跑通基线 benchmark；后续每阶段以此为合并闸门。
- **守护**：纯离线，不动线上路径。

### Phase 1 — Prompt 层次重构（composer，确定性，要求 1）
- **目标**：落地 3.2 的显式 P1/P2/P3 + TOC + 模块骨架渲染。
- **文件**：`composer.py`（顺序/标题/骨架渲染器）、`template_loader.py`（P1/P2 结构化切分）、
  `output_contract.py`（P3 标题对齐）。GLOBAL_PREAMBLE/SELF_CHECK 作平台源码精简重写。
- **验收**：benchmark 不回退；composer round-trip 测试通过。
- **守护**：§① P3 文本与渲染顺序语义不变；§③.4 不调 LLM。

### Phase 2 — FieldRule 结构化手递手契约（要求 3）
- **目标**：定义 FieldRule schema，作为 reflection→fork→optimizer→composer 唯一中间表示；
  module 已有的 `ocr_suggestions{semantics/position/...}` 升级为 FieldRule。
- **文件**：新增 `field_rule.py`（pydantic + 渲染器）；改 `reflector.py`、`module_optimizer.py`、
  `composer.py`、`persistence.py`。
- **验收**：round-trip 单测（FieldRule→prompt→可解析）；benchmark 不回退。
- **守护**：§② module meta 不丢；§⑤ 累积语义保留（见 Phase 4）。

### Phase 3 — 泛化推断强化（要求 2）
- **目标**：跨样本对照喂给**所有**反思路径（含全局 skill）；prompt 改为「列每样本观测→归纳
  覆盖全部样本的规则→自检 holds_for_all」；弱化绝对位置，改用相对锚点 + 值形态模式；
  写入 `FieldRule.generalization`。
- **文件**：`reflection/skills/*.yaml`、`country_agents/**`、`reflector.py`（对照块通用化）。
- **验收**：构造「位置漂移」样本 → 新结构 accuracy 显著优于旧（位置式）。
- **守护**：§① 反思只改 Part 2 字段语义，不碰 Part 3。

### Phase 4 — 矛盾检测与意图对齐（要求 5）
- **目标**：composer **上游**新增「coherence 消解」步骤：当某 field 的 `provenance` 多条规则
  冲突时，LLM 消解，**按最新用户意图/GT 收敛**为单一自洽规则，保留被取代项到审计字段。
- **文件**：新增 `reconciler.py`（走 `llm_text_completion_failover`）；`composer.py` 确定性 linter；
  接入 `customer_iteration._fork_api_definition` 与 `run_orchestrator._run_one_round`。
- **验收**：构造「R1 取括号内 / R3 取括号外」冲突 → 收敛为最新意图、无双重指令。
- **守护**：§③.4 消解在 reconciler 不在 composer；§⑤.3「单 diff 多 skill 累积」**不变**——
  本步只消解**跨轮矛盾**；fail-open（消解失败保留旧 prompt + 记审计）。

### Phase 5 — Skill 库重构（skill-creator anatomy，要求 4）
- **目标**：reflection/ 重组为「公共基底（统一 FieldRule 输出 schema、术语表、泛化教义、
  Input/Output 示例）+ 薄变体」；删重复「4 问」；大引用文件加 TOC；收紧 match 谓词与 description。
- **文件**：新增 `reflection/base/*.md`；瘦身 `skills/*.yaml`、`country_agents/**`；
  loader 支持基底合并。
- **验收**：总文本量下降且 benchmark 不回退；新增一国 agent 成本（行数）显著下降。

### Phase 6 — 文档与触发优化 + 收尾
- **目标**：更新 `docs/prompt-system.md` 与 `CLAUDE.md`（§① 渲染顺序、§⑤ 新增「跨轮矛盾消解」
  条款、新增 FieldRule 不变量）；为新 skill/agent 跑 skill-creator 式「触发/输出」评测。

---

## 5. 关键取舍与守护小结

- composer 保持确定性：结构/骨架/linter 进 composer；泛化与矛盾消解留上游 LLM 步骤。
- 每阶段以 Phase 0 benchmark 为闸门：准确率回退即不合并。
- §⑤ 边界：保留「单 diff 多视角累积」，新增「跨轮矛盾按最新意图消解」，二者不冲突，
  会在 CLAUDE.md 写清。

---

## 6. 进度跟踪

| Phase | 状态 | commit |
|---|---|---|
| 0 评测底座 | ✅ 完成（harness + CLI + 4 offline 测试） | 待 commit |
| 1 层次重构 | 未开始 | — |
| 2 FieldRule | 未开始 | — |
| 3 泛化 | 未开始 | — |
| 4 矛盾消解 | 未开始 | — |
| 5 skill 库 | 未开始 | — |
| 6 文档/触发 | 未开始 | — |
