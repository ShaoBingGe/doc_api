"""把原版 MY.yaml 压成精简版 —— 只改文案，结构由构造保证不变。

做法：加载原 YAML → 整段替换 prompt_format → 按路径替换 schema 的 description
→ **断言**结构骨架（properties 键集 / type / enum / required / 嵌套形状）与原版
完全一致，任何结构漂移直接 raise。

压缩依据（只砍这四类，不砍有判别力的规则）:
  1. 死重      —— 教正则引擎怎么写 `\\s*` 的整段（VLM 不跑正则）
  2. 死分支    —— 越南盾/欧洲千分位、Záloha/Ký hiệu 等非马来西亚语境
  3. 重复      —— 同一规则在锚点段、提取规则段、schema description 三处重述
  4. 超长示例  —— 单字段 6~8 个格式示例收敛到 2~3 个代表
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

import yaml

SRC = sys.argv[1] if len(sys.argv) > 1 else "/Users/shaobin/Downloads/MY.yaml"
DST = sys.argv[2] if len(sys.argv) > 2 else str(
    pathlib.Path(__file__).parent / "MY_slim.yaml")

# ── 新的 prompt_format ───────────────────────────────────────────────────────

PROMPT = """\
**Task:** Extract every invoice/document in the file into JSON. One page may hold \
several invoices; a single invoice may span several pages — merge multi-page \
invoices into ONE record. Scans may be skewed or low quality; read as accurately \
as possible. Keep the document's original language; do not translate.

**Document Classification (docType):**
- `invoice`: formal payment voucher — has supplier + customer, invoice number, date, \
tax info, goods/services detail, total. Standard invoice, proforma invoice, credit \
note and debit note ALL count as `invoice`, whatever the title says.
- `receipt`: small-value payment voucher, usually no detailed tax info (retail, \
catering, transport, tickets).
- `other`: only documents that are genuinely neither of the above (contracts, order \
confirmations, …). **Safety rule: if the page shows a seller, a buyer and an amount \
payable, it is an `invoice` (or `receipt`) — never classify such a page as `other`.** \
An unusual layout, a missing invoice number or a title you do not recognise are NOT \
reasons to fall back to `other`.

**Malaysia (MY) Anchors** — apply when the document is clearly Malaysian (address \
in Malaysia, currency MYR/RM, Malay text). Do NOT apply to documents explicitly \
marked with another country or currency.
- **Supplier vs buyer**: the party carrying TIN / SST No / BRN / bank details is the \
supplier (billFrom). The party after "Bill To / Customer / MESSRS / Sold To / \
Attention" is the buyer (billTo).
- **TIN** = `C` + 10~11 digits (e.g. C5884056070). Buyer TIN → \
billToTaxIdentificationNumber; supplier TIN → billFromTaxIdentificationNumber. \
Never put SST No or BRN into a TIN field.
- **BRN / SSM** (company registration): 12 digits (199201000276), old form \
`518287-T`, or both together `199701009372 (424868-V)`. **Copy the full original \
text verbatim, including the parentheses and everything inside — never truncate to \
just one part, and never split the two halves across two fields.** A registration \
number never carries a `C` prefix: do not add one and do not re-file it as a TIN. \
Take the supplier's only → billFromBusinessRegistrationNumber.
- **SST No** = `B16-xxxx-xxxxxxxx` / `W10-xxxx-xxxxxxxx`. This template has NO SST \
field — if seen, discard it. Never force it into TIN / BRN / invoiceNumber.
- **invoiceNumber**: near the top; e.g. PT-148862, INV-117055, 25FC011847. Exclude \
SST No, TIN, Cert. No., System Ref No.

**Formatting:**
- **Amounts** → plain numbers, no thousands separators (output `1234567.89`). First \
decide the separator convention: MYR/USD/CNY/SGD/GBP use `,` for thousands and `.` \
for decimals (`1,234,567.89` → 1234567.89). If the document clearly uses the \
opposite convention (`.` thousands, `,` decimals — e.g. VND and some European \
currencies), read it that way (`1.234.567,89` → 1234567.89, `6.000` → 6000).
- **Dates** → output `YYYY-MM-DD`. This rule is absolute. Malaysia writes dates \
**day first**, so for ANY all-numeric date — two- or four-digit year alike \
(`12-02-26`, `12/02/2026`, `07-09-24`) — read it as **DD-MM-YY(YY) first** and accept \
that reading when day ≤ 31, month ≤ 12 and year ≥ 2015. Never silently swap day and \
month: `12/02/2026` is 12 February 2026, not 2 December. Only if the day-first \
reading is impossible, fall back to YY-MM-DD, then MM-DD-YY, taking the first valid \
reading with year ≥ 2015 (`32-07-26`: day=32 invalid → YY-MM-DD → 2032-07-26). The \
year **must be ≥ 2015**; any reading yielding an earlier year is wrong. A date already \
written unambiguously (`12 Feb 2026`) is simply reformatted, never re-ordered.
- **Tax rate** → percentage string, e.g. "10%".
- **Line quantity** → `unitPrice × quantity = netAmount` (tolerance 0.01). If the \
document offers several candidate quantity columns, pick the one that satisfies the \
equation; if there is only one, use it.

**Required output:**
- `invoice`/`receipt`: docType, invoiceType, page, currency must always be present \
(infer currency from context if unprinted).
- `other`: only docType and page.
- **Five downstream-critical fields — search hard, never fabricate:**
  1. `invoiceNumber` — sweep the whole document (header, footer, tables, stamps, \
remarks) under all aliases (Invoice No / No. / INVOICE NO / E INV NO). Only output a \
number that really appears; never substitute a PO / DO / SST / customer number. \
Leave empty if genuinely absent.
  2. `invoiceDate` — look for Invoice Date / Date / Tarikh; a lone date on the \
document is usually it. Never guess; leave empty if genuinely absent.
  3. `invoiceType` — if undeterminable, default to `Commercial Invoice`.
  4. `totalAmount` — prefer the printed Total / Grand Total; if there is no total \
line, sum the tax-inclusive line amounts. Never invent; default 0 only if it truly \
cannot be derived.
  5. `currency` — prefer the printed marking, else infer from country/symbol/ \
language; if still undeterminable default to `MYR`.
  **Multi-currency documents**: currency, totalAmount and line amounts must all \
belong to the same currency system. In that case ignore the MY currency default and \
follow what the document actually prints.
  Before emitting, re-check these five for oversight — but fill gaps with the \
agreed fallbacks above, never with invented data.
- **Whatever IS printed must be extracted, even when the field is not in the required \
list.** Omitting a value that is visible on the page counts as a miss, exactly like \
getting it wrong. In particular: when a subtotal / net line or a tax line is printed — \
or can be summed from the line items — output `totalNetAmount` and `totalTaxAmount`; \
when a company registration number (BRN / SSM / Company No.) or a TIN is printed \
beside the supplier, output `billFromBusinessRegistrationNumber` / \
`billFromTaxIdentificationNumber`. Never leave such a field out just because it is \
optional. **Zero is a value, not an absence**: on a zero-rated, exempt or tax-free \
invoice output `totalTaxAmount: 0` (and `tax: 0` on the lines) — do not omit the \
field just because the tax happens to be nil.
- Any other field genuinely absent from the document: leave the key out. Do not \
infer, and never write placeholder text such as "Not found".

**Taxes:**
- `detailOfTaxSummary.taxCategory` must use these standard abbreviations (if the tax \
you identify is not listed, use its name as printed): {tax_categories_text}
- Extract **every** tax type present — one invoice may carry several (e.g. VAT plus \
withholding tax). Distinguish additive taxes (VAT/SST) from subtractive ones (WHT).

**Anti-false-extraction (invoiceNumber, PO, DO, TIN, BRN):**
1. **PO/DO placement is mutually exclusive**: they appear either in the header or in \
the line items, not both. Look in the header first; only if absent, look in the rows.
2. **Never cross lines or columns.** A value must sit on the same line as its label, \
or on the line immediately below it. Never absorb text belonging to a neighbouring \
column or the next row — e.g. `P/O No. :` followed by a line break must not yield \
"Qty Unit", and `DO-2604/0012` on one line must not become "DO-2604/0012 MENARA".
3. **Reject non-values.** A document number can never be a column heading, a form \
label, an address fragment or a place name (e.g. QTY, UNIT, PRICE, AMOUNT, TOTAL, \
DESCRIPTION, PRODUCT, CODE, ITEM, DATE, TERMS, PAYMENT, DELIVERY, CUSTOMER, INVOICE, \
REF, NO., PAGE, TEL, FAX, ATTN, BANK, ACCOUNT, REMARK, SIGNATURE, PCS, KG, BAGS, \
PALLET, TRIP, LORRY, JALAN, MENARA, KUALA LUMPUR, SELANGOR, PENANG, JOHOR, MALAYSIA). \
Also reject strings of length ≤ 2 and anything starting with `:`.
4. Extract only values that carry an explicit label — do not guess.
5. Keep the printed format; do not reformat or alter digits.
6. Multiple DO / PO numbers → one field, separated by semicolons \
(e.g. `SH34756; SH34757`).
7. Keep supplier and buyer information strictly apart.
"""

# ── schema description 替换（键 = 字段路径）─────────────────────────────────

D = {
    "page": "Page numbers holding this document (1-based). A multi-page invoice "
            "lists all its pages in one array, e.g. [2, 3, 4].",
    "invoiceType":
        "Invoice type:\n"
        "  - 'Proforma Invoice': contains PROFORMA / PRO FORMA / Prepayment.\n"
        "  - 'Credit Note': contains CREDIT / AVOIR, or totalAmount < 0.\n"
        "  - 'Tax Invoice': contains 'Tax Invoice'.\n"
        "  - 'Commercial Invoice': the default when none of the above matches.\n"
        "  (A REFUND note maps to Credit Note; a DEBIT note stays Commercial Invoice.)",
    "invoiceNumber":
        "Invoice or receipt number. Labels: 'Invoice No', 'No.', 'INVOICE NO:', "
        "'E INV NO'; usually at the top. Examples: PT-148862, S-25/10-0059, "
        "IN2601/1024, 25FC011847. Do NOT take Sales Tax No / SST ID / Cert. No. / "
        "System Ref No. / Return No.",
    "invoiceCode":
        "Invoice type code or serial ('Serial'). Omit if the document has none.",
    "invoiceDate":
        "Issue date (downstream-critical). Labels: 'Invoice Date', 'Date', 'Tarikh'. "
        "A lone date is usually the invoice date; if several dates appear (due date, "
        "shipping date), take the issue/invoice one. All-numeric dates are day-first "
        "(DD-MM-YYYY) — do not swap day and month. Never fabricate. Output "
        "YYYY-MM-DD.",
    "totalNetAmount":
        "Invoice-wide net amount excluding tax — the sum of ALL line net amounts. "
        "Output it whenever a subtotal/net line is printed or the lines can be "
        "summed. If the document has several parts (e.g. principal plus commission), "
        "sum them all; do not report only one part.",
    "totalAmount":
        "Total including tax (downstream-critical). Labels: 'Total', 'Grand Total', "
        "'Total Due', 'Amount Payable'. With no total line, sum the tax-inclusive "
        "line amounts. Never invent; default 0 only if truly underivable.",
    "totalTaxAmount":
        "Total tax — always output it, including `0` when the invoice is zero-rated, "
        "exempt or simply carries no tax. Omitting the field is wrong; `0` is right. "
        "(1) Prefer values explicitly labelled Tax / VAT / GST / SST / IVA. (2) With "
        "a multi-row tax summary table, sum the per-type amounts — not the table's "
        "own Total/Subtotal row. (3) Never derive tax by subtracting other figures "
        "from the total; it must carry a tax label.",
    "currency":
        "ISO 4217 code (MYR, USD, CNY, SGD, GBP …). Default 'MYR' when unmarked and "
        "uninferable.",
    "billToName":
        "Buyer name. If there is no 'Bill To', look for MESSRS / Purchaser / "
        "Customer / Buyer / Attention to.",
    "billToComposite": "Buyer's full address block.",
    "billToCountry": "Buyer's country, taken from the address.",
    "billToCountryCode": "Buyer's ISO 3166-1 alpha-2 country code (MY, SG, CN …).",
    "billToTaxIdentificationNumber":
        "Buyer's TIN. Labels: 'TIN', 'TIN No.', 'Buyer TIN', 'Customer Tin No.'. "
        "Format `C` + 10~11 digits (e.g. C5884056070). Take the BUYER's when both "
        "parties show a TIN. Not the same as an SST ID (B16-/W10- format).",
    "billFromName":
        "Supplier/issuer name. With no 'From' block, use Account Name / Beneficiary "
        "Name / Seller / Remit to, the bottom signature, or the merchant name on a "
        "receipt.",
    "billFromComposite": "Supplier's full address block.",
    "billFromCountry": "Supplier's country, taken from the address.",
    "billFromCountryCode": "Supplier's ISO 3166-1 alpha-2 country code.",
    "billFromTaxIdentificationNumber":
        "Supplier's tax identification number (TIN, `C` + 10~11 digits); also "
        "'VAT Registration' / 'TAX#'.",
    # BRN 实测：无论用压缩描述还是把原版描述整段还原，精简版都稳定在 74%，
    # 而原版 3 轮为 77.8/80.8/81.5%。→ 缺口不来自本字段的措辞，而来自原版把
    # BRN 重复提及三处所形成的「显著性强化」。既然还原描述也修不回来，就不
    # 为它多付 60 token；该缺口作为已知代价记录在案。
    "billFromBusinessRegistrationNumber":
        "Supplier's company registration / SSM / BRN number, printed beside the "
        "supplier name, usually at the top. Labels: 'Company Reg.No', 'Co. Reg. No.', "
        "'Registration No', 'SSM', 'BRN', 'Company No.'. Formats: plain 12 digits "
        "(199201000276, 200201024195), old style (518287-T, 009532-K), or **new and "
        "old together, with or without a space before the bracket** — "
        "199701009372 (424868-V), 202101033325(1433625-W). When both numbers are "
        "printed, return the **whole string exactly as shown, brackets included** — "
        "returning only `1433625-W` or only the 12-digit half is wrong. Take the "
        "SUPPLIER's, not the buyer's.",
    "purchaseOrderNumber":
        "Invoice-level purchase order number — the number the BUYER issued, from the "
        "customer block at the top. Labels: 'P/O No.', 'Your PO No.', 'Your P.O. No', "
        "'PO No', 'PO #', 'Order No', \"Buyer's Order No\", 'Customer PO No.', "
        "'Customer Ref', 'YOUR REFERENCE', 'REFER YOUR P/O NO.'. **Shape: a letter "
        "prefix (W1 / K2 / N1 / P …) followed by digits, frequently with a space** — "
        "W1 537443, K2 513875, W1 525065, N1 547225, W1 529109; occasionally plain "
        "digits (90001110, 0052719). Do not substitute the supplier's own internal "
        "reference (e.g. TYS/RIA/PO/056) for the buyer's PO. Avoid 'Delivery Order "
        "No' and 'Sales Order No'. If the PO belongs to one line only, put it on that "
        "line instead.",
    "dueDate": "Payment due date.",
    "salesOrderNumber":
        "Invoice-level sales order number (SO No / S/O No / SO#). If it belongs to "
        "one line only, put it on that line instead.",
    "deliveryOrderNumber":
        "Invoice-level delivery order number, from the header block. Labels: "
        "'D/O No.', 'DO NO', 'DELIVERY ORDER NO.', 'External Document No'. Examples: "
        "E-DO00863, DO2601/0543, SH34756. Several DO numbers may appear — list them "
        "all in this one field, semicolon-separated. If a DO belongs to one line "
        "only, put it on that line instead.",
    "detailOfGoodsOrServices":
        "Line items of goods or services.\n"
        "**Three strong rules:**\n"
        "1. Each line should satisfy |quantity × unitPrice − netAmount| / "
        "max(|netAmount|, 1) < 0.01. If it cannot, output the document's netAmount "
        "as printed — do not bend quantity or unitPrice to make it fit.\n"
        "2. Add-on lines written as \"+item amount\" (e.g. \"+ Wanton Mee 1.00\") "
        "merge into the description of the main line above; never emit them as "
        "separate rows.\n"
        "3. If netAmount is not printed, compute quantity × unitPrice — do not leave "
        "it empty.",
    "articleName": "Goods or service name (complete).",
    "description": "Remarks.",
    "quantity": "Quantity.",
    "unitOfMeasure": "Unit of measure (pcs, kg, m, hour …).",
    "unitPrice": "Unit price.",
    "netAmount": "Amount excluding tax.",
    "tax": "Tax amount.",
    "grossAmount": "Amount including tax.",
    "orderNumber": "Order number.",
    "detailOfTaxSummary":
        "Tax summary rows. The same tax category at different rates must be listed "
        "as separate rows.",
    "taxCategory":
        "Standard tax abbreviation from the list given in the prompt (VAT, SST, WHT "
        "…). If the tax is not in that list, use its printed name.",
    "netTaxableAmount": "Tax base (taxable amount, excluding tax).",
    "originalInvoiceReferences":
        "Original invoice references. Populate only when invoiceType is 'Credit "
        "Note'; otherwise keep the field but leave it empty. One element per "
        "referenced invoice.",
    "originalInvoiceCode": "Original invoice code / serial; omit if absent.",
    "originalInvoiceNumber": "Original invoice number.",
    "originalInvoiceDate": "Original invoice date, YYYY-MM-DD.",
}
# 出现两次的完全相同描述
TAXRATE = ('Tax rate as `number%` (e.g. "0%", "10%", "10.5%"). Exempt / zero-rated / '
           'tax-free / 免税 / 非课税 → "0%". Anything not expressible as a valid '
           'percentage (N/A, prose, long digit strings) → "".')
# 行内单号：与表头同名字段区分开
D_LINE = {
    "deliveryOrderNumber":
        "DO number belonging to THIS line only (if it covers the whole invoice, put "
        "it in the header field instead). Often date+DO+number, e.g. \"25/02/2025 DO "
        "127235\". Examples: KT00367825, DO-K.157321, DO-2604/0012.",
    "purchaseOrderNumber":
        "PO number belonging to THIS line only (if it covers the whole invoice, put "
        "it in the header field instead). Letter prefix plus digits, sometimes "
        "spaced: W1525987, W1 543625, K2 513875.",
    "salesOrderNumber":
        "SO number belonging to THIS line only (if it covers the whole invoice, put "
        "it in the header field instead).",
}


# ── 应用 + 结构断言 ─────────────────────────────────────────────────────────

def skeleton(node):
    """抽出结构骨架：忽略 description，保留键集/type/enum/required/嵌套形状。"""
    if isinstance(node, dict):
        return {k: skeleton(v) for k, v in sorted(node.items()) if k != "description"}
    if isinstance(node, list):
        return [skeleton(x) for x in node]
    return node


def walk(node, in_line_items=False):
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            table = D_LINE if (in_line_items and name in D_LINE) else D
            if name == "taxRate":
                sub["description"] = TAXRATE
            elif name in table:
                sub["description"] = table[name]
            walk(sub, in_line_items or name == "detailOfGoodsOrServices")
    if isinstance(node.get("items"), dict):
        walk(node["items"], in_line_items)
    for br in node.get("anyOf") or []:
        walk(br, in_line_items)


def main() -> None:
    orig = yaml.safe_load(pathlib.Path(SRC).read_text())
    slim = copy.deepcopy(orig)

    slim["prompt_template"]["prompt_format"] = PROMPT
    slim["remark"] = ("Slim variant of #101 — same structure/required/enums, "
                      "compressed wording only.")
    sch = slim["prompt_template"]["json_schema"]
    walk(sch)

    # 结构必须逐字节相同，否则拒绝产出
    a = json.dumps(skeleton(orig["prompt_template"]["json_schema"]), sort_keys=True)
    b = json.dumps(skeleton(sch), sort_keys=True)
    if a != b:
        raise SystemExit("结构漂移！精简版被拒绝。")

    pathlib.Path(DST).write_text(
        yaml.safe_dump(slim, allow_unicode=True, sort_keys=False, width=100))

    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")

    def tk(v):
        return len(enc.encode(v if isinstance(v, str)
                              else json.dumps(v, ensure_ascii=False)))

    op, os_ = orig["prompt_template"]["prompt_format"], orig["prompt_template"]["json_schema"]
    np_, ns = slim["prompt_template"]["prompt_format"], sch
    print("结构断言通过：properties / type / enum / required / 嵌套形状 完全一致\n")
    print(f"{'':16s}{'原版':>10}{'精简':>10}{'降幅':>10}")
    for label, o, n in [("prompt_format", op, np_), ("json_schema", os_, ns)]:
        print(f"{label:16s}{tk(o):>10}{tk(n):>10}{1-tk(n)/tk(o):>9.1%}")
    to, tn = tk(op) + tk(os_), tk(np_) + tk(ns)
    print(f"{'合计':16s}{to:>10}{tn:>10}{1-tn/to:>9.1%}")
    print(f"\n写入 {DST}")


if __name__ == "__main__":
    main()
