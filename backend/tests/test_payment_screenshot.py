"""`docType: paymentScreenshot` —— 支付截图类型（国家模板 §1.1.2）。

场景：报销里夹着大量手机支付截图（通行费卡扣费、信用卡/银行卡/电子钱包
单笔付款详情页）。它们不是商户开出的票据，按旧规则整份判 `other` → 触发
A0704/4009「未识别到有效票据」，对接方拿不到任何可用信息。

这类截图能可靠提供的只有四个值：**日期 / 币种 / 金额 / 流水号**。
其中流水号不读屏（屏上参考号未必存在、且易误读），用平台按文件注入的
`sourceFileHash`——确定性、100% 有值、绝不幻觉。

本文件锁三件事：
  1. mapper 把 paymentScreenshot 裁剪到白名单字段（模型多吐的一律丢弃）；
  2. paymentScreenshot **算「取到票据」**，不得被判成 A0704/4009；
  3. 国家模板的 schema 分支与正文规则齐备（字段集、必填、正数约定）。
"""

from __future__ import annotations

import pytest

from app.services import open_api_mapper as mapper


def _entity(**over) -> dict:
    e = {
        "docType": "paymentScreenshot",
        "page": [1],
        "invoiceDate": "2026-07-15",
        "currency": "MYR",
        "totalAmount": 27.0,
    }
    e.update(over)
    return e


# ── 1. mapper 字段裁剪 ────────────────────────────────────────────────────────

def test_keeps_the_four_values():
    """日期 / 币种 / 金额 / 流水号（sourceFileHash）必须原样保留。"""
    out = mapper.map_entity(_entity(), source_file_hash="abc123")
    basic = out["header"]["basic"]
    assert basic["docType"] == "paymentScreenshot"
    assert basic["invoiceDate"] == "2026-07-15"
    assert basic["currency"] == "MYR"
    assert basic["totalAmount"] == "27.0"
    assert basic["sourceFileHash"] == "abc123", "流水号即 sourceFileHash"
    assert basic["page"] == ["1"]


def test_drops_fields_the_model_leaked():
    """屏幕上常有商户名/卡号/参考号，但本类型刻意不承载——模型多吐也要丢掉，
    否则下游会把一张扣费截图当成可入账的票据。"""
    out = mapper.map_entity(
        _entity(
            billFromName="Seeds Hotels Premier Sri Petaling",
            invoiceNumber="20260715TNGDMYNB030OQR30238100",
            totalTaxAmount=1.62,
            totalNetAmount=25.38,
            billFromBusinessRegistrationNumber="123456-A",
            detailOfGoodsOrServices=[{"articleName": "Parking", "netAmount": 27.0}],
        ),
        source_file_hash="h",
    )
    assert out["header"]["billFrom"]["billFromName"] == ""
    assert out["header"]["basic"]["invoiceNumber"] == ""
    assert out["header"]["basic"]["totalTaxAmount"] == ""
    assert out["header"]["basic"]["totalNetAmount"] == ""
    assert out["detail"]["detailOfGoodsOrServices"] == []


def test_other_branch_untouched():
    """裁剪逻辑按 docType 分流，不能误伤 other（只留 docType/page/hash）。"""
    out = mapper.map_entity(
        {"docType": "other", "page": [2], "totalAmount": 800},
        source_file_hash="h",
    )
    assert out["header"]["basic"]["totalAmount"] == ""


def test_invoice_branch_untouched():
    """正常发票不受影响——新分支不得改动既有映射。"""
    out = mapper.map_entity(
        {"docType": "invoice", "page": [1], "invoiceNumber": "INV-1",
         "billFromName": "ACME", "totalAmount": 10},
        source_file_hash="h",
    )
    assert out["header"]["basic"]["invoiceNumber"] == "INV-1"
    assert out["header"]["billFrom"]["billFromName"] == "ACME"


# ── 2. 不得被判成「没有票据」 ────────────────────────────────────────────────

def test_payment_screenshot_is_a_real_result():
    """整份只有支付截图时必须返回成功，而不是 A0704/4009 ——
    这正是引入该类型的目的。"""
    payload = mapper.build_response(
        [_entity()], trace_id="t", doc_pages=1, source_file_hash="h")
    assert mapper.has_no_billing_entity(payload) is False


def test_all_other_still_fails():
    """对照组：全 other 仍判「没有票据」，旧行为不能被这次改动冲掉。"""
    payload = mapper.build_response(
        [{"docType": "other", "page": [1]}],
        trace_id="t", doc_pages=1, source_file_hash="h")
    assert mapper.has_no_billing_entity(payload) is True


def test_mixed_screenshot_and_other_succeeds():
    payload = mapper.build_response(
        [{"docType": "other", "page": [1]}, _entity(page=[2])],
        trace_id="t", doc_pages=2, source_file_hash="h")
    assert mapper.has_no_billing_entity(payload) is False


# ── 3. 国家模板：schema 分支与正文规则 ───────────────────────────────────────

@pytest.fixture(scope="module")
def my_template():
    from app.ocr_optimizer.service import template_loader
    return template_loader.load_country_template("MY")


def _branch(raw) -> dict:
    for b in raw["prompt_template"]["json_schema"]["items"]["anyOf"]:
        if "paymentScreenshot" in (b["properties"]["docType"].get("enum") or []):
            return b
    raise AssertionError("模板里找不到 paymentScreenshot 分支")


def test_schema_branch_exposes_exactly_the_four(my_template):
    """schema 分支只声明这四个值 + page，多一个都可能被模型填出来。"""
    props = set(_branch(my_template)["properties"])
    assert props == {"docType", "page", "invoiceDate", "currency", "totalAmount"}


def test_schema_branch_requires_the_values(my_template):
    """三个值缺一就不该判成支付截图（正文 §1.4 同款要求）。"""
    req = set(_branch(my_template)["required"])
    assert req == {"docType", "page", "invoiceDate", "currency", "totalAmount"}


def test_amount_is_positive_not_credit_note(my_template):
    """钱包里的负号是「扣款」，不是贷项——不能被 Credit Note 取负规则捕获。"""
    desc = _branch(my_template)["properties"]["totalAmount"]["description"]
    assert "positive" in desc.lower()
    assert "Credit Note" in desc


def test_body_states_the_three_conditions(my_template):
    body = my_template["prompt_template"]["prompt_format"]
    assert "### 1.1.2" in body
    for cue in ("portrait", "toll card", "exactly one payment"):
        assert cue.lower() in body.lower(), f"§1.1.2 缺少判据线索: {cue}"
    # 与 §1.0.5 的「流水表整页 → other」必须留有明确分界，否则两条规则会打架
    sect = body.split("### 1.1.2", 1)[1].split("## 1.2", 1)[0].lower()
    assert "scrollable list" in sect and "§1.0.5" in sect, (
        "§1.1.2 必须写明「多笔流水列表不属本类型、仍归 other」")
    # 反向：§1.0.5 也要指回来，否则模型只看到「银行卡单条 → other」会漏判
    assert "§1.1.2" in body.split("### 1.0.5", 1)[1].split("### 1.0.6", 1)[0]
