"""针对 **LHDN preprod 真实响应** 的回归测试。

fixture 是 2026-09-18 用沙箱凭证（Kingdee / preprod）实打 `GET /documents/{uuid}/raw`
拿回来的原样响应，内容为税局测试主体「马来西亚测试企业」，无真实客户 PII。

这些用例锁住的都是**真实数据才暴露出来的坑**，每一条都对应一次线上会算错钱的 bug：
  1. `document` 是裸 XML，不是 base64
  2. 税额按 MYR 申报、单据可为外币 → 币种混用会让 net + tax ≠ total
  3. `Percent` 挂在 TaxSubtotal 下，不在 TaxCategory 下
  4. `/raw` 用 longID（大写 ID），`/details` 用 longId
  5. `unitCode` 是 UN/ECE 数字代码（"10"），不是票面计量单位（"group"）
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.services.myinvois.ubl_mapper import decode_raw_document, map_document

FIX = pathlib.Path(__file__).parent / "fixtures" / "myinvois"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def usd() -> dict:
    return _load("preprod_raw_usd.json")


@pytest.fixture
def myr() -> dict:
    return _load("preprod_raw_myr.json")


# ── 坑 1：document 是裸 XML ──────────────────────────────────────────────────
def test_document_is_plain_xml_not_base64(usd):
    assert usd["document"].lstrip().startswith("<?xml"), "preprod 返回裸 XML"
    ubl = decode_raw_document(usd)
    assert ubl is not None and "Invoice" in ubl


# ── 坑 2：外币单据 + MYR 税额 ────────────────────────────────────────────────
def test_usd_invoice_converts_myr_tax_via_ubl_exchange_rate(usd):
    """票面(USD)：Excl 12.85 / Tax 1.03 / Incl 13.88；XML 里 TaxAmount 是 MYR 1.71。

    税局数据一律优先，所以不是丢弃 MYR 税额，而是用 UBL 自带的
    TaxExchangeRate（USD ×1.66 → MYR）折回单据币种：1.71 ÷ 1.66 = 1.03，
    与票面 USD 税额完全一致，且 net + tax = total 自洽。
    """
    m = map_document(usd)
    assert m["currency"] == "USD"
    assert m["totalNetAmount"] == 12.85
    assert m["totalAmount"] == 13.88
    assert m["totalTaxAmount"] == 1.03, "MYR 1.71 应按税局汇率折为 USD 1.03"
    assert round(m["totalNetAmount"] + m["totalTaxAmount"], 2) == m["totalAmount"]
    # 汇率块与换算留痕，便于审计
    fx = m["_myinvois"]["exchange_rate"]
    assert fx == {"source": "USD", "target": "MYR", "rate": 1.66, "op": "multiply"}
    conv = m["_myinvois"]["converted"]
    assert conv and any("1.71 MYR" in c and "1.03 USD" in c for c in conv)
    # 税表与行项目同样归一
    row = m["detailOfTaxSummary"][0]
    assert row["taxRate"] == "8%"
    assert row["netTaxableAmount"] == 12.85
    assert row["tax"] == 1.03
    line = m["detailOfGoodsOrServices"][0]
    assert line["tax"] == 1.03
    assert line["grossAmount"] == 13.88


def test_myr_invoice_needs_no_conversion(myr):
    """本币单据：币种一致，税额与多档税率完整映射，无需换算。"""
    m = map_document(myr)
    assert m["currency"] == "MYR"
    assert m["totalNetAmount"] == 144.76
    assert m["totalTaxAmount"] == 17.63
    assert m["totalAmount"] == 162.39
    assert not m["_myinvois"]["converted"], "同币种不应产生换算记录"
    # net + tax == total（币种一致才成立）
    assert round(m["totalNetAmount"] + m["totalTaxAmount"], 2) == m["totalAmount"]
    # 多档税率分行
    rows = m["detailOfTaxSummary"]
    assert len(rows) == 3
    assert {r["taxRate"] for r in rows} == {"8%", "5%", "0%"}
    assert round(sum(r["tax"] for r in rows), 2) == m["totalTaxAmount"]
    # 免税档：税种码 E → Exempt
    exempt = [r for r in rows if r["taxRate"] == "0%"][0]
    assert exempt["taxCategory"] == "Exempt"


# ── 坑 3：Percent 在 TaxSubtotal 下 ─────────────────────────────────────────
def test_percent_read_from_tax_subtotal_level(usd):
    """真实 XML 的 <cbc:Percent> 是 TaxSubtotal 直接子元素，不在 TaxCategory 内。"""
    assert "<cbc:Percent>8</cbc:Percent>" in usd["document"]
    m = map_document(usd)
    assert m["detailOfTaxSummary"][0]["taxRate"] == "8%"
    assert m["detailOfGoodsOrServices"][0]["taxRate"] == "8%"


# ── 坑 4：longID 大小写 ─────────────────────────────────────────────────────
def test_long_id_capital_casing_from_raw(usd):
    assert "longID" in usd and "longId" not in usd, "/raw 用的是 longID"
    assert map_document(usd)["_myinvois"]["long_id"] == usd["longID"]


# ── 坑 5：unitCode 不是票面计量单位 ─────────────────────────────────────────
def test_unit_code_not_mapped_as_unit_of_measure(usd):
    """XML unitCode="10"（UN/ECE 代码），票面印的是 "group" —— 不映射，留 OCR。"""
    assert 'unitCode="10"' in usd["document"]
    line = map_document(usd)["detailOfGoodsOrServices"][0]
    assert "unitOfMeasure" not in line
    # 其余行字段照常
    assert line["articleName"] == "test"
    assert line["quantity"] == 2.333
    assert line["unitPrice"] == 6.3636


# ── 综合：核心身份字段 ──────────────────────────────────────────────────────
def test_core_identity_fields_from_real_payload(usd):
    m = map_document(usd)
    assert m["invoiceNumber"] == "MYTEST20260917000085"
    assert m["invoiceDate"] == "2026-09-17"
    assert m["billFromTaxIdentificationNumber"] == "C60131481060"
    assert m["billToTaxIdentificationNumber"] == "C60131481060"
    assert m["billFromBusinessRegistrationNumber"] == "202501024926"
    assert m["billFromCountryCode"] == "MY"
    meta = m["_myinvois"]
    assert meta["status"] == "Valid"
    assert meta["uuid"] == "R4M6XHDBQFKEK46WS10C5Q2M10"
    # 类型码 01 不覆盖 invoiceType（无法区分 Commercial / Tax Invoice）
    assert meta["type_code"] == "01"
    assert "invoiceType" not in m


# ── 坑 6：报送方用占位符表示"没有" ─────────────────────────────────────────
@pytest.mark.parametrize("placeholder", ["NA", "N/A", "n.a.", "NIL", "-", "TBD", "无"])
def test_placeholder_order_reference_is_dropped(usd, placeholder):
    """preprod 实测：某票 OrderReference/ID = "NA"。

    照单全收会把占位符当成真 PO 号写进 purchaseOrderNumber，下游据此去匹配
    采购单必然落空 —— 留空才是正确语义。
    """
    import re as _re
    doc = usd["document"].replace(
        "</cbc:InvoiceTypeCode>",
        f"</cbc:InvoiceTypeCode>\n<cac:OrderReference><cbc:ID>{placeholder}</cbc:ID></cac:OrderReference>",
    )
    m = map_document({**usd, "document": doc})
    assert "purchaseOrderNumber" not in m, f"占位符 {placeholder!r} 不应落地"


def test_real_order_reference_is_kept(usd):
    """真实单号照常提取 —— 过滤不能误杀。"""
    doc = usd["document"].replace(
        "</cbc:InvoiceTypeCode>",
        "</cbc:InvoiceTypeCode>\n<cac:OrderReference><cbc:ID>W1 537443</cbc:ID></cac:OrderReference>",
    )
    m = map_document({**usd, "document": doc})
    assert m["purchaseOrderNumber"] == "W1 537443"
