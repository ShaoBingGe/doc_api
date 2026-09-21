"""UBL 2.1（MyInvois JSON / XML）→ 平台标准 29 字段结构。

MyInvois 的 JSON 走一种特殊的「每层都是数组、标量挂在 `_` 键下」的编码：

    {"Invoice": [{"ID": [{"_": "INV-001"}],
                  "IssueDate": [{"_": "2026-01-15"}], ...}]}

XML 变体先由 `_xml_to_ubl_dict` 归一成同一形状，之后共用一套映射逻辑。

**只映射税局确实保存的字段**；票面上有但 UBL 不承载的（票面标题、PO/DO/SO、
付款条款等）一律留空，交给 fusion 保留 OCR 结果。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# ── MyInvois 代码表 ──────────────────────────────────────────────────────────
# e-Invoice 类型码 → 平台 invoiceType 枚举。
# 注意：01 既可能是普通商业发票也可能是税务发票，UBL 无从区分我们枚举里的
# 'Commercial Invoice' / 'Tax Invoice'，故 01/03 **不覆盖** OCR 的判断
# （值为 None = 交给 fusion 保留 OCR）。02/04 语义明确，覆盖。
INVOICE_TYPE_CODES: dict[str, str | None] = {
    "01": None,             # Invoice          → 保留 OCR（Commercial / Tax 由票面定）
    "02": "Credit Note",    # Credit Note
    "03": None,             # Debit Note       → 我们枚举无此项，保留 OCR
    "04": "Credit Note",    # Refund Note      → 语义等同贷项
    "11": None,             # Self-billed Invoice
    "12": "Credit Note",    # Self-billed CN
    "13": None,             # Self-billed DN
    "14": "Credit Note",    # Self-billed Refund
}

# 税种码 → 平台标准简称（见国家模板 Part 1.3）
TAX_TYPE_CODES: dict[str, str] = {
    "01": "SST",       # Sales Tax
    "02": "SST",       # Service Tax
    "03": "TTX",       # Tourism Tax
    "04": "HVGT",      # High-Value Goods Tax
    "05": "LVG",       # Sales Tax on Low Value Goods
    "06": "NA",        # Not Applicable
    "E": "Exempt",     # Tax exemption
}

# MyInvois 用 ISO 3166-1 alpha-3；平台 schema 要 alpha-2。
_ALPHA3_TO_2 = {
    "MYS": "MY", "SGP": "SG", "CHN": "CN", "USA": "US", "JPN": "JP",
    "GBR": "GB", "DEU": "DE", "THA": "TH", "IDN": "ID", "VNM": "VN",
    "HKG": "HK", "TWN": "TW", "IND": "IN", "AUS": "AU", "KOR": "KR",
    "PHL": "PH", "BRN": "BN", "NLD": "NL", "FRA": "FR", "CHE": "CH",
}
_COUNTRY_NAME = {"MY": "Malaysia", "SG": "Singapore", "CN": "China"}


# ── 通用取值助手（吃 MyInvois 的数组/`_` 编码）────────────────────────────────
def _node(obj: Any, key: str) -> Any:
    """取 obj[key] 的第一个元素（MyInvois 每层都包了一层数组）。"""
    if not isinstance(obj, dict):
        return None
    v = obj.get(key)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _nodes(obj: Any, key: str) -> list:
    if not isinstance(obj, dict):
        return []
    v = obj.get(key)
    if isinstance(v, list):
        return v
    return [v] if v is not None else []


def _val(obj: Any, *keys: str) -> Any:
    """沿 keys 下钻并取标量（叶子挂在 `_`；也容忍直接是标量）。"""
    cur = obj
    for k in keys:
        cur = _node(cur, k)
        if cur is None:
            return None
    if isinstance(cur, dict):
        return cur.get("_")
    return cur


def _text(obj: Any, *keys: str) -> str | None:
    v = _val(obj, *keys)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(obj: Any, *keys: str) -> float | None:
    v = _val(obj, *keys)
    return _to_num(v)


def _fx(inv: dict) -> dict | None:
    """读 UBL 自带的汇率块（preprod 实测存在）。

        <cac:TaxExchangeRate>
          <cbc:SourceCurrencyCode>USD</cbc:SourceCurrencyCode>
          <cbc:TargetCurrencyCode>MYR</cbc:TargetCurrencyCode>
          <cbc:CalculationRate>1.66</cbc:CalculationRate>
          <cbc:MathematicOperatorCode>Multiply</cbc:MathematicOperatorCode>
        </cac:TaxExchangeRate>
    """
    for key in ("TaxExchangeRate", "PricingExchangeRate", "PaymentExchangeRate"):
        node = _node(inv, key)
        if not isinstance(node, dict):
            continue
        rate = _to_num(_val(node, "CalculationRate"))
        src = (_text(node, "SourceCurrencyCode") or "").upper()
        tgt = (_text(node, "TargetCurrencyCode") or "").upper()
        if rate and rate > 0 and src and tgt:
            op = (_text(node, "MathematicOperatorCode") or "Multiply").strip().lower()
            return {"source": src, "target": tgt, "rate": rate, "op": op}
    return None


def _convert(value: float, from_cur: str, to_cur: str, fx: dict | None) -> float | None:
    """用 UBL 自带汇率把金额换算到 to_cur；换不了返回 None。"""
    if fx is None or from_cur == to_cur:
        return value if from_cur == to_cur else None
    src, tgt, rate = fx["source"], fx["target"], fx["rate"]
    multiply_fwd = fx["op"] != "divide"          # Multiply: target = source × rate
    if from_cur == src and to_cur == tgt:
        out = value * rate if multiply_fwd else value / rate
    elif from_cur == tgt and to_cur == src:
        out = value / rate if multiply_fwd else value * rate
    else:
        return None
    return round(out, 2)                          # 货币精度


def _amount(
    obj: Any,
    key: str,
    doc_cur: str | None,
    fx: dict | None = None,
    converted: list[str] | None = None,
) -> float | None:
    """取金额并**归一到单据币种**。

    preprod 实测：MyInvois 要求税额一律按 **MYR** 申报，而单据本身可为外币，
    于是同一张 USD 发票里会出现

        <cbc:TaxExclusiveAmount currencyID="USD">12.85</>   ← 单据币种
        <cbc:TaxAmount          currencyID="MYR">1.71</>    ← 税额固定 MYR

    直接写入会导致 12.85 + 1.71 ≠ 13.88。但税局数据应当优先，所以不是丢弃，
    而是用 UBL 自带的 TaxExchangeRate 折回单据币种：1.71 ÷ 1.66 = 1.03
    —— 与票面 USD 税额完全一致。换算过的字段会记进 `_myinvois.converted`
    以便审计；**实在换不了**（无汇率块）才返回 None 让 OCR 兜底。
    """
    n = _node(obj, key)
    if n is None:
        return None
    if not isinstance(n, dict):
        return _to_num(n)
    raw_val = _to_num(n.get("_"))
    if raw_val is None:
        return None
    cur = str(n.get("currencyID") or "").upper().strip()
    if not cur or not doc_cur or cur == doc_cur:
        return raw_val
    out = _convert(raw_val, cur, doc_cur, fx)
    if out is not None and converted is not None:
        converted.append(f"{key}: {raw_val} {cur} → {out} {doc_cur}")
    return out


def _to_num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pct(v: Any) -> str | None:
    """税率 → `数字%` 字符串（与国家模板 taxRate 契约一致）。"""
    n = _to_num(v)
    if n is None:
        return None
    return f"{int(n)}%" if float(n).is_integer() else f"{n}%"


# ── XML → 同构 dict ─────────────────────────────────────────────────────────
def _xml_to_ubl_dict(root: ET.Element) -> dict:
    """把 UBL XML 树转成 MyInvois-JSON 同形状，从而复用同一套映射。"""

    def conv(el: ET.Element) -> dict:
        out: dict[str, Any] = {}
        text = (el.text or "").strip()
        if text:
            out["_"] = text
        for ak, av in el.attrib.items():
            out[re.sub(r"^\{.*\}", "", ak)] = av
        for child in el:
            tag = re.sub(r"^\{.*\}", "", child.tag)
            out.setdefault(tag, []).append(conv(child))
        return out

    tag = re.sub(r"^\{.*\}", "", root.tag)
    return {tag: [conv(root)]}


# ── 解包 /raw 响应 ───────────────────────────────────────────────────────────
def decode_raw_document(raw: dict) -> dict | None:
    """从 LHDN `/raw` 响应里解出 UBL 文档（base64 的 JSON 或 XML）。

    `/raw` 的 `document` 字段是 base64；少数网关会直接给已解开的对象/字符串，
    这里一并容忍。失败返回 None（调用方跳过融合，不报错）。
    """
    if not isinstance(raw, dict):
        return None
    doc = raw.get("document")
    if doc is None:
        # 有的响应把 UBL 直接内联在 documentBody / invoice 下
        for k in ("documentBody", "Invoice", "invoice"):
            if isinstance(raw.get(k), (dict, list)):
                return raw if k in ("Invoice", "invoice") else raw[k]
        return None
    if isinstance(doc, dict):
        return doc

    text: str | None = None
    if isinstance(doc, (bytes, bytearray)):
        text = bytes(doc).decode("utf-8", "replace")
    elif isinstance(doc, str):
        s = doc.strip()
        if s.startswith("{") or s.startswith("<"):
            text = s                       # 已是明文
        else:
            try:
                text = base64.b64decode(s, validate=False).decode("utf-8", "replace")
            except (binascii.Error, ValueError) as exc:
                logger.info("MyInvois document base64 解码失败：%s", exc)
                return None
    if not text:
        return None

    text = text.lstrip("﻿").strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.info("MyInvois document JSON 解析失败：%s", exc)
            return None
    if text.startswith("<"):
        try:
            return _xml_to_ubl_dict(ET.fromstring(text))
        except ET.ParseError as exc:
            logger.info("MyInvois document XML 解析失败：%s", exc)
            return None
    return None


# ── 主映射 ──────────────────────────────────────────────────────────────────
def map_document(raw: dict) -> dict | None:
    """LHDN `/raw` 响应 → 平台 29 字段子集（只含税局确有的字段）。

    返回的 dict 额外带一个 `_myinvois` 元信息块（校验状态、uuid 等），
    由 fusion 取走放进 metadata，**不会**进入最终票据字段。
    """
    ubl = decode_raw_document(raw)
    if ubl is None:
        return None

    inv = _node(ubl, "Invoice") or _node(ubl, "CreditNote") or _node(ubl, "DebitNote")
    if inv is None and isinstance(ubl, dict) and "ID" in ubl:
        inv = ubl                            # 已是 Invoice 本体
    if not isinstance(inv, dict):
        return None

    out: dict[str, Any] = {}

    # —— 单据标识 ——
    inv_id = _clean_id(_text(inv, "ID")) or _clean_id(raw.get("internalId"))
    if inv_id:
        out["invoiceNumber"] = inv_id
    if d := _text(inv, "IssueDate"):
        out["invoiceDate"] = _issue_date_local(d, _text(inv, "IssueTime"))
    doc_cur = (_text(inv, "DocumentCurrencyCode") or "").upper() or None
    if doc_cur:
        out["currency"] = doc_cur

    type_code = _text(inv, "InvoiceTypeCode")
    if type_code and INVOICE_TYPE_CODES.get(str(type_code).strip()):
        out["invoiceType"] = INVOICE_TYPE_CODES[str(type_code).strip()]

    # —— 双方 ——
    _map_party(out, _val_party(inv, "AccountingSupplierParty"), "billFrom")
    _map_party(out, _val_party(inv, "AccountingCustomerParty"), "billTo")

    # —— 合计 ——
    fx = _fx(inv)
    converted: list[str] = []

    lmt = _node(inv, "LegalMonetaryTotal")
    if lmt is not None:
        net = _amount(lmt, "TaxExclusiveAmount", doc_cur, fx, converted)
        if net is None:
            net = _amount(lmt, "LineExtensionAmount", doc_cur, fx, converted)
        if net is not None:
            out["totalNetAmount"] = net
        gross = _amount(lmt, "TaxInclusiveAmount", doc_cur, fx, converted)
        if gross is None:
            gross = _amount(lmt, "PayableAmount", doc_cur, fx, converted)
        if gross is not None:
            out["totalAmount"] = gross

    tax_total = _node(inv, "TaxTotal")
    if tax_total is not None:
        tt = _amount(tax_total, "TaxAmount", doc_cur, fx, converted)
        if tt is not None:
            out["totalTaxAmount"] = tt
        rows = _map_tax_subtotals(tax_total, doc_cur, fx, converted)
        if rows:
            out["detailOfTaxSummary"] = rows

    # —— 行项目 ——
    lines = _map_lines(inv, doc_cur, fx, converted)
    if lines:
        out["detailOfGoodsOrServices"] = lines

    # —— 贷项引用原发票 ——
    refs = _map_billing_refs(inv)
    if refs:
        out["originalInvoiceReferences"] = refs

    # —— 单号（UBL 偶有承载）——
    if po := _clean_id(_text(inv, "OrderReference", "ID")):
        out["purchaseOrderNumber"] = po
    do_ref = _node(inv, "DespatchDocumentReference")
    if do_ref is not None and (do := _clean_id(_text(do_ref, "ID"))):
        out["deliveryOrderNumber"] = do

    out["_myinvois"] = {
        "uuid": raw.get("uuid"),
        "status": raw.get("status"),
        # /raw 返回 longID（大写 ID），/details 返回 longId —— 两种都认
        "long_id": raw.get("longId") or raw.get("longID"),
        "converted": converted or None,
        "exchange_rate": fx,
        "submission_uid": raw.get("submissionUid"),
        "issuer_tin": raw.get("issuerTin"),
        "receiver_tin": raw.get("receiverId") or raw.get("receiverTin"),
        "date_time_validated": raw.get("dateTimeValidated"),
        "type_code": type_code,
        "type_name": raw.get("typeName"),
    }
    return out


def _val_party(inv: dict, key: str) -> dict | None:
    wrapper = _node(inv, key)
    if wrapper is None:
        return None
    party = _node(wrapper, "Party")
    return party if isinstance(party, dict) else (wrapper if "PartyName" in wrapper else None)


def _map_party(out: dict, party: dict | None, prefix: str) -> None:
    """把一方的名称 / 税号 / BRN / 地址写进 out（prefix = billFrom|billTo）。"""
    if not isinstance(party, dict):
        return

    # 名称：PartyLegalEntity.RegistrationName 优先，其次 PartyName.Name
    name = _text(party, "PartyLegalEntity", "RegistrationName") \
        or _text(party, "PartyName", "Name")
    if name:
        out[f"{prefix}Name"] = name

    # 标识：按 schemeID 区分 TIN / BRN / SST / TTX
    for ident in _nodes(party, "PartyIdentification"):
        id_node = _node(ident, "ID")
        if not isinstance(id_node, dict):
            continue
        value = (id_node.get("_") or "").strip() if isinstance(id_node.get("_"), str) \
            else id_node.get("_")
        scheme = (id_node.get("schemeID") or "").upper().strip()
        if not value:
            continue
        value = str(value).strip()
        if scheme == "TIN":
            out[f"{prefix}TaxIdentificationNumber"] = value
        elif scheme in ("BRN", "NRIC", "PASSPORT", "ARMY"):
            # BRN 只有开票方字段承载（与国家模板一致：买方无 BRN 字段）
            if prefix == "billFrom":
                out["billFromBusinessRegistrationNumber"] = value
        # SST / TTX：平台 29 字段无对应字段，按契约不落地（避免污染 TIN）

    addr = _node(party, "PostalAddress")
    if isinstance(addr, dict):
        composite = _compose_address(addr)
        if composite:
            out[f"{prefix}Composite"] = composite
        a3 = _text(addr, "Country", "IdentificationCode")
        if a3:
            a2 = _ALPHA3_TO_2.get(a3.upper(), a3.upper()[:2] if len(a3) >= 2 else None)
            if a2:
                out[f"{prefix}CountryCode"] = a2
                out[f"{prefix}Country"] = _COUNTRY_NAME.get(a2, a3.upper())


def _compose_address(addr: dict) -> str | None:
    parts: list[str] = []
    for line in _nodes(addr, "AddressLine"):
        if t := _text(line, "Line"):
            parts.append(t)
    for key in ("PostalZone", "CityName", "CountrySubentityCode", "CountrySubentity"):
        if t := _text(addr, key):
            parts.append(t)
    seen, uniq = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return ", ".join(uniq) if uniq else None


def _map_tax_subtotals(tax_total: dict, doc_cur: str | None = None,
                       fx: dict | None = None,
                       converted: list[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for st in _nodes(tax_total, "TaxSubtotal"):
        if not isinstance(st, dict):
            continue
        cat = _node(st, "TaxCategory")
        code = _text(cat, "ID") if cat is not None else None
        row: dict[str, Any] = {}
        if code:
            row["taxCategory"] = TAX_TYPE_CODES.get(str(code).strip(), str(code).strip())
        # preprod 实测：Percent 是 TaxSubtotal 的**直接子元素**，不在 TaxCategory 下。
        # 两个位置都试，兼容 JSON/XML 两种提交形态。
        rate = _pct(_val(st, "Percent"))
        if rate is None and cat is not None:
            rate = _pct(_val(cat, "Percent"))
        if rate:
            row["taxRate"] = rate
        base = _amount(st, "TaxableAmount", doc_cur, fx, converted)
        if base is not None:
            row["netTaxableAmount"] = base
        amt = _amount(st, "TaxAmount", doc_cur, fx, converted)
        if amt is not None:
            row["tax"] = amt
        if row:
            rows.append(row)
    return rows


def _map_lines(inv: dict, doc_cur: str | None = None,
               fx: dict | None = None,
               converted: list[str] | None = None) -> list[dict]:
    lines: list[dict] = []
    for ln in _nodes(inv, "InvoiceLine") or _nodes(inv, "CreditNoteLine"):
        if not isinstance(ln, dict):
            continue
        row: dict[str, Any] = {}

        item = _node(ln, "Item")
        if item is not None:
            if desc := _text(item, "Description"):
                row["articleName"] = desc

        qty_node = _node(ln, "InvoicedQuantity") or _node(ln, "CreditedQuantity")
        if isinstance(qty_node, dict):
            q = _to_num(qty_node.get("_"))
            if q is not None:
                row["quantity"] = q
        # unitOfMeasure 刻意**不映射**：UBL 的 unitCode 是 UN/ECE Rec 20 代码
        # （preprod 实测 unitCode="10"，票面印的是 "group"）。代码不等于票面
        # 计量单位，写进去反而不如 OCR 读到的可读值，故保留 OCR。

        price = _node(ln, "Price")
        if price is not None:
            p = _amount(price, "PriceAmount", doc_cur, fx, converted)
            if p is not None:
                row["unitPrice"] = p

        net = _amount(ln, "LineExtensionAmount", doc_cur, fx, converted)
        if net is not None:
            row["netAmount"] = net

        ln_tax = _node(ln, "TaxTotal")
        if ln_tax is not None:
            t = _amount(ln_tax, "TaxAmount", doc_cur, fx, converted)
            if t is not None:
                row["tax"] = t
            sub = _node(ln_tax, "TaxSubtotal")
            if sub is not None:
                r = _pct(_val(sub, "Percent"))
                if r is None:
                    cat = _node(sub, "TaxCategory")
                    if cat is not None:
                        r = _pct(_val(cat, "Percent"))
                if r:
                    row["taxRate"] = r
            # 两者都已由 _amount 归一到单据币种，可放心相加
            if net is not None and t is not None:
                row["grossAmount"] = round(net + t, 6)

        if row:
            lines.append(row)
    return lines


def _map_billing_refs(inv: dict) -> list[dict]:
    refs: list[dict] = []
    for br in _nodes(inv, "BillingReference"):
        if not isinstance(br, dict):
            continue
        inv_ref = _node(br, "InvoiceDocumentReference")
        if not isinstance(inv_ref, dict):
            continue
        row: dict[str, Any] = {}
        if n := _clean_id(_text(inv_ref, "ID")):
            row["originalInvoiceNumber"] = n
        if d := _text(inv_ref, "IssueDate"):
            # 与 invoiceDate 同一把尺子：对外输出的日期一律是马来本地日期。
            # BillingReference 常常只有 IssueDate 没有 IssueTime —— 那时
            # _issue_date_local 会保守地原样返回，不会瞎推一天。
            row["originalInvoiceDate"] = _issue_date_local(
                d, _text(inv_ref, "IssueTime"))
        if row:
            refs.append(row)
    return refs


# 报送方常用来表示"没有"的占位串。实测 preprod 真实票据里
# OrderReference/ID = "NA" —— 照单全收会把占位符当成真单号写进
# purchaseOrderNumber，下游据此去匹配采购单必然落空。留空比填 "NA" 正确。
_PLACEHOLDERS = {
    "NA", "N/A", "N.A.", "NIL", "NONE", "NULL", "-", "--", "---", "/",
    "TBA", "TBD", "X", "XX", "XXX", "0", "N", "无", "暂无", "不适用",
}


def _clean_id(value: str | None) -> str | None:
    """清洗单号类字段：占位符一律当作"没有"。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() in _PLACEHOLDERS:
        return None
    return s


def _norm_date(value: str) -> str:
    """UBL IssueDate 本就是 YYYY-MM-DD；容忍带时间后缀。"""
    s = str(value).strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s


# 马来西亚自 1982-01-01 起固定 UTC+8 且无夏令时 —— 对发票日期而言定值即精确，
# 用定值也免掉对系统 tzdata 的依赖。
_MYT_OFFSET = timedelta(hours=8)
_UTC_MARK = re.compile(r"(?:Z|[+-]00:?00)$", re.IGNORECASE)


def _issue_date_local(date_text: str, time_text: str | None) -> str:
    """把 UBL 的 (IssueDate, IssueTime) 折算成马来本地日期。

    MyInvois 规定 IssueDate/IssueTime **按 UTC 提交**（IssueTime 带 `Z` 后缀）。
    直接取 IssueDate 等于拿 UTC 日期当票面日期：凡 00:00–08:00 MYT 开具的票，
    税局日期比票面早一天。又因「税局恒覆盖 OCR」，这会把 OCR 本来读对的
    invoiceDate 覆盖成错的，月末还会跨账期。

    保守起见**仅在 IssueTime 明确带 UTC 标记时换算**：没有时间、或时间不带
    时区标记（部分报送方直接填本地时间），一律按原样返回 —— 宁可不动，
    也不能把本已正确的本地日期再推后一天。
    """
    base = _norm_date(date_text)
    t = str(time_text or "").strip()
    if not t or not _UTC_MARK.search(t):
        return base
    m = re.match(r"^(\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?", t)
    if not m or not re.match(r"^\d{4}-\d{2}-\d{2}$", base):
        return base
    try:
        dt = datetime(
            *(int(x) for x in base.split("-")),
            hour=int(m.group(1)), minute=int(m.group(2)),
            second=int(float(m.group(3) or 0)),
        )
    except ValueError:
        return base
    return (dt + _MYT_OFFSET).strftime("%Y-%m-%d")
