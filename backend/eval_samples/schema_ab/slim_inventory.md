# R0 · MY 模板瘦身迁移清单（7_13 基线）

> 产出于 `docs/plan-my-template-slimming.md` R0 ｜ 2026-08-28
> 零模型调用，纯静态比对。基线数值见同目录 `slim_baseline.json`。

## 0. 基线尺寸

| | 字符 |
|---|---|
| 正文 `prompt_format` | 13,998 |
| schema description 合计（含明细子字段） | 7,130 |
| 组装后 prompt（`build_v1_prompt`） | 18,923 |

## 1. 逐段迁移判定

| 段 | 字符 | 判定 | 依据 |
|---|---|---|---|
| §1.0 切分（1.0.1–1.0.7） | 1,898 | **留** | 文档级判断，无字段可承载 |
| §1.1 分类 + §1.1.1 报价单 | 2,625 | **留** | 跨字段合议推理 |
| §1.2 语言/货币/日期 | 1,024 | **半迁** | 日期格式细则→`invoiceDate`；多币种 MYR 留正文 |
| §1.3 税号/单号 | 2,150 | **迁（需合并）** | 见 §2 —— description 是**摘要+外链**，不是完整拷贝 |
| §1.34 抬头与单价 | 507 | **纯删** | 与 description 逐句同款，见 §3 |
| §1.35 行字段归属 | 638 | **纯删** | 与明细 description 逐句同款，见 §3 |
| §1.36 马来语 | 3,416 | **半迁** | 1.36.1 标题裁决留；1.36.2/3/4 字段标签→各字段 |
| §1.4 缺失纪律 | 1,468 | **留** | 全局输出纪律 |

## 2. ⚠️ 阻塞项：description 里有 8 处指向正文的交叉引用

删正文会让这些引用**悬空**（模型被指向一个不存在的章节）。迁移前必须逐条处置：

| description 字段 | 引用 | 处置 |
|---|---|---|
| `invoiceNumber` | 见 Part 1.3 | **R3 必须先内联**：正文有完整的加油票取值优先级（Bill No/Receipt No/Resit No/R.No/Master Bill/Booking No → Reference No → Approval Code）与 **8 项禁取清单**（Terminal / Site Id / Site Tran No / Batch No / Card No / Seq. Number / App ID / App Crypt / CDB Ref No），description 只写了「…见 Part 1.3」 |
| `docType` | 见 Part 1.1.1 | 保留 —— §1.1.1 留正文，引用有效 |
| `invoiceDate` | 见 Part 1.36.3 | R5 迁月份表时同步内联 |
| `totalAmount` / `currency` | 见 Part 1.2 | 保留 —— 多币种规则留正文 |
| `totalTaxAmount` | 见 Part 1.4 | 保留 —— §1.4 留正文 |
| `detail[].quantity` | 见 Part 3.4 | 保留 —— Part 3 是平台契约，非国别正文 |
| `detailOfTaxSummary` | 见 Part 3.3 | 保留 —— 同上 |

**规则：迁走某段前，先把指向它的 description 引用改成内联内容；引用指向"留"的段落则不动。**

## 3. 已确认可纯删的两段（静态比对结论）

用「正文反引号实词是否出现在目标 description 中」做覆盖检查：

- **§1.34**：19 个实词，未覆盖 5 个 —— 逐一核对后确认**全部是正则跨行切碎的假阳性**
  （如 `后的数字，或`、`等于该金额。`），无实质缺失。→ **纯删除**
- **§1.35**：11 个实词，未覆盖 1 个 = `detailOfGoodsOrServices`（字段名本身）。→ **纯删除**
- §1.3：59 个实词，未覆盖 30 个，其中**含真实内容**（8 项禁取清单、取值优先级、
  括号旧号混淆对）→ 不可纯删，必须先合并进 description。

## 4. R2 起的执行顺序（按风险从低到高）

1. **R2 · §1.34**（507，纯删，无交叉引用指向它）
2. **R3 · §1.3**（2,150，先内联 `invoiceNumber` 引用 + 合并禁取清单，再删正文）
3. **R4 · §1.35 + §1.36.2/3/4**（638 + ~2,584）
4. **R5 · §1.2 半迁 + 收口**

## 5. 每轮验证判据（固定）

- 三样本 `phase2_verify.py --rounds 3` 全过（18 判据）；
- 黄金集 ≥2 seed `--schema auto`，strict overall 不降；
- **qwen 抽验**（R1 通道就绪后）：S1 + S3 各 1 轮，迁走的字段不得丢失；
- 输入 token 对照 `slim_baseline.json` 记录降幅。
