# 设计与开发计划 · 视图分层定案 + 数组/多页错误的结构化反思

> 状态：**待执行**（约定：开放平台新接口联调测试完成后动手）
> 类型：架构定案（零代码）+ 反思机器增强（新功能）
> 原则沿用既有计划：每阶段独立可交付、全量验证、独立提交、可随时停。

---

## 0. 背景与架构定案（2026-08-26 讨论结论）

**问题**：外部开放平台 API（templateId=7 / piaozone 契约）输出嵌套 JSON
（`header.basic / billTo / billFrom / bussiness / payment` + `detail.*`），
而国家模板 yaml、composer、反思、评测全部工作在**扁平** JSON 上，中间由
`open_api_mapper` 加工。要不要把嵌套下沉进 prompt / composer？

**定案：不下沉。扁平 = 规范模型（canonical），嵌套 = 契约视图（view）。**

四条理由（写入 ADR，防止将来被"顺手优化"掉）：

1. **契约不进机器**。`header.bussiness` 这个拼写错误是对接方契约写死的——嵌套
   下沉等于把一个客户的契约（连同错别字）烧进全球层，违反 CLAUDE.md §2.2
   （国别知识只进国别层，机器全球共享；契约知识连国别层都不该进，只属于边缘）。
2. **不制造新错误类**。分组由确定性代码完成时，"值对但放错分组"这类错误
   **结构上不可能发生**；让 LLM 输出嵌套，漂移面从「键名」扩大到「键名 × 路径」。
   qwen 无 response_schema 硬约束，嵌套键路径全靠 prompt 文本约束——刚用
   字段清单治好的键名漂移（5 轮 30/30）会以新形态复发。
3. **迁移面大、收益为零**。全部 `OcrModule.json_path`、黄金集 GT、
   `align_for_path`、edit_intent、reconciler 都要跟着改路径，换来的只是删掉
   mapper 里已测试的确定性代码。
4. **mapper 删不掉**。全字段补齐、标量全字符串化、`sourceFileHash` 回填、
   `apply_aliases` 兜底都是契约整形，无论 LLM 输出什么形状都得在代码里做。

**一句话红线（拟入 CLAUDE.md）**：反思/评测/优化机器只见扁平规范坐标系；
一切契约形状（嵌套、全字符串化、别名）在 `open_api_mapper` 边缘层单向生成；
若将来外部通道回传修正，必须在入口**逆映射回扁平 + 反字符串化**再进机器。

---

## 1. 代码取证（已核实，2026-08-26）

| # | 事实 | 位置 | 对本计划的含义 |
|---|---|---|---|
| 1 | 评分侧**已有**行级贪心对齐：相似度矩阵 → 贪心配对 → 配对分/max(len)，未配对行记「漏提取/多提取」（批次4） | `service/evaluator.py:76-130 _compare_lists` | 评分不需要新做；但对齐逻辑**埋在 evaluator 内部**，反思侧无法复用 |
| 2 | edit_intent 是**纯字符级标量**判定（NORMALIZE/RETARGET/…），对数组值无结构感知 | `reflection/edit_intent.py` | 客户改明细行时，行插入/合并产生整片假 diff，反思收到噪声 |
| 3 | composer 支持 `json_path="$"`/`""` 的**全局模块**：prompt 正常渲染，schema 装配时 properties 并入记录对象（空 fragment 则只贡献 prompt 文本） | `service/composer.py:210-216` | 「切分伪模块」有现成的合法落点，**不需要动 composer** |
| 4 | 反思只允许改 Part 2 模块；`country_global_text`（含 §1.0 切分规则）新版本原样沿用、平台专属 | CLAUDE.md 红线①、§3.2 | 客户回路的**实体切分错误目前无处可写**——这是空白，不是 bug |
| 5 | 前端工作区**没有**实体级合并/拆分标注入口（搜到的 merge/split 均为代码重构注释） | `frontend/src` 全文检索 | Phase D 只能先做机器侧承接；标注入口是另一件事 |
| 6 | reflector 按 `diff.module_key` 路由反思结果，同 key 多 diff 累积合并 | `reflection/reflector.py:114,199` | 类型化数组操作可以作为 diff 的增强字段无缝进入现有路由 |
| 7 | 数组单元格修正被跨样本反思语料**显式跳过**（字段名含 `[`/`.`） | 已录 [plan-line-items.md](./plan-line-items.md) §0 | 归 plan-line-items 修，本计划不重复 |

---

## 2. 与 plan-line-items.md 的边界（两计划并行不冲突）

| | plan-line-items（待执行） | 本计划 |
|---|---|---|
| 管什么 | 数组的**结构编辑**：新增带列定义的数组、加/删/改列、列级反思语料、行级 GT 补删 | 数组修正的**判定与归因**：把客户对数组的编辑分类成类型化操作 + 证据；实体切分反思落点；视图分层 ADR |
| 共享构件 | 需要行对齐（其 §行级 GT 依赖 evaluator 批次4 信号） | 需要行对齐（编辑分类的第一步） |

**约定**：本计划 Phase A 先把 evaluator 里的对齐器抽成公共件；
plan-line-items 落地时改用同一个。两边共享一套对齐逻辑是硬要求——
否则会出现「反思说修好了、评测说没变」的信号分裂。

---

## 3. 目标与非目标

**目标**
1. 视图分层定案固化为 ADR + CLAUDE.md 红线，含外部反馈逆向通道的规范（先文档）；
2. 客户对数组字段的修正，反思前先被**纯代码**分类为类型化操作
   （CELL_EDIT / ROW_MERGE / ROW_SPLIT / ROW_ADD / ROW_DROP / COLUMN_SHIFT），
   每类附结构化证据（对齐行对 + 邻行 + 归属实体 `page[]`），证据先行；
3. 病因→落点有确定路由：格错→列规则、并/拆→行切分规则、多/漏→覆盖排除规则、
   平移→列锚点规则，全部落明细模块 `ocr_prompt`（Part 2 合法范围）；
4. 实体级切分错误（两票并一/一票拆二）在客户回路有合法反思落点
   （`json_path="$"` 切分伪模块），Part 1 仍只归平台。

**非目标**
- 不把嵌套下沉进 prompt/composer（本计划的定案恰恰相反）；
- 不做列级独立 OcrModule（与 plan-line-items 非目标一致）;
- 不实现外部修正回传 API（当前无此通道，只落逆映射规范）；
- 不做前端实体合并/拆分标注 UI（取证 #5：入口不存在，另立计划）；
- 不改评分算法（批次4 已达标）。

---

## 4. 分阶段计划

### Phase A · 抽取公共行对齐器（纯重构，字节级等价）

把 `evaluator._compare_lists` 中的对齐部分（相似度矩阵 + 贪心配对 + 未配对标记）
抽到独立模块 `service/row_align.py`：

```python
def align_rows(gt_rows, ocr_rows, *, similarity) -> AlignResult
# AlignResult: pairs[(gi, oj, score)], unmatched_gt[], unmatched_ocr[]
```

- evaluator 改为调用它，行为**字节级等价**（现有评测测试全绿为验收）；
- `similarity` 参数化：evaluator 传 `_compare_recursive`，edit_intent 侧
  传自己的字符级相似度——同一对齐骨架，两侧各自的比较语义。

交付：`row_align.py` + evaluator 重构 + 等价性测试。

### Phase B · 数组编辑分类器（edit_intent 扩展，纯代码零 LLM）

新增 `classify_array_edit(original_rows, corrected_rows) -> list[ArrayEditOp]`：

| 操作 | 判定（基于 Phase A 对齐结果） | 病因假设 |
|---|---|---|
| `CELL_EDIT` | 行 1↔1 配对，单格差异 | 值读错/列语义错 |
| `ROW_MERGE` | OCR 两行内容拼接 ≈ GT 一行（1↔n 包含检测） | 折行被拆成两行 |
| `ROW_SPLIT` | GT 两行 ≈ OCR 一行 | 两行被并成一行 |
| `ROW_ADD` | GT 行无匹配（unmatched_gt） | 漏行（跨页续表首行高发） |
| `ROW_DROP` | OCR 行无匹配（unmatched_ocr） | 小计/运费行被当明细 |
| `COLUMN_SHIFT` | 1↔1 配对内多格差异且值在列间平移（A 列旧值 ≈ B 列新值） | 列锚定错位 |

- `CELL_EDIT` 的单格差异**继续喂现有字符级分类器**——NORMALIZE/RETARGET
  的证据机制原样生效，只是先定位到格；
- 每个 op 产出证据块（沿用 `EditIntent.render()` 的「证据可信」文风）：
  对齐行对原文、上下各一邻行、归属实体的 `page[]`；
- GT 存储**零改动**：Annotation 照旧存整个修正后数组，op 在反思时现算。

交付：分类器 + 每类操作的构造性单测（含"插入一行不得把后续行全判为 CELL_EDIT"
的回归用例——这正是现状的噪声形态）。

### Phase C · 反思路由 + 数组专用技能

- `reflector._append_evidence_blocks` 挂载点追加数组 op 证据（与现有
  edit_intent 证据同一机制，证据先行、推理在后）；
- 病因→落点路由（进 reflector，确定性代码）：所有数组 op 都落**明细模块**的
  `ocr_prompt`（Part 2 合法），但按 op 类型定向到不同规则段语义：
  MERGE/SPLIT→行切分、ADD/DROP→覆盖/排除、SHIFT→列锚点、CELL→列格式；
- 新增 2–3 个薄技能 yaml（`reflection/skills/`）：`row_segmentation` /
  `row_coverage` / `column_anchor`，match 谓词认 op 类型，引用 base 公共基底；
- 累积/矛盾门/reconciler **一律不动**——建议仍以「# 客户反馈补充」块落地。

交付：路由 + 技能 yaml + mock LLM 路由单测。

### Phase D · 实体切分伪模块（多票据/多页，机器侧承接）

- 实体级对齐：`invoiceNumber` 精确匹配优先，退化用 `page[]` 重叠度；
  判定 `ENTITY_MERGE` / `ENTITY_SPLIT`；
- 在客户 API 上**惰性创建** `json_path="$"`、空 schema_fragment 的
  `document_segmentation` 伪模块（取证 #3：composer 原生支持，prompt 渲染、
  schema 不受影响）——实体切分反思写它，Part 1 纹丝不动；
- 平台提升路径：伪模块中沉淀的切分规则由平台**人工审阅**后升入国家模板 §1.0
  （走黄金回路 A/B），不自动——与技能库「晋升需管理员拍板」同构；
- 诚实边界（取证 #5）：前端无实体合并/拆分标注入口，本 Phase 的判定信号
  只能来自「客户把实体 A 的字段值改成了实体 B 的内容」这类间接证据 +
  黄金集离线比对；直接标注入口另立计划。

交付：实体对齐器 + 伪模块惰性创建 + 反思落点 + 多票据样本 e2e（mock）。

### Phase E · ADR + 逆向通道规范（文档，可与 A 并行先做）

- `docs/ADR-003-canonical-flat-vs-contract-view.md`：§0 定案全文 + mapper
  分组表声明式现状（`BASIC_FIELDS` 等）+ 第二外部契约的接入姿势（只加视图映射）；
- 逆向通道规范：嵌套→扁平 unmap + **反字符串化**（`"5,286.12"`→`5286.12`、
  日期还原）——否则外部采集的 GT 会让 edit_intent 产出成片假 NORMALIZE；
- CLAUDE.md 补红线一条（§0 末尾那句话）。

---

## 5. 验证

| 阶段 | 验收 |
|---|---|
| A | 现有全部评测相关测试零改动全绿（对齐字节级等价） |
| B | 构造性单测全类覆盖；用 MY 客户真实标注跑一份**错误类型分布报告**（这份报告本身就是对分类法是否切合实际数据的检验） |
| C | mock LLM 下：每类 op 路由到预期规则段；技能 match 命中单测 |
| D | 多票据样本（DOC_07_15_25006 六票样本）e2e：人为制造并票 GT → 反思落到伪模块而非字段模块 |
| 全程 | 改机器必跑黄金集 A/B，严格分不降（CLAUDE.md 自检 5）；客户回路 `enable_meta=False` 不受影响（自检 1） |

---

## 6. 排序与依赖

```
E(ADR, 半天) ──────────────┐  可先行，零代码
A(对齐器抽取, 半天) ─→ B(分类器, 1-2天) ─→ C(路由+技能, 1天) ─→ D(实体级, 1-2天)
```

- A 是 B 的硬前置；C 依赖 B 的 op 定义；D 复用 B/C 的机制但独立可停；
- 与 plan-line-items 无执行顺序依赖，唯一约定是它落地时改用 Phase A 的对齐器；
- 每阶段独立提交；B 完成后即可拿真实标注出分布报告，**报告结论可能修订 C/D 的优先级**（如实际数据中 COLUMN_SHIFT 极少则其技能降级为可选）。
