"""MyInvois 查验增强：链接提取 / UBL 映射 / 消融归一。

因为无法在测试里打真实税局 API（需要客户侧 access_token），这里用**贴近真实
结构的 UBL fixture** 覆盖映射与融合逻辑；链接提取则直接跑仓库里的真实 PDF。
"""

from __future__ import annotations

import base64
import json

import pytest

from app.services.myinvois.fusion import fuse_entities
from app.services.myinvois.link_extractor import MyInvoisRef, extract_refs
from app.services.myinvois.ubl_mapper import decode_raw_document, map_document


# ── fixtures ────────────────────────────────────────────────────────────────
def _v(x):
    """MyInvois JSON 的 [{"_": value}] 包装。"""
    return [{"_": x}]


def _ubl_invoice(
    *,
    inv_id="INV-9001",
    type_code="01",
    currency="MYR",
    net=1000.0,
    tax=60.0,
    gross=1060.0,
):
    return {
        "Invoice": [{
            "ID": _v(inv_id),
            "IssueDate": _v("2026-03-15"),
            "InvoiceTypeCode": [{"_": type_code, "listVersionID": "1.0"}],
            "DocumentCurrencyCode": _v(currency),
            "AccountingSupplierParty": [{"Party": [{
                "PartyLegalEntity": [{"RegistrationName": _v("WMSB MARKETING SDN BHD")}],
                "PartyIdentification": [
                    {"ID": [{"_": "C8875596070", "schemeID": "TIN"}]},
                    {"ID": [{"_": "201801036457", "schemeID": "BRN"}]},
                    {"ID": [{"_": "W10-2210-22000008", "schemeID": "SST"}]},
                ],
                "PostalAddress": [{
                    "AddressLine": [{"Line": _v("No 8, Jalan Perindustrian")}],
                    "CityName": _v("Shah Alam"),
                    "PostalZone": _v("40150"),
                    "Country": [{"IdentificationCode": _v("MYS")}],
                }],
            }]}],
            "AccountingCustomerParty": [{"Party": [{
                "PartyLegalEntity": [{"RegistrationName": _v("PP CHIN HIN SDN BHD")}],
                "PartyIdentification": [
                    {"ID": [{"_": "C5884056070", "schemeID": "TIN"}]},
                ],
                "PostalAddress": [{
                    "AddressLine": [{"Line": _v("Menara Chin Hin")}],
                    "CityName": _v("Kuala Lumpur"),
                    "Country": [{"IdentificationCode": _v("MYS")}],
                }],
            }]}],
            "InvoiceLine": [
                {
                    "ID": _v("1"),
                    "InvoicedQuantity": [{"_": 10, "unitCode": "UNT"}],
                    "LineExtensionAmount": [{"_": 600.0, "currencyID": "MYR"}],
                    "Item": [{"Description": _v("CEMENT BAG 50KG")}],
                    "Price": [{"PriceAmount": [{"_": 60.0}]}],
                    "TaxTotal": [{
                        "TaxAmount": [{"_": 36.0}],
                        "TaxSubtotal": [{
                            "TaxCategory": [{"ID": _v("02"), "Percent": _v(6)}],
                        }],
                    }],
                },
                {
                    "ID": _v("2"),
                    "InvoicedQuantity": [{"_": 4, "unitCode": "UNT"}],
                    "LineExtensionAmount": [{"_": 400.0}],
                    "Item": [{"Description": _v("STEEL MESH A6")}],
                    "Price": [{"PriceAmount": [{"_": 100.0}]}],
                    "TaxTotal": [{"TaxAmount": [{"_": 24.0}]}],
                },
            ],
            "TaxTotal": [{
                "TaxAmount": [{"_": tax, "currencyID": "MYR"}],
                "TaxSubtotal": [{
                    "TaxableAmount": [{"_": net}],
                    "TaxAmount": [{"_": tax}],
                    "TaxCategory": [{"ID": _v("02"), "Percent": _v(6)}],
                }],
            }],
            "LegalMonetaryTotal": [{
                "LineExtensionAmount": [{"_": net}],
                "TaxExclusiveAmount": [{"_": net}],
                "TaxInclusiveAmount": [{"_": gross}],
                "PayableAmount": [{"_": gross}],
            }],
        }]
    }


def _raw(ubl: dict, *, status="Valid", uuid="R0JMAZWCTPZQ3X8BJZ9XGKNK10"):
    return {
        "uuid": uuid,
        "submissionUid": "SUB123",
        "longId": "LONG123",
        "internalId": "INV-9001",
        "typeName": "invoice",
        "status": status,
        "issuerTin": "C8875596070",
        "receiverId": "C5884056070",
        "dateTimeValidated": "2026-03-15T10:05:00Z",
        "document": base64.b64encode(json.dumps(ubl).encode()).decode(),
    }


def _ocr_entity(**over):
    """一条典型的 OCR 输出（带税局不承载的字段：票面标题 / PO / DO / page）。"""
    base = {
        "docType": "invoice",
        "invoiceType": "Tax Invoice",
        "page": [1],
        "currency": "MYR",
        "nameOfInvoice": "TAX INVOICE",
        "invoiceNumber": "INV-9001",
        "invoiceDate": "2026-03-15",
        "billFromName": "WMSB MARKETING",              # OCR 少了 SDN BHD
        "billFromTaxIdentificationNumber": "W10-2210-22000008",  # OCR 把 SST 当 TIN（真实错误模式）
        "billToName": "PP CHIN HIN SDN BHD",
        "totalNetAmount": 1000.0,
        "totalAmount": 1060.0,
        "purchaseOrderNumber": "W1 537443",            # 税局不承载 → 必须保留
        "deliveryOrderNumber": "E-DO00863",            # 同上
        "detailOfGoodsOrServices": [
            {"articleName": "CEMENT", "quantity": 10, "unitPrice": 60.0,
             "netAmount": 600.0, "purchaseOrderNumber": "W1 537443"},
            {"articleName": "STEEL MESH", "quantity": 4, "unitPrice": 100.0,
             "netAmount": 400.0},
        ],
    }
    base.update(over)
    return base


# ── 链接提取（真实 PDF）──────────────────────────────────────────────────────
def test_extract_refs_on_real_einvoice(tmp_path):
    import pathlib
    sample = pathlib.Path(__file__).parent.parent.parent / "MY-test" / \
        "INV_WMSB-26_04_0138_20260407_163842.pdf"
    if not sample.exists():
        pytest.skip("本地样本不在（MY-test 未入库）")
    refs = extract_refs(sample.read_bytes())
    assert len(refs) == 1
    r = refs[0]
    assert r.source == "annotation"
    assert len(r.uuid) == 26 and r.uuid.isalnum()
    assert r.page == 1


def test_extract_refs_multi_invoice_pdf():
    import pathlib
    sample = pathlib.Path(__file__).parent.parent.parent / "MY-test" / \
        "001 E-Invoice -New_20251022_120841.pdf"
    if not sample.exists():
        pytest.skip("本地样本不在")
    refs = extract_refs(sample.read_bytes())
    assert len(refs) == 7, "该样本含 7 张票"
    assert len({r.uuid for r in refs}) == 7, "uuid 必须各不相同（不能去重成 1 条）"
    assert [r.page for r in refs] == [1, 3, 5, 7, 9, 11, 13]


@pytest.mark.parametrize("blob", [b"", b"not a pdf", b"%PDF-broken"])
def test_extract_refs_never_raises(blob):
    assert extract_refs(blob) == []


# ── UBL 映射 ────────────────────────────────────────────────────────────────
def test_map_document_core_fields():
    m = map_document(_raw(_ubl_invoice()))
    assert m is not None
    assert m["invoiceNumber"] == "INV-9001"
    assert m["invoiceDate"] == "2026-03-15"
    assert m["currency"] == "MYR"
    # 双方 TIN 正确分流
    assert m["billFromTaxIdentificationNumber"] == "C8875596070"
    assert m["billToTaxIdentificationNumber"] == "C5884056070"
    # BRN 只落开票方
    assert m["billFromBusinessRegistrationNumber"] == "201801036457"
    assert "billToBusinessRegistrationNumber" not in m
    # 合计
    assert m["totalNetAmount"] == 1000.0
    assert m["totalAmount"] == 1060.0
    assert m["totalTaxAmount"] == 60.0
    # 国家码 alpha3 → alpha2
    assert m["billFromCountryCode"] == "MY"
    assert m["_myinvois"]["status"] == "Valid"


def test_map_document_never_writes_sst_into_tin():
    """SST 号不得落进任何 TIN 字段（平台 29 字段无 SST 字段）。"""
    m = map_document(_raw(_ubl_invoice()))
    joined = json.dumps(m, ensure_ascii=False)
    assert "W10-2210-22000008" not in joined


def test_map_document_tax_summary_and_lines():
    m = map_document(_raw(_ubl_invoice()))
    rows = m["detailOfTaxSummary"]
    assert len(rows) == 1
    assert rows[0]["taxCategory"] == "SST"        # 税种码 02 → SST
    assert rows[0]["taxRate"] == "6%"
    assert rows[0]["tax"] == 60.0
    lines = m["detailOfGoodsOrServices"]
    assert len(lines) == 2
    assert lines[0]["articleName"] == "CEMENT BAG 50KG"
    assert lines[0]["quantity"] == 10
    assert lines[0]["unitPrice"] == 60.0
    assert lines[0]["grossAmount"] == 636.0        # net 600 + tax 36
    # unitOfMeasure 刻意不映射：UBL 的 unitCode 是 UN/ECE 代码，不是票面计量单位
    # （preprod 实测 unitCode="10" 而票面印 "group"），保留 OCR 的可读值。
    assert "unitOfMeasure" not in lines[0]


def test_map_document_type_code_01_does_not_override_invoice_type():
    """01 无法区分 Commercial / Tax Invoice → 不给值，交给 OCR。"""
    m = map_document(_raw(_ubl_invoice(type_code="01")))
    assert "invoiceType" not in m


def test_map_document_credit_note_type_code():
    m = map_document(_raw(_ubl_invoice(type_code="02")))
    assert m["invoiceType"] == "Credit Note"


def test_decode_raw_accepts_xml():
    xml = """<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
                      xmlns:cbc="urn:cbc"><cbc:ID>INV-XML-1</cbc:ID>
             <cbc:IssueDate>2026-02-01</cbc:IssueDate></Invoice>"""
    raw = {"uuid": "U1", "status": "Valid",
           "document": base64.b64encode(xml.encode()).decode()}
    assert decode_raw_document(raw) is not None
    m = map_document(raw)
    assert m["invoiceNumber"] == "INV-XML-1"
    assert m["invoiceDate"] == "2026-02-01"


def test_map_document_garbage_returns_none():
    assert map_document({"document": "!!!not-base64-or-json!!!"}) is None
    assert map_document({}) is None


# ── 消融归一 ────────────────────────────────────────────────────────────────
def _ref(page=1, uuid="R0JMAZWCTPZQ3X8BJZ9XGKNK10"):
    return MyInvoisRef(uuid=uuid, share_token="T" * 30, page=page,
                       source="annotation", url="https://x")


def test_fusion_overrides_ocr_and_keeps_ocr_only_fields():
    ocr = [_ocr_entity()]
    mapped = map_document(_raw(_ubl_invoice()))
    fused, report = fuse_entities(ocr, [(_ref(), mapped)])
    e = fused[0]

    # 税局有 → 覆盖（修正了 OCR 把 SST 当 TIN 的错误）
    assert e["billFromTaxIdentificationNumber"] == "C8875596070"
    assert e["billFromName"] == "WMSB MARKETING SDN BHD"
    assert e["billFromBusinessRegistrationNumber"] == "201801036457"

    # 税局无 → 保留 OCR
    assert e["nameOfInvoice"] == "TAX INVOICE"
    assert e["purchaseOrderNumber"] == "W1 537443"
    assert e["deliveryOrderNumber"] == "E-DO00863"
    assert e["page"] == [1]
    assert e["docType"] == "invoice"
    assert e["invoiceType"] == "Tax Invoice"        # 01 未覆盖

    assert report.fused == 1
    doc = report.documents[0]
    assert "billFromTaxIdentificationNumber" in doc["fields_from_myinvois"]
    assert "purchaseOrderNumber" in doc["fields_kept_from_ocr"]


def test_fusion_preserves_line_level_po_when_counts_match():
    ocr = [_ocr_entity()]
    mapped = map_document(_raw(_ubl_invoice()))
    fused, _ = fuse_entities(ocr, [(_ref(), mapped)])
    lines = fused[0]["detailOfGoodsOrServices"]
    assert len(lines) == 2
    # 名称/金额来自税局
    assert lines[0]["articleName"] == "CEMENT BAG 50KG"
    # 行级 PO 是税局不承载的 → 从 OCR 行继承
    assert lines[0]["purchaseOrderNumber"] == "W1 537443"


def test_fusion_never_overrides_page_even_if_present():
    mapped = map_document(_raw(_ubl_invoice()))
    mapped["page"] = [99]                 # 模拟异常输入
    mapped["docType"] = "receipt"
    fused, _ = fuse_entities([_ocr_entity()], [(_ref(), mapped)])
    assert fused[0]["page"] == [1]
    assert fused[0]["docType"] == "invoice"


def test_fusion_credit_note_amounts_go_negative():
    """UBL 只存绝对值；贷项票融合后金额必须为负（否则会抹掉 OCR 判对的负号）。"""
    ocr = [_ocr_entity(invoiceType="Credit Note", totalAmount=-709.25,
                       totalNetAmount=-709.25)]
    mapped = map_document(_raw(_ubl_invoice(type_code="02", net=709.25,
                                            tax=0.0, gross=709.25)))
    assert mapped["totalAmount"] == 709.25          # 税局给的是正值
    fused, _ = fuse_entities(ocr, [(_ref(), mapped)])
    e = fused[0]
    assert e["invoiceType"] == "Credit Note"
    assert e["totalAmount"] == -709.25, "融合后必须恢复负号"
    assert e["totalNetAmount"] == -709.25
    for ln in e["detailOfGoodsOrServices"]:
        assert ln["netAmount"] <= 0
        assert ln["quantity"] <= 0


def test_fusion_invalid_status_reports_but_does_not_override():
    ocr = [_ocr_entity()]
    mapped = map_document(_raw(_ubl_invoice(), status="Cancelled"))
    fused, report = fuse_entities(ocr, [(_ref(), mapped)])
    # 未覆盖：OCR 的错误值原样保留（由调用方据报告决定怎么处理）
    assert fused[0]["billFromTaxIdentificationNumber"] == "W10-2210-22000008"
    assert report.fused == 0
    assert report.documents[0]["action"] == "reported_only"
    assert "Cancelled" in report.documents[0]["reason"]


def test_fusion_multi_invoice_associates_by_page():
    ocr = [
        _ocr_entity(page=[1, 2], invoiceNumber="A-1"),
        _ocr_entity(page=[3, 4], invoiceNumber="B-2"),
    ]
    m1 = map_document(_raw(_ubl_invoice(inv_id="A-1"), uuid="U" * 26))
    m2 = map_document(_raw(_ubl_invoice(inv_id="B-2"), uuid="V" * 26))
    fused, report = fuse_entities(
        ocr, [(_ref(page=1, uuid="U" * 26), m1), (_ref(page=3, uuid="V" * 26), m2)]
    )
    assert report.fused == 2
    assert fused[0]["invoiceNumber"] == "A-1"
    assert fused[1]["invoiceNumber"] == "B-2"
    assert [d["entity_index"] for d in report.documents] == [0, 1]


def test_fusion_falls_back_to_invoice_number_when_pages_mismatch():
    ocr = [
        _ocr_entity(page=[7], invoiceNumber="A-1"),
        _ocr_entity(page=[9], invoiceNumber="B-2"),
    ]
    m2 = map_document(_raw(_ubl_invoice(inv_id="B-2"), uuid="V" * 26))
    fused, report = fuse_entities(ocr, [(_ref(page=99, uuid="V" * 26), m2)])
    assert report.fused == 1
    assert report.documents[0]["entity_index"] == 1, "应按发票号配到第二张"


def test_fusion_skips_when_cannot_associate():
    ocr = [_ocr_entity(page=[1], invoiceNumber="A-1"),
           _ocr_entity(page=[2], invoiceNumber="B-2")]
    mapped = map_document(_raw(_ubl_invoice(inv_id="ZZZ-UNKNOWN"), uuid="W" * 26))
    fused, report = fuse_entities(ocr, [(_ref(page=42, uuid="W" * 26), mapped)])
    assert report.fused == 0
    assert fused == ocr, "对不上就不动 OCR 结果"
    assert any(s["action"] == "skipped" for s in report.skipped)


def test_fusion_empty_input_is_noop():
    ocr = [_ocr_entity()]
    fused, report = fuse_entities(ocr, [])
    assert fused == ocr
    assert report.fused == 0
