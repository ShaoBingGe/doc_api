# template 7 prompt 迭代记录

样本：`July Claim 20260826`（25 页马来西亚报销贴单）
模型：`gemini-3.5-flash` · thinking_level=low · temperature=0 · seed=42

| 版本 | 改动 | 得分 | 轮次 |
|---|---|---|---|
| v1 | 线上基线 | 5/8 → 抖动 | 单轮 |
| v2 | 六条规则一次性补齐（A 刷卡票判 receipt / B 抬头 / C RefNo / D MYR 优先 / E 国家 / F unitPrice） | **7/8** | [7,7,7] |
| v3 | 删 E（国家推导，见下）；收紧 C 的适用范围 | 6.7/7 | [6,7,7] |
| v4 | 新增 A-2 反向边界（银行卡交易行 / 对账单不算 receipt） | **7/7** | [7,7,7,7] |
| **v5** | 新增 A-1（手写现金收据本判 receipt） | **8/8** | **[8,8,8,8]** |

---

## 逐轮说明

### v1 → v2：一次补齐六条，7/8

对比报告列的 6 个问题一次性写成补丁段。结果只剩国家字段没动（0/23）。

**方法上的关键发现**：v1 基线连跑两次结果不同——`nameOfInvoice` 一次
0/25 全空、一次 18/23 有值。**单轮评测分不清「prompt 改好了」和「模型这次
手气好」**，此后所有版本固定跑 3–4 轮取通过率。

同时把 `temperature=0` / `seed=42` / `top_p=1` 写进 `.env`（此前 temperature
虽由 pydantic 默认兜成 0，但没有 seed）。v2 起三轮结果完全一致，抖动消失。

### v2 → v3：撤掉国家推导（**产品决策，非技术失败**）

`billFromCountry` 0/23 的根因不是规则没写清，而是与 v1 正文冲突：

> §1.4 / Part 3 §3.7：**缺失信息**：无法找到的字段一律不输出，
> **不要推断、不要捏造**

从地址反推国家属于推断。**经确认尊重该原则，国家字段不再作为需求**，
判据从 8 条降为 7 条。

同轮修了 v2 的一个副作用：规则 C「Reference No 优先」被推广到了所有小票，
导致星巴克那张有明确 `Invoice No: SB398R1-0002771` 的票被改取了 Ref No。
收紧为「票面有明确单号标注时优先取它，全部缺失才取 Reference No」。

### v3 → v4：补反向边界，修 K1 回退

v3 第 1 轮把第 1 页判成了 `receipt`——规则 A 放宽 receipt 判定后，
第 1 页那条银行卡交易记录（`SB398-SHELL … -RM 100.00`）被"刷卡凭条"
特征命中。

新增 A-2：**银行卡账单里的单条交易记录、对账单流水表一律判 `other`**。
判据是「一张真正的 receipt 必然具备：商户抬头 + 至少一行消费明细 +
一个合计金额，三者缺一就不是」。K1 恢复 4/4。

### v4 → v5：修 A-2 带来的过度保守

A-2 让模型对 receipt 收得太紧，把两张手写 `CASH BILL` 现金收据
（第 4 页 `10206`、第 10 页 `6053`）推成了 `invoice`——v2 时它们本来是
`receipt`。

新增 A-1：**手写/预印现金收据本判 receipt**（预印表格 + 手写内容 +
`Terima Oleh / Received By` 签收栏 + 无税号无买方）。
同时新增判据 P6 盯住这一点，防止将来再被改回去。

---

## v5 最终识别结果（25 条）

`docType` 分布：**receipt 18 / invoice 5 / other 2**
（v1 是 invoice 19 / receipt 4 / other 2，方向完全反转）

判为 `invoice` 的 5 张均有税号或正式抬头，经核对无误：

| 页 | 抬头 | 单号 | 依据 |
|---|---|---|---|
| 5 | INVOICE | C0005781 | U&ME HOTEL，有 SST Reg. No |
| 8 | TAX INVOICE | 9001647 | LINDEN KOPI，抬头即 TAX INVOICE |
| 9 | INVOICE | MJSG001/119084 | — |
| 10 | INVOICE | ST001/373250 | — |
| 12 | Invoice | 1-00028092 | — |

加油小票单号全部 6 位、无重号、无 8 位 Terminal 号
（v1 曾出现 `84202913` 在第 2、3 页重复）。

---

## 已知遗留（不在本次范围）

1. **第 13–25 页（Touch'nGo 对账单）判 `other`** —— 这是**正确行为**，
   已由 K2 锁定。若将来需要把对账单也结构化，那是新增文档类型的需求，
   要改 schema，不是改 prompt。
2. **字符级 OCR 噪声**依然存在（小票为传真质量点阵打印）。
   本轮未针对此优化——prompt 改不动 OCR 的字符识别精度。
3. **补丁只存在于 tpl7 的 prompt 快照里**，未合并回
   `MY_invoice_prompt.yaml`。若跑
   `seed_open_api.refresh_prompt_from_country_template`，补丁会被国家模板
   覆盖掉。要长期保留必须合并进国家模板。

---

## v6：合并进国家模板（`MY_invoice_prompt.yaml` id 7_8 → 7_9）

v5 验证达标后，把五轮迭代的规则**按章节合并进国家模板**（不再是贴在 prompt
尾部的"专项修正"段）：

| v5 补丁段 | 合并到的位置 |
|---|---|
| A 刷卡/POS 小票判 receipt | §1.1 receipt 分类 |
| A-1 手写现金收据本判 receipt | §1.1 receipt 分类 |
| A-2 银行卡交易行/对账单判 other | §1.0.5 附件与非票据页 |
| B nameOfInvoice 票面抬头 | schema `nameOfInvoice` description |
| C 加油票单号取 Reference No | §1.3 发票号 + schema `invoiceNumber` |
| D 多币种优先 MYR | §1.2 货币 + schema `totalAmount` / `currency` |
| F unitPrice 票面有则必填 | schema `unitPrice` description |

v6 走真实装配管线（`template_loader.decompose_country_template("MY")` +
`build_v1_prompt`）生成，与直接改快照不同——**这是 refresh 之后线上实际会
得到的 prompt**，不存在"被国家模板覆盖回去"的问题。

### 评测

- claim25（25 页报销贴单）：**8/8 × 4 轮全过**，与 v5 持平
- 6 页多票据样本（DOC_07_15_25006）回归：**6 票据 / 10 明细 / 零空值 × 2 轮**
  —— 新分类规则未破坏既有的切分与提取行为

### 影响面提示

国家模板是 **MY 层**改动：影响所有从 MY 模板初始化/刷新的 API，不只
templateId=7。分类规则在 gemini-3.5-flash 上验证；阿里云跑 qwen 的 MY API
吃到同一模板后行为**未单独验证**。
