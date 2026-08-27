# 设计与开发计划 · 生产链路未向模型传递 `response_schema`

> 状态：**分析完成，分阶段执行中**（分支 `feat/schema-passed-to-model`）
> 类型：平台级架构修复
> 发现于：2026-08-27，template 7 prompt 迭代过程中；2026-08-27 晚完成全链路摸查与 Phase 0 基线

---

## 0. 缺陷本体（2026-08-27 摸查后，比首次记录的范围更大）

### 0.1 开放平台提取路径 —— schema 完全死亡（首次记录的部分）

```python
# backend/app/services/extract_service.py · _run_processor
if prompt:
    instruction = prompt          # ← schema 参数被完全忽略
elif schema:
    instruction = f"...Schema:\n{json.dumps(schema)}"   # 只有无 prompt 时才用
...
raw_text = processor.process_document(storage_path, instruction)
#          ↑ 只传两个参数——processor 的第三参 runtime_config 根本没被使用
```

影响面：`/api/v1/extract/`（X-API-Key）、开放平台同步 `document/analyze`、
异步 `analyze/async` —— **全部生产提取流量**。

### 0.2 工作区路径 —— 管道修好了，水从来没放过（本次新发现）

`document_service._call_processor` 曾被修过一次（键名 `"schema"` → `"response_schema"`，
见代码内「结构审查 F12 死参数」注释），它**正确地**组装了
`runtime_config={"response_schema": schema}`。但它唯一的调用链是：

```
reprocess_document → _run_extraction(schema 参数默认 None) → _call_processor(schema=None)
```

`reprocess_document` 从不解析 schema，`_run_extraction` 全仓库只有这一个调用点。
**结论：工作区上传/重识别路径同样从未传过 schema** —— 上一次修复只修了键名，
没有接上水源。

### 0.3 离线基准工具 —— 用的还是死键（本次新发现）

`eval/bench_japan_inv.py:260` 与 `eval/skill_demo_jp.py:43` 传的是
`{"schema": schema}` —— 这个键**没有任何 processor 读取**（正是 F12 修掉的旧键名）。
JP 基准的历史数据也都是「无 schema 硬约束」跑出来的。

### 0.4 真正传了 schema 的只有两处

| 调用点 | 键 | 生效 |
|---|---|---|
| `ocr_optimizer/service/ocr_runner.py`（客户迭代评测） | `response_schema` | ✅ |
| `prompts/tpl7/evaluate.py`（tpl7 离线评测） | `response_schema` | ✅ |

**这就是系统性偏差的机制**：客户迭代回路在「有 schema 硬约束」的世界里给
prompt 打分、早停、做单调守护、选版本；生产提取在「无约束」的世界里运行。
迭代认为的「好」不等于生产的「好」。

### 0.5 连带发现：schema 来源分裂 + 激活不回写（修复时必须处理的坑）

- `api_def.response_schema`：只在 **建 API / 手动更新 / seed 刷新** 时写入。
- `OcrPromptVersion.composed_schema`：客户定制/迭代每建新版本都会重组
  （`run_orchestrator.py:366/1385`），但**激活新版本时不回写** `api_def.response_schema`。

→ 若修复时简单地把 `api_def.response_schema` 传下去，客户迭代过的 API 会传
**陈旧 schema**（还是建 API 时的字段集，丢失 rename/新字段）。
**正确的 schema 源：active 版本的 `composed_schema`，无版本时才回退
`api_def.response_schema`**（与 prompt 的取法 `get_active_composed_prompt` 对齐）。

---

## 1. 根因链（为什么会发生、为什么一直没暴露）

1. **代际断层**：`_run_processor` 是最早的提取代码，设计是「prompt 与 schema 二选一、
   schema 以文本内联」。processor 接口的第三参 `runtime_config` 是后来为
   ocr_optimizer 加的，新代码用上了，旧调用点从未迁移。
2. **接口不设防**：`process_document(path, instruction, runtime_config=None)` 第三参
   可省略且省略不报错，死参数静默存活；`extra` 键（如旧键名 `"schema"`）也被
   静默吞掉，0.3 的死键活了很久。
3. **测试盲区**：全仓库没有一条测试断言「生产提取把 response_schema 传到了 processor」。
4. **症状被两层补偿掩盖**：
   - composer 在 prompt 正文里渲染了「紧凑字段树」，模型靠它也能输出大致正确的
     形状（`composer.py:153` 的注释「完整约束仍由 response_schema 随调用强制」
     在生产路径上是**假话**）；
   - tpl7 迭代把关键规则搬进 prompt 正文（MY 模板 §1.34），进一步补偿了缺失。

---

## 2. Phase 0 基线实测（2026-08-27，已完成）

同一生产 prompt（`build_v1_prompt`，18,576 字符）+ 同一模型
（gemini-3.5-flash · temp=0 · seed=42，与线上逐项一致的本地 `.env`），
3 个样本各跑「传 / 不传」各 1 轮：

| 样本 | 不传 schema | 传 schema | 差异 |
|---|---|---|---|
| S1 单页扫描发票 | 全字段正确（含噪声注册号 `201801036457 (1298487-V)`）| 同左 | **无** |
| S2 六页六张发票 | 6 条，invoiceType **全 Commercial** | 6 条，invoiceType **3 张翻成 Tax Invoice** | **有——首个真实行为分歧** |
| S3 Credit Note | enum=Credit Note、-709.25、origRefs=1 | 同左 | **无** |

**解读**：根形状（array）、切分、金额、负数、enum 主体在两种模式下都稳；
分歧集中在 **invoiceType 子分类**——schema description 里的 Tax Invoice 判据
（「票面含 Tax Invoice / Faktur Pajak」）在传 schema 时开始生效，改变了边缘判定。
哪边是对的需要 Phase 2 按票面逐页裁决（S2 是无文本层扫描件，需人工看图）。
单轮样本量不足以区分「schema 影响」与「模型抖动」，Phase 2 每配置至少 3 轮。

## 3. 三个中途验证样本（已固化到 `backend/eval_samples/schema_ab/`）

选样原则：形态差异最大化，各自压住 schema 最敏感的一类行为。

| # | 文件 | 形态 | 验证的 schema 敏感点 | 通过判据 |
|---|---|---|---|---|
| S1 | `S1_single_invoice.pdf`（EBM INV2507-293）| 1 页扫描件标准发票，注册号有印刷噪声 | 表头字段 + 类型强制（NUMBER/STRING）不破坏抗噪提取 | docType=invoice；invoiceNumber=INV2507-293；totalAmount=5286.12；BRN=201801036457 (1298487-V)；明细 2 行 |
| S2 | `S2_six_invoices.pdf`（DOC_07_15_25006）| 6 页 6 张独立发票，无文本层 | **数组根形状 + 切分**（历史上根形状错配导致 0 分事故的同型场景）；invoiceType enum 边缘判定 | 恰 6 条；6 个互不相同的 invoiceNumber（0020723/0150725/0310725/0490725/0500725/0540725）；invoiceType 以 Phase 2 的逐页人工裁决为准，两模式与裁决一致者胜 |
| S3 | `S3_credit_note.pdf`（CN-2025/519）| Credit Note，负金额 + 原发票引用 | enum 强制不得把 CN 翻成 Commercial；originalInvoiceReferences 数组结构 | invoiceType=Credit Note；totalAmount=-709.25；origRefs ≥1；invoiceNumber=CN-2025/519 |

实验脚本：`backend/eval_samples/schema_ab/run_ab.py`（直调 GeminiProcessor，
零服务依赖，`--rounds N` 控制轮数；结果落 `ab_results.json`）。

---

## 4. 分阶段执行计划

> 原则沿用仓库惯例：每阶段独立可交付、全量验证、独立提交、可随时停。
> 全程在分支 `feat/schema-passed-to-model`，不动 main。

### Phase 1 · 管道打通 + 灰度开关（纯代码，默认行为零变化）

1. `config.py` 增加 `PASS_SCHEMA_TO_MODEL: bool = False`（本地 `.env` 已预置该行）。
2. `persistence.py` 增加 `get_active_prompt_and_schema()`（或复用 `get_active_version`）：
   **schema 与 prompt 同源取自 active 版本**，无版本回退 `api_def.response_schema`
   （治 0.5 的陈旧 schema 坑）。
3. `extract_service`：`extract_document` 取出 schema 后经 `_run_with_failover` 一路
   透传；`_run_processor` 开关开启且 schema 存在时
   `processor.process_document(path, instruction, {"response_schema": schema})`。
4. `document_service`：`reprocess_document → _run_extraction` 把同源 schema 接上，
   同一开关管控（治 0.2）。
5. 顺手修 0.3 两处死键 `"schema"` → `"response_schema"`（离线工具，不受开关管）。
6. 测试（新文件 `test_pass_schema_to_model.py`）：
   - 开关关：processor 收到的 runtime_config 为 None——与现状逐字节等价；
   - 开关开：processor 收到 `response_schema`，且内容 == active 版本 composed_schema；
   - 版本激活后：传的是**新版本**的 schema（防陈旧源回归）;
   - qwen 路径：传了也无害（DashScope 忽略）；
   - 全量 `pytest -q` 不新增失败。

### Phase 2 · 三样本中途验证 —— ✅ **已完成（18/18 全过，开=关）**

**裁决**：S2 六页逐页渲染标题区看图确认，**6 页标题全是 `INVOICE`**，
无一页印 `TAX INVOICE` → GT 全部 Commercial Invoice（见 `ground_truth.json`）。

**发现并修掉的两处退化**（都是「开 < 关」，按预案修 description 而非关开关）：

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 传 schema 后 6 张里的 3 张 invoiceType 被升格为 Tax Invoice | 翻转的恰是票面印有 `(Sales Tax Reg No: W10-1808-21032194)` 的 p2/p4/p5。schema description 原文「票面包含 "Tax Invoice"…**等**关键词」的"等"字被宽解成「有销售税登记号＝税务发票」 | 模板 7_12：判据收紧为**标题文字**含 TAX INVOICE / INVOIS CUKAI；明写「仅有 Sales Tax Reg No / SST 列不构成依据」 |
| 2 | 传 schema 后 BRN 稳定误读 `1298487-V` → `1296487-V`（3/3 轮一致，非抖动；关闭时 3/3 轮正确）| BRN 的「逐位核读」自检**只覆盖 12 位新号**，括号内旧号无任何校验；schema 硬约束下模型对模糊字符辨识变差，恰好暴露这个缺口 | 模板 7_13：自检扩展到括号内旧号（6–7 位数字+字母），列出 `8↔6`/`3↔8`/`1↔7` 高频混淆对与「数闭合环」的辨字方法 |

**这两处都是 schema description 长期是死字才留下的债** —— 描述从没被模型读过，
写得含糊/有缺口也没人发现。schema 一通，问题当场显形并被修掉，
两处修复对「关」的一侧同样有益（规则本身更严密了）。

最终：3 样本 × 2 模式 × 3 轮 = 18/18 全过，开与关逐项持平。
复现：`.venv/bin/python eval_samples/schema_ab/phase2_verify.py --rounds 3`

> 同构性修正：过程中发现本地 `api_def.model_name` 是 `gemini-2.5-flash`
> 而线上是 `gemini-3.5-flash`（行级 model_name 覆盖 `.env`），已对齐——
> 否则 A/B 比的是两个不同模型。

<details><summary>原计划（已执行）</summary>

- 走**完整生产调用链**（起本地服务，经 `/document/analyze`），不再直调 processor——
  验证的是管道，不只是模型行为。
- 开关关/开各 3 轮 × 3 样本，对照 §3 判据；
- S2 的 invoiceType 分歧先做**逐页人工裁决**（扫描件看图定 GT），谁贴近 GT 谁胜；
- 出现「开 < 关」的字段 → 定位是 schema description 与 prompt 正文冲突，
  修描述（国别层）而不是关开关，修完重跑。
- 通过标准：三样本判据全过，且「开」不劣于「关」。
</details>

### Phase 3 · 黄金集 A/B —— ✅ **已完成（4 seed 全部「传 ≥ 不传」，无一下降）**

**工具改动（初版 `--no-schema`，后升级）**：黄金集评测走 `ocr_runner`，
它历史上**恒传 schema**，`PASS_SCHEMA_TO_MODEL` 管不到（§0.4 的不同构实体）。
Phase 3 先用 `--no-schema` 在批次入口传 None 做 A/B；**Phase 3.5 根治**：
`ocr_runner._run_single` 直接跟随开关（评测与生产同一个世界），
`run_golden_batch` 的 flag 升级为 `--schema {auto,on,off}` —— auto 跟随配置，
on/off 进程内改写开关供 A/B。报告 `schema_passed` 记录实际生效值。

**结果**（每批 5 个样本 —— `sample_batch` 有 `size=min(size,5)` 硬上限，
非参数写错；4 个 seed 共 20 样本次 × 2 模式）：

| seed | 不传 schema | 传 schema | 差 |
|---|---|---|---|
| 7  | 0.3111 | **0.4667** | **+0.1556** |
| 13 | 0.6000 | 0.6000 | 0 |
| 21 | 0.4222 | 0.4222 | 0 |
| 42 | 0.4889 | 0.4889 | 0 |
| **均值** | **0.4556** | **0.4945** | **+0.0389** |

seed=7 那批逐字段：doc_type 0.40→0.60、invoice_number 0.20→0.40、
invoice_date / total_net_amount / total_amount / currency / page 均 0.40→0.60；
invoice_type 与 line_items 持平。**没有任何字段下降**，核心三字段门通过。

> ⚠️ **line_items 在 8 次运行里恒为 0/5**（两种模式都是）。它比的是**整个明细
> 数组的 exact-match**——每行每字段全等才算对，浮点/描述文字/单位任一处差异
> 即判错，实务上几乎不可能达成。**该字段在本次 A/B 中判别力为零**，
> 「不得下降」在它身上是空条件。与本次改动无关（两侧一致），
> 但黄金集判据本身值得单独排查——已记入 §7 遗留项。

JP 黄金集未跑：仓库根缺 `JP_invoice_prompt.yaml`（同一原因让
`test_bench_harness.py` 的 2 条用例长期红灯），JP 侧无法在本地复现。

### Phase 4 · 灰度上线（腾讯云，唯一在线服务器）

1. 合分支 → 部署代码（开关默认 False，上线即无行为变化）；
2. `.env` 置 `PASS_SCHEMA_TO_MODEL=true` 重启，观察 24–48h：
   - A0704 占比（当前基线 ~17%）不异常升高；
   - 失败率 / 平均耗时无回归；
   - 抽 10 个真实任务的输出与前一日同类样本对照。
3. 回退 = `.env` 改一行重启（这正是开关存在的意义）。

### Phase 5 · 收尾

- 稳定后开关默认值改 True，写入部署 runbook；
- 重新评估 MY §1.34（nameOfInvoice/unitPrice 当年因本缺陷搬进正文的规则）
  是否搬回 description，让 Part 2 的设计承诺重新成立；
- 更新 `docs/prompt-system.md` 与国家模板 Part 2 说明（schema description
  何时生效）；`composer.py:153` 那句「response_schema 随调用强制」使之成真。

---

## 5. 风险清单

| 风险 | 缓解 |
|---|---|
| Gemini 硬约束改变边缘判定（Phase 0 已实测到 invoiceType 翻转）| Phase 2 逐页 GT 裁决 + Phase 3 严格分门；冲突改 description 不改机器 |
| 陈旧 schema 源（0.5）| schema 与 prompt 同源取 active 版本，测试锁死 |
| qwen 行为差异 | DashScope 忽略 response_schema，天然对照组，零变化 |
| 客户迭代回路受影响 | 迭代评测（ocr_runner）本来就传 schema——修复后生产与评测**同构**，这正是目标 |
| 上线出问题 | 开关一行回退；默认 False 部署，观察后再开 |

## 5.5 Phase 3.5 · 两处评测基建修复 —— ✅ 已完成（2026-08-28）

1. **评测链路收编进开关**：`ocr_runner._run_single` 的 schema 下发改为跟随
   `PASS_SCHEMA_TO_MODEL` —— 客户迭代、黄金集、灰度全部与生产同构；开关怎么拨，
   评测和生产就同时在哪个世界。`run_golden_batch --schema {auto,on,off}`
   （auto=跟随配置；on/off=进程内强制，供 A/B）。
2. **行表判据重写**（`harness._score_row_table_strict`）：line_items/tax_summary
   从「整数组 exact-match」改为**行数一致（gate）+ 逐行关键字段匹配率**。
   关键字段=articleName/quantity/unitPrice/netAmount（商品行）与
   taxCategory/taxRate/tax/netTaxableAmount（税表行）；description/unitOfMeasure
   不参与——GT 把品名复制进了 description 而模板 §1.35 要求那种情况留空，
   比它等于惩罚正确行为。数值比较容差 1e-6（6 vs 6.0 相等）。
   **验证**：seed=7 重跑，line_items 从恒 0/5 变为 off=0.33 / on=0.37——
   有判别力了，且传 schema 仍 ≥ 不传（overall 0.3481 vs 0.3519）。
   注：黄金集分数存在跨运行抖动（同 seed 两次 off 分别 0.3111/0.3481），
   **结论只依赖同一运行内的两模式对照**，不依赖跨运行绝对值。

## 7. 遗留项（本次不处理，另行排期）

1. ~~黄金集 line_items 判据恒 0~~ → **已修**（见 §5.5）。
2. **`api_def.response_schema` 激活时不回写**（§0.5）—— 本次用「同源取 active
   版本」绕开了，但两处 schema 长期不一致本身是隐患，值得在版本激活流程里补回写。
3. **JP 模板缺失** —— 仓库根没有 `JP_invoice_prompt.yaml`，导致 JP 黄金集与
   2 条 bench 用例无法运行。

## 6. 关联

- `backend/eval_samples/schema_ab/` —— 三样本 + A/B 脚本 + Phase 0 结果
- `prompts/tpl7/CHANGELOG.md` —— 缺陷的发现过程
- CLAUDE.md §3.3「Prompt 四段平台契约」/ 自检 5「改机器先黄金集 A/B」
