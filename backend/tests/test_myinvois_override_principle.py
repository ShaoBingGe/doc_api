"""不可动摇的原则：**税局登记信息恒覆盖 OCR**。

这不是一条普通用例，是一条**契约锁**。客户明确要求：凡税局有的字段，
一律以税局登记值为准，OCR 只在税局没有该字段时才保留。

典型场景（真实发生过）：票面把公司名印成全大写
`ENGENIOUS SOLUTIONS SDN BHD`，而税局登记名是 `Engenious Solutions Sdn Bhd`。
下游对账、供应商主数据匹配都应以**登记名**为准 —— 票面写法只是排版。

若将来有人认为"应保留票面原样"而把某个登记字段移出覆盖范围，
本文件会失败。要改必须先改这里，并且需要客户重新拍板。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.services.myinvois.fusion import _NEVER_OVERRIDE, fuse_sources
from app.services.myinvois.link_extractor import MyInvoisRef
from app.services.myinvois.ubl_mapper import map_document

FIX = pathlib.Path(__file__).parent / "fixtures" / "myinvois"

# 税局登记信息 —— 这些字段**只要税局有值，就必须覆盖 OCR**
REGISTRATION_FIELDS = [
    "billFromName",
    "billFromTaxIdentificationNumber",
    "billFromBusinessRegistrationNumber",
    "billFromComposite",
    "billFromCountry",
    "billFromCountryCode",
    "billToName",
    "billToTaxIdentificationNumber",
    "billToComposite",
    "billToCountry",
    "billToCountryCode",
]
# 税局登记的单据事实 —— 同样必须覆盖
DOCUMENT_FACTS = [
    "invoiceNumber", "invoiceDate", "currency",
    "totalNetAmount", "totalTaxAmount", "totalAmount",
    "detailOfTaxSummary", "detailOfGoodsOrServices",
]


def _lhdn() -> dict:
    return map_document(json.loads((FIX / "preprod_raw_myr.json").read_text(encoding="utf-8")))


def _ref() -> MyInvoisRef:
    return MyInvoisRef(uuid="U" * 26, share_token="", page=1,
                       source="annotation", url="")


def _ocr_with_conflicting_values(lhdn: dict) -> dict:
    """造一条"每个字段都和税局不一样"的 OCR 结果。"""
    ocr = {"docType": "invoice", "invoiceType": "Tax Invoice", "page": [1]}
    for k in REGISTRATION_FIELDS + DOCUMENT_FACTS:
        v = lhdn.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            ocr[k] = v.upper() + " (OCR 票面写法)"
        elif isinstance(v, (int, float)):
            ocr[k] = float(v) + 11111.0
        elif isinstance(v, list):
            ocr[k] = [{"articleName": "OCR 读到的完全不同的行"}]
    return ocr


@pytest.mark.parametrize("field", REGISTRATION_FIELDS)
def test_registration_field_always_wins(field):
    """逐个登记字段：税局有值时，消融后必须等于税局值，不得是 OCR 值。"""
    lhdn = _lhdn()
    if field not in lhdn:
        pytest.skip(f"fixture 中税局未提供 {field}")
    ocr = _ocr_with_conflicting_values(lhdn)
    fused, _ = fuse_sources([ocr], [("lhdn", [(_ref(), lhdn)])])
    assert fused[0][field] == lhdn[field], (
        f"{field} 必须以税局登记值为准，实得 {fused[0][field]!r}")
    assert fused[0][field] != ocr[field]


@pytest.mark.parametrize("field", DOCUMENT_FACTS)
def test_document_fact_always_wins(field):
    lhdn = _lhdn()
    if field not in lhdn:
        pytest.skip(f"fixture 中税局未提供 {field}")
    ocr = _ocr_with_conflicting_values(lhdn)
    fused, _ = fuse_sources([ocr], [("lhdn", [(_ref(), lhdn)])])
    assert fused[0][field] == lhdn[field], f"{field} 必须以税局值为准"


def test_company_name_casing_registered_beats_printed():
    """复现 INV-ESSB-000012 真实场景：票面全大写 vs 税局登记大小写 —— 取登记名。

    票面 `ENGENIOUS SOLUTIONS SDN BHD` 只是排版；供应商主数据匹配、对账
    都应落到税局登记的 `Engenious Solutions Sdn Bhd`。
    """
    registered = "Engenious Solutions Sdn Bhd"
    printed = "ENGENIOUS SOLUTIONS SDN BHD"
    assert registered != printed                       # 场景前提
    lhdn = {**_lhdn(), "billFromName": registered}
    ocr = {"docType": "invoice", "invoiceType": "Tax Invoice", "page": [1],
           "billFromName": printed}
    fused, _ = fuse_sources([ocr], [("lhdn", [(_ref(), lhdn)])])
    assert fused[0]["billFromName"] == registered, "必须取税局登记名，而非票面写法"


def test_only_page_and_doctype_are_exempt():
    """豁免名单必须**只有** page 与 docType。

    它们不是"票面事实"：page 是 PDF 物理属性（税局无此概念），
    docType 是平台分类口径。任何登记字段被加进豁免名单都应让本条失败。
    """
    assert _NEVER_OVERRIDE == {"page", "docType"}, (
        f"豁免名单被改动了：{_NEVER_OVERRIDE} —— 税局登记信息不得进入豁免")


def test_ocr_only_survives_when_tax_authority_has_no_such_field():
    """反向保证：税局**没有**的字段，OCR 值必须原样保留（否则会丢数据）。"""
    lhdn = _lhdn()
    ocr = {
        "docType": "invoice", "invoiceType": "Tax Invoice", "page": [1],
        "nameOfInvoice": "E - INVOICE",        # UBL 不承载票面标题
        "invoiceCode": "LOCAL-CODE-7",
        "dueDate": "2026-12-31",
        "salesOrderNumber": "SO-888",
        "purchaseOrderNumber": "PO-LOCAL-77",
    }
    fused, _ = fuse_sources([ocr], [("lhdn", [(_ref(), lhdn)])])
    for k, v in ocr.items():
        if k in lhdn:
            continue
        assert fused[0][k] == v, f"{k} 税局没有，必须保留 OCR 值"


def test_placeholder_filter_does_not_touch_registration_fields():
    """占位符过滤只作用于单号类字段，绝不能削弱登记信息的覆盖。"""
    from app.services.myinvois.ubl_mapper import _clean_id

    lhdn = _lhdn()
    for f in REGISTRATION_FIELDS:
        if f in lhdn:
            assert lhdn[f], f"{f} 不应被任何清洗逻辑清空"
    # 过滤器本身语义正确
    assert _clean_id("NA") is None and _clean_id("W1 537443") == "W1 537443"
