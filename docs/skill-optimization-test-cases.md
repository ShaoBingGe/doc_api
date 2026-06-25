# 技能优化重构 — 测试用例设计（编码前）

> 配套 [ADR-001](./ADR-001-skill-optimization-reflact.md) 与 [开发计划](./skill-optimization-plan.md)。
> 流程纪律：**先设计测试用例（本文）→ 再编码**。本文是可评审的测试契约；通过后再落地。

## 0. 测试哲学（token 经济是第一约束）

| 层 | 是否调真实 OCR | 频率 | 决定性 | 关哪一期 |
|---|---|---|---|---|
| **L0 harness 自检** | 否（mock processor） | CI 每次 | 确定性 | P0' |
| **L1 机制行为** | **否（注入 rollout 分数，零 OCR）** | CI 每次 | 确定性 | P1 |
| **L2 验收（Japan-inv 三数）** | 是（少量、阶段边界） | **手动/夜间** | 统计 | P1/P2 |
| **L3 回归护栏** | 部分（golden_set 缓存） | 手动/夜间 | 确定性+统计 | 每期 |
| **L4 成本预算** | 计数即可 | CI（mock）+ 手动核对 | 确定性 | P1 |

**关键原则：所有「机制是否正确」的判定都用注入的合成 rollout 分数，不烧 token。** 只有「泛化到底有没有
提升」（L2）和「有没有打破现网」（L3）才碰真实 OCR，且按计划 §五 仅在阶段边界跑、test 全量只跑数次。

---

## 1. 共享夹具（conftest）

| 夹具 | 作用 | 备注 |
|---|---|---|
| `japan_inv(split, k=None, seed=42)` | 读 `Japan-inv/<split>/{docs,labels}`，返回 `[(pdf_path, gt_entity), ...]`；`k` 抽样 | GT 字段名与平台 canonical 1:1 |
| `mock_processor(script)` | 假 processor：按 `script[sample_id] → structured_data` 返回，**不调 VLM** | L0/L1 全用它 |
| `synthetic_rollouts(per_field_acc)` | 直接造 `OcrModuleIteration.per_sample_results`（可控 hard/soft/字段精度） | L1 机制测试核心 |
| `fake_skill(content)` / `fake_edits([...])` | 造技能文档 + 类型化 edit | L1 |
| `golden_baseline()` | 读 `ckpt/bench/baseline.json`（P0' 产出） | L2 对比基准 |
| `tmp_api_def(country, samples)` | 在测试 DB 建一个国家模板 API + 样本（复用现有 factory） | L1/L3 |

---

## 2. 测试用例目录

### L0 — 基准 harness 自检（关 P0'）

| ID | 用例 | Arrange / Act / Assert | 通过判据 |
|---|---|---|---|
| **SKT-H01** | 划分加载正确 | 读 train/val/test/init → 计数 | 182/73/108/8，docs 与 labels 一一配对 |
| **SKT-H02** | 字段对齐无丢失 | 取一 GT entity → 与平台 canonical 字段集求交 | 平台字段全在 GT 键里（docType…billFromTaxIdentificationNumber） |
| **SKT-H03** | 评分器接线正确 | 构造「预测==GT」与「预测≠GT」两例 → `evaluator.compare` | 完全相等→hard=1；单字段错→hard=0、soft∈(0,1) |
| **SKT-H04** | 三数计算正确 | mock_processor 喂已知预测 → harness 算 train/val/test 精度+裂隙+调用数 | 与手算一致；调用数==样本数 |
| **SKT-H05** | 复现性 | 同 seed 跑两次抽样 | 样本集合、三数逐位相同 |
| **SKT-H06** | OCR 缓存命中 | 同 `(sample, skill_version)` 第二次 | 不再调 processor（命中计数↑） |

### L1 — 机制行为（关 P1，**全部零 OCR**）

| ID | 机制 | Arrange / Act / Assert | 通过判据 |
|---|---|---|---|
| **SKT-G01** | 留出 Gate：拒过拟合 | 候选在 train↑、在 val↓（注入分数）→ Gate | **reject**，版本回退到 current |
| **SKT-G02** | 留出 Gate：纳真改进 | 候选在 val 严格↑ → Gate | accept |
| **SKT-G03** | Gate：平/降则拒 | val 分相等或下降 | reject（严格 > 才接受） |
| **SKT-G04** | 滚动留一切分 | 12 样本 → 切 train/val | 每折 val 不空、anchor 恒在 train、各样本恰当过 1 次 val |
| **SKT-G05** | soft 指标判别 | val 仅 1 字段部分改善（hard 不变） | soft 门能分辨（hard 门不能）→ 证明默认用 soft |
| **SKT-C01** | 编辑预算/Clip top-L | 给 8 条候选 edit → clip(L=3) | 输出恰 3 条，按 support_count 降序 |
| **SKT-C02** | 不再整段重写 | 优化一字段 → 产出 edit | 是类型化 `FieldEdit`（append/replace/delete），diff 行数 ≤ 阈值 |
| **SKT-C03** | 自主 LR 随严重度 | 未达标严重(0.2) vs 轻微(0.9) | 严重→更大 L；轻微→更小 L |
| **SKT-M01** | minibatch 成组反思 | 同字段 5 样本 diff 成组 | 1 次反思调用覆盖 5 样本（非 5 次） |
| **SKT-M02** | 支持度计数 | 3 样本同错 + 1 样本独错 | 共性 edit `support_count=3`，独错=1 |
| **SKT-M03** | 聚合去重 | 两样本产同义 edit | 合并为 1 条，support 累加 |
| **SKT-D01** | 缺陷 vs 失误：系统性 | k/N 样本同错 | 标 `SKILL_DEFECT` → 改正文 |
| **SKT-D02** | 缺陷 vs 失误：偶发 | 1/N 偶发错 | 标 `EXECUTION_LAPSE` → 只进附录，正文**字节不变** |
| **SKT-D03** | 不确定默认 LAPSE | 模糊信号 | 默认 LAPSE（保护正文） |
| **SKT-R01** | 被拒缓冲：不重提 | 第 1 轮拒 edit X → 第 2 轮反思 | 候选里**无 X** |
| **SKT-R02** | 被拒缓冲：跨轮持久 | run 内多轮 | buffer 累积、随 run 存取 |
| **SKT-N01** | 噪声样本门 | 已确认 < 3+N 启动迭代 | 拦截 + 引导补传；≥ 12 放行 |

### L2 — 验收（Japan-inv 三数，关 P1/P2，**手动/夜间，真实 OCR**）

| ID | 用例 | 方法 | 通过判据（vs Baseline） |
|---|---|---|---|
| **SKT-A01** | 泛化提升 | test(108) 全量，重构后 1 次 | **test 字段精度 ≥ baseline**（核心收益） |
| **SKT-A02** | 过拟合收窄（中心假设） | 仅 3 精选样本迭代，量 train_acc−test_acc | **裂隙 NEW < OLD** 且 test 不降 |
| **SKT-A03** | 噪声量肘点 | k∈{3,5,8,12,16,24}，test 缩减子集50 | 曲线单调饱和，肘点 ≈ 计划 N（复核 N=9 合理） |
| **SKT-A04** | 技能复用冷启动（P2） | 同国第 2 个 API 用全局技能 | 冷启动 test 精度 > 无技能 |

### L3 — 回归护栏（关每期，**必须不破**）

| ID | 用例 | 通过判据 |
|---|---|---|
| **SKT-RG01** | golden_set 不降 | MY/JP `eval/golden_set` 跑分 ≥ 重构前（缓存复用） |
| **SKT-RG02** | country-lock 不变 | 迭代后 invoiceNumber/invoiceDate/billFromName/billFromTaxIdentificationNumber 的 schema_fragment+ocr_prompt 字节不变（被排除反思+钉死） |
| **SKT-RG03** | 单调 finalize | 最终版 val 分 ≥ 起始版 |
| **SKT-RG04** | 客户 override 仍生效 | field_constraints 投影/钉死逻辑不被技能层覆盖 |
| **SKT-RG05** | 现有 OCR 抽取链不变 | document_service 投影/归一化测试全绿 |

### L4 — 成本预算（关 P1）

| ID | 用例 | 通过判据 |
|---|---|---|
| **SKT-B01** | 单轮 OCR 调用上界 | 12 样本×1（step-1）+ Gate 复用，**Gate 不新增 OCR**（计数断言） |
| **SKT-B02** | 0 未达标早退 | 全字段满分轮 → 跳过 optimize+OCR |
| **SKT-B03** | 新增 LLM 阶段受控 | reflect/clip/gate 的 LLM 调用数/轮 ≤ 预算（mock 计数） |

---

## 3. 分期门禁（Definition of Done）

- **P0' 合并**：L0 全绿 + Baseline 快照落盘 + 噪声扫描产出肘点报告。
- **P1 合并**：L1 全绿 + L4 全绿 + L3 全绿 + L2 的 **SKT-A01/A02 达标**（test 不降、裂隙收窄）。
- **P2 合并**：上 + **SKT-A04**（技能复用）+ L3 仍绿。
- **P3/P4/P5**：各自补 L1/L2 对应项 + L3 恒绿。

## 4. CI 与运行策略

- **CI（每次提交）**：L0 + L1 + L4 + L3 中的确定性项（RG02/RG04/RG05）。**全程零真实 OCR、秒级。**
- **手动/夜间（带真实 OCR）**：L2 全量 test + L3 的 golden_set（RG01/RG03）。结果落 `ckpt/bench/<sha>.json`。
- **PR 模板**：附 L2 三数对比表（NEW vs baseline）。

## 5. 待确认（评审点）
1. L1 机制测试是否都接受用「注入 rollout 分数」替代真实 OCR？（强烈建议是——这是 token 经济的关键。）
2. L2 验收里 SKT-A02「仅 3 样本量过拟合裂隙」是否作为 P1 的**硬门**（不达标不合并）？（建议是，因为它正是本次重构的中心目标。）
3. 测试落点 `backend/tests/skill_opt/`，是否与现有 `backend/tests/` 约定一致？
