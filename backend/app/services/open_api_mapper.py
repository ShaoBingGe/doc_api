"""扁平提取结果 → 开放平台（piaozone）响应结构的映射。

国家模板（如 MY_invoice_prompt.yaml）输出的是**扁平**的票据对象数组：

    [{"docType": "invoice", "invoiceNumber": "9311", "billFromName": "...", ...}]

生产开放平台的契约是**分组嵌套**、且**所有标量值都是字符串**：

    {"errcode": "0000", "description": "Success",
     "data": [{"header": {"basic": {...}, "billTo": {...}, "billFrom": {...},
                          "bussiness": {...}, "payment": {...}},
               "detail": {"detailOfGoodsOrServices": [...],
                          "detailOfTaxSummary": [...],
                          "originalInvoiceReferences": []}}],
     "traceId": "...", "docPages": N}

本模块只做形状搬运，不改动任何值的语义：
  * 分组键与顺序、字段全集都对齐线上日志（缺失字段补空串，不省略）；
  * `bussiness` 的拼写照抄线上契约（不是笔误，改了会破坏调用方解析）；
  * 数字/数组一律转字符串（线上 `"totalAmount": "100.24"`、`"page": ["1"]`）。
"""

from __future__ import annotations

from typing import Any

# ── 分组定义（键顺序 = 线上响应字段顺序）─────────────────────────────────────

BASIC_FIELDS = [
    "sourceFileHash", "docType", "nameOfInvoice", "invoiceType", "invoiceNumber",
    "invoiceCode", "invoiceDate", "totalNetAmount", "totalAmount", "totalTaxAmount",
    "currency", "page",
]

BILL_TO_FIELDS = [
    "billToName", "billToComposite", "billToCity", "billToStateOrProvince",
    "billToCountry", "billToCountryCode", "billToFax", "billToPostalCode",
    "billToTelephone", "billToTaxIdentificationNumber", "billToRecipient",
    "billToBankAccount", "billToBankOfAccount", "billToEmail",
]

BILL_FROM_FIELDS = [
    "billFromName", "billFromComposite", "billFromCity", "billFromStateOrProvince",
    "billFromCountry", "billFromCountryCode", "billFromFax", "billFromPostalCode",
    "billFromTelephone", "billFromTaxIdentificationNumber",
    "billFromBusinessRegistrationNumber", "billFromBankAccount",
    "billFromBankOfAccount", "billFromEmail",
]

# 线上契约拼作 "bussiness"（sic）—— 照抄，改了会破坏调用方解析
BUSSINESS_FIELDS = [
    "purchaseOrderNumber", "contractNumber", "startDate", "endDate",
    "salesOrderNumber", "deliveryOrderNumber",
]

PAYMENT_FIELDS = [
    "paymentMethod", "paymentStatus", "paymentTerms", "dueDate",
    "paymentCurrency", "exchangeRate", "paidAmount",
]

GOODS_ROW_FIELDS = [
    "articleID", "articleName", "description", "quantity", "unitOfMeasure",
    "unitPrice", "netAmount", "taxRate", "tax", "grossAmount", "orderNumber",
    "salesOrderNumber", "deliveryOrderNumber", "purchaseOrderNumber",
]

TAX_ROW_FIELDS = ["taxCategory", "taxRate", "netTaxableAmount", "tax"]

ORIGINAL_REF_FIELDS = [
    "originalInvoiceCode", "originalInvoiceNumber", "originalInvoiceDate",
]

SUCCESS_CODE = "0000"
SUCCESS_DESC = "Success"

# 文档页数超过模型单次可处理上限时的提示语。仍返回 errcode="0000"（前 N 页确实识别成功了），
# 只是把降级情况写进 description，调用方无需改分支逻辑就能察觉截断。
TRUNCATED_DESC = "超过{limit}页的部分，不予以识别"

# ── 字段别名兜底 ──────────────────────────────────────────────────────────────
#
# qwen 视觉模型不支持 response_schema 硬约束（DashScope 限制，见 CLAUDE.md §六），
# 输出字段名只靠 prompt 内的 schema 文本约束，实测会自由发挥：同一份 MY 模板下
# 有的票吐 `billFromName`，有的吐 `billFrom`；有的吐 `invoiceDate`，有的吐 `issueDate`。
# 严格按规范键取值会把这些识别对了的值丢成空串。
#
# 规则：别名**只在规范键缺失或为空时**生效，绝不覆盖已有值；只收语义唯一的同义键，
# 有歧义的（如 `date`、`name`）不进表，宁可留空也不猜错。
# 别名按**归一化**匹配（小写 + 去下划线/连字符/空格），所以一条 "subtotalamount"
# 同时覆盖 subTotal­Amount / sub_total_amount / SubtotalAmount 等写法——实测同一张票
# 两次调用就吐过 `subTotal` 和 `subtotalAmount` 两种，逐个字面量加是打地鼠。
# 列表顺序即优先级，语义最确定的在前，兜底项在后。
ALIASES: dict[str, tuple[str, ...]] = {
    "invoiceDate": ("issueDate", "invoiceIssueDate", "dateOfIssue",
                    "date"),  # `date` 歧义最大，放末位兜底
    "billFromName": ("billFrom", "seller", "sellerName", "vendor", "vendorName",
                     "supplier", "supplierName"),
    "billToName": ("billTo", "buyer", "buyerName", "customer", "customerName"),
    "billFromComposite": ("billFromAddress", "sellerAddress", "vendorAddress",
                          "supplierAddress"),
    "billToComposite": ("billToAddress", "buyerAddress", "customerAddress"),
    "billFromTelephone": ("billFromPhone", "billFromPhoneNumber", "sellerPhone"),
    "billToTelephone": ("billToPhone", "billToPhoneNumber", "buyerPhone"),
    "billFromTaxIdentificationNumber": ("billFromTaxId", "sellerTaxId", "sellerTIN"),
    "billToTaxIdentificationNumber": ("billToTaxId", "buyerTaxId", "buyerTIN"),
    "totalNetAmount": ("subTotal", "subtotalAmount", "netTotal", "netAmountTotal"),
    "totalTaxAmount": ("taxTotal", "totalTax", "taxAmount"),
    "nameOfInvoice": ("invoiceTitle", "documentTitle"),
    "purchaseOrderNumber": ("poNumber", "customerReferenceNumber",
                            "yourPurchaseOrderNumber"),
    "deliveryOrderNumber": ("ourDeliveryOrderNumber", "doNumber"),
    "paymentTerms": ("terms",),
}


def _nkey(k: str) -> str:
    """键归一化：小写 + 去下划线/连字符/空格。"""
    return k.lower().replace("_", "").replace("-", "").replace(" ", "")


# 预计算：归一化别名 → 规范键的优先级序（构建一次，避免每条实体重复归一）
_ALIAS_INDEX: dict[str, list[str]] = {
    canonical: [_nkey(a) for a in aliases] for canonical, aliases in ALIASES.items()
}

# 别名值为嵌套对象时，从中提取主名的候选键（如 billFrom: {"name": "..."}）
_NESTED_NAME_KEYS = ("name", "companyName", "value", "text", "title")


def _coerce_alias_value(value: Any) -> Any:
    """别名值可能是嵌套对象/数组 → 取其主名；取不到就放弃（返回 None）。"""
    if isinstance(value, dict):
        for k in _NESTED_NAME_KEYS:
            v = value.get(k)
            if isinstance(v, (str, int, float)) and str(v).strip():
                return v
        return None
    if isinstance(value, (list, tuple)):
        return _coerce_alias_value(value[0]) if len(value) == 1 else None
    return value


def apply_aliases(entity: dict) -> dict:
    """把模型吐出的别名键补进规范键（不覆盖已有值）。返回新 dict，不改原对象。

    匹配按归一化键进行，故 `subTotal` / `subtotal_amount` / `SubtotalAmount`
    命中同一条规则。规范键本身若已有值则整条跳过。
    """
    out = dict(entity)
    # 实体的归一化键 → 原始键（重名时保留先出现的，与 dict 顺序一致）
    present: dict[str, str] = {}
    for k in out:
        present.setdefault(_nkey(k), k)

    for canonical, nalias in _ALIAS_INDEX.items():
        if out.get(canonical) not in (None, "", [], {}):
            continue  # 规范键已有值 → 别名不参与
        for na in nalias:
            src = present.get(na)
            if src is None or src == canonical:
                continue
            val = _coerce_alias_value(out.get(src))
            if val not in (None, "", [], {}):
                out[canonical] = val
                break
    return out


# ── 值归一 ────────────────────────────────────────────────────────────────────

def _to_str(value: Any) -> str:
    """标量 → 字符串（线上所有标量都是字符串）。None/缺失 → 空串。

    bool 先于 int 判断（Python 中 bool 是 int 的子类），避免 True → "1"。
    float 去掉无意义的科学计数与尾随处理交给 repr：线上 "100.24" / "81.0" 两种
    形态都出现过，说明生产侧就是直接 str() 数字，这里保持一致。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return str(value)


def _to_str_list(value: Any) -> list[str]:
    """数组 → 字符串数组（如 page: [1] → ["1"]）。非数组包成单元素数组。"""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [_to_str(v) for v in value]
    return [_to_str(value)]


def _group(entity: dict, fields: list[str]) -> dict[str, Any]:
    """按字段全集取值：缺失补空串，page 特殊处理为字符串数组。"""
    out: dict[str, Any] = {}
    for f in fields:
        out[f] = _to_str_list(entity.get(f)) if f == "page" else _to_str(entity.get(f))
    return out


def _rows(value: Any, fields: list[str]) -> list[dict[str, str]]:
    """明细数组 → 字段补齐 + 全字符串化的行数组。"""
    if not isinstance(value, list):
        return []
    out = []
    for row in value:
        if not isinstance(row, dict):
            continue
        out.append({f: _to_str(row.get(f)) for f in fields})
    return out


# ── 主映射 ────────────────────────────────────────────────────────────────────

def map_entity(entity: dict, *, source_file_hash: str = "") -> dict:
    """一条扁平票据 → 一个 {header, detail} 对象。"""
    enriched = apply_aliases(entity)
    # sourceFileHash 由调用方注入（模型不产出它）；实体自带则以自带为准
    enriched.setdefault("sourceFileHash", source_file_hash)
    if not enriched.get("sourceFileHash"):
        enriched["sourceFileHash"] = source_file_hash

    return {
        "header": {
            "basic": _group(enriched, BASIC_FIELDS),
            "billTo": _group(enriched, BILL_TO_FIELDS),
            "billFrom": _group(enriched, BILL_FROM_FIELDS),
            "bussiness": _group(enriched, BUSSINESS_FIELDS),
            "payment": _group(enriched, PAYMENT_FIELDS),
        },
        "detail": {
            "detailOfGoodsOrServices": _rows(
                enriched.get("detailOfGoodsOrServices"), GOODS_ROW_FIELDS),
            "detailOfTaxSummary": _rows(
                enriched.get("detailOfTaxSummary"), TAX_ROW_FIELDS),
            "originalInvoiceReferences": _rows(
                enriched.get("originalInvoiceReferences"), ORIGINAL_REF_FIELDS),
        },
    }


def normalise_entities(structured: Any) -> list[dict]:
    """把处理器的各种输出形状统一成扁平票据列表。

    支持：顶层数组、`{"entities": [...]}`、`{"data": [...]}`、单个对象。
    """
    if isinstance(structured, list):
        return [e for e in structured if isinstance(e, dict)]
    if isinstance(structured, dict):
        for key in ("entities", "data", "results"):
            inner = structured.get(key)
            if isinstance(inner, list):
                return [e for e in inner if isinstance(e, dict)]
        # 单个票据对象
        if structured:
            return [structured]
    return []


def build_response(
    structured: Any,
    *,
    trace_id: str,
    doc_pages: int,
    source_file_hash: str = "",
    errcode: str = SUCCESS_CODE,
    description: str = SUCCESS_DESC,
) -> dict:
    """组装完整的开放平台响应体（字段顺序与线上一致）。"""
    entities = normalise_entities(structured)
    return {
        "errcode": errcode,
        "description": description,
        "data": [map_entity(e, source_file_hash=source_file_hash) for e in entities],
        "traceId": trace_id,
        "docPages": doc_pages,
    }


def build_error(errcode: str, description: str, *, trace_id: str) -> dict:
    """错误响应：保持同一外壳，data 为空数组（调用方无需分支解析）。"""
    return {
        "errcode": errcode,
        "description": description,
        "data": [],
        "traceId": trace_id,
        "docPages": 0,
    }
