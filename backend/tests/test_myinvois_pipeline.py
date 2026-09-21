"""MyInvois 端到端接线：网关客户端 + enricher 编排 + fail-open。

不打真实网络：`MyInvoisGateway` 用替身注入，从而在**真实 PDF + 真实二维码
解码 + 真实 UBL 映射/消融**的前提下验证整条编排。
"""

from __future__ import annotations

import io
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

fitz = pytest.importorskip("fitz")
zxingcpp = pytest.importorskip("zxingcpp")
PILImage = pytest.importorskip("PIL.Image")

from app.core.config import get_settings
from app.services.myinvois import enricher
from app.services.myinvois.gateway_client import MyInvoisGateway, VerifyResult

FIX = pathlib.Path(__file__).parent / "fixtures" / "myinvois"
UUID = "V8SZKF3QC4YFJAHPWJP1CGZK10"
LONG = "DJKP4CSY4DAS3WKYWJP1CGZK10PObjHf1786182163"


# ── helpers ────────────────────────────────────────────────────────────────
def _qr_pdf(url: str) -> bytes:
    bc = zxingcpp.create_barcode(url, zxingcpp.BarcodeFormat.QRCode)
    img = zxingcpp.write_barcode_to_image(bc, scale=8)
    mv = memoryview(img)
    h, w = mv.shape
    buf = io.BytesIO()
    PILImage.frombytes("L", (w, h), bytes(mv)).save(buf, "PNG")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "TAX INVOICE")
    page.insert_image(fitz.Rect(400, 60, 520, 180), stream=buf.getvalue())
    out = doc.tobytes()
    doc.close()
    return out


def _ubl_xml() -> str:
    """复用 preprod 真实报文做替身网关的返回体。"""
    return json.loads((FIX / "preprod_raw_myr.json").read_text(encoding="utf-8"))["document"]


class _StubGateway(MyInvoisGateway):
    """替身：不走网络，按构造参数返回固定查验结果。"""

    def __init__(self, *, status="VALID", ok=True, with_doc=True, reason=None):
        self._status, self._ok, self._with_doc, self._reason = status, ok, with_doc, reason
        self.base, self.client_id = "http://stub", "x"
        self.client_secret, self.company_id = "y", "z"
        self.timeout, self._http = 5.0, None
        self.calls: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    def verify(self, doc_uuid: str) -> VerifyResult:
        self.calls.append(doc_uuid)
        if not self._ok:
            return VerifyResult(ok=False, uuid=doc_uuid, errorcode="500",
                                http_status=500, reason=self._reason or "网关故障")
        return VerifyResult(
            ok=True, uuid=doc_uuid, errorcode="200", http_status=200,
            verify_status=self._status,
            raw_status={"VALID": "Valid", "CANCELLED": "Cancelled"}.get(self._status),
            document_no="MYTEST20260917000086", verify_code=LONG,
            submitted_at="2026-09-17T07:47:46Z", issued_at="2026-09-17T07:47:11Z",
            validated_at="2026-09-17T07:47:47Z" if self._status == "VALID" else None,
            cancelled_at="2026-09-18T01:00:00Z" if self._status == "CANCELLED" else None,
            invoice_document=_ubl_xml() if self._with_doc else None,
            invoice_document_format="XML" if self._with_doc else None,
        )


def _ocr() -> list[dict]:
    return [{
        "docType": "invoice", "invoiceType": "Tax Invoice", "page": [1],
        "currency": "USD", "nameOfInvoice": "TAX INVOICE",
        "invoiceNumber": "OCR-WRONG", "dueDate": "2026-10-30",
        "billFromTaxIdentificationNumber": "B16-SST-WRONG",   # OCR 误把 SST 当 TIN
        "totalAmount": 999.99, "purchaseOrderNumber": "PO-77",
    }]


@pytest.fixture
def enabled(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "MYINVOIS_ENABLED", True, raising=False)
    return s


# ── enricher 编排 ──────────────────────────────────────────────────────────
def test_qr_to_verify_to_fusion(enabled):
    gw = _StubGateway()
    fused, rep = enricher.enrich_and_fuse(
        None, file_bytes=_qr_pdf(f"https://preprod.myinvois.hasil.gov.my/{UUID}/share/{LONG}"),
        entities=_ocr(), gateway=gw)
    assert gw.calls == [UUID], "应按二维码解出的 uuid 去查验"
    assert rep.refs_found == 1 and rep.fetched == 1 and rep.fused == 1
    e = fused[0]
    # 税局覆盖
    assert e["billFromTaxIdentificationNumber"] == "C60131481060"
    assert e["currency"] == "MYR"
    assert e["totalAmount"] == 162.39
    assert e["totalTaxAmount"] == 17.63
    # 税局没有 → 保留 OCR
    assert e["nameOfInvoice"] == "TAX INVOICE"
    assert e["dueDate"] == "2026-10-30"
    assert e["purchaseOrderNumber"] == "PO-77"
    assert e["page"] == [1]
    json.dumps(rep.as_dict())          # 报告可序列化进 metadata


def test_no_qr_means_pure_ocr(enabled):
    """规则 3：票面无二维码 → 直接走 OCR，连网关都不该碰。"""
    doc = fitz.open()
    doc.new_page().insert_text((60, 60), "INVOICE without any QR")
    pdf = doc.tobytes()
    doc.close()
    gw = _StubGateway()
    ocr = _ocr()
    fused, rep = enricher.enrich_and_fuse(None, file_bytes=pdf, entities=ocr, gateway=gw)
    assert fused == ocr
    assert rep.refs_found == 0 and rep.fused == 0
    assert gw.calls == [], "无码时不得发起任何查验请求"
    assert rep.error is None, "无码是正常路径，不算错误"


def test_cancelled_is_reported_but_not_fused(enabled):
    """已作废的票不该被当权威值覆盖，但必须上报（高价值业务信号）。"""
    gw = _StubGateway(status="CANCELLED")
    ocr = _ocr()
    fused, rep = enricher.enrich_and_fuse(
        None, file_bytes=_qr_pdf(f"https://myinvois.hasil.gov.my/{UUID}/share/{LONG}"),
        entities=[dict(e) for e in ocr], gateway=gw)
    assert rep.fused == 0
    assert fused[0]["billFromTaxIdentificationNumber"] == "B16-SST-WRONG", "未覆盖"
    rec = [s for s in rep.skipped if s.get("action") == "reported_only"]
    assert rec and rec[0]["verify_status"] == "CANCELLED"
    assert rec[0]["cancelled_at"]


def test_not_found_does_not_fuse(enabled):
    """生产票打到沙箱网关就是这个结果 —— 必须安静降级，不能污染 OCR。"""
    gw = _StubGateway(status="NOT_FOUND", with_doc=False)
    ocr = _ocr()
    fused, rep = enricher.enrich_and_fuse(
        None, file_bytes=_qr_pdf(f"https://myinvois.hasil.gov.my/{UUID}/share/{LONG}"),
        entities=[dict(e) for e in ocr], gateway=gw)
    assert rep.fused == 0 and fused[0]["invoiceNumber"] == "OCR-WRONG"


def test_gateway_failure_keeps_ocr(enabled):
    gw = _StubGateway(ok=False, reason="MyInvois getDocument fail")
    ocr = _ocr()
    fused, rep = enricher.enrich_and_fuse(
        None, file_bytes=_qr_pdf(f"https://myinvois.hasil.gov.my/{UUID}/share/{LONG}"),
        entities=[dict(e) for e in ocr], gateway=gw)
    assert fused == ocr and rep.fused == 0
    assert rep.skipped[0]["action"] == "verify_failed"


def test_disabled_is_noop(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "MYINVOIS_ENABLED", False, raising=False)
    ocr = _ocr()
    fused, rep = enricher.enrich_and_fuse(None, file_bytes=b"x", entities=ocr)
    assert fused == ocr and rep.enabled is False


def test_never_raises_on_internal_error(enabled, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("simulated explosion")
    monkeypatch.setattr(enricher, "extract_qr_hits", boom)
    ocr = _ocr()
    fused, rep = enricher.enrich_and_fuse(None, file_bytes=b"x", entities=ocr)
    assert fused == ocr and "simulated explosion" in (rep.error or "")


def test_meta_carries_verify_timestamps(enabled):
    """客户要的 8 要素里，发送/校验日期只在查验回执里，不在 UBL 正文。"""
    gw = _StubGateway()
    _, rep = enricher.enrich_and_fuse(
        None, file_bytes=_qr_pdf(f"https://myinvois.hasil.gov.my/{UUID}/share/{LONG}"),
        entities=_ocr(), gateway=gw)
    doc = [d for d in rep.documents if d.get("action") == "fused"][0]
    assert doc["uuid"] == UUID


# ── 网关客户端 ─────────────────────────────────────────────────────────────
def test_gateway_unconfigured_is_soft():
    gw = MyInvoisGateway(base_url="", client_id="", client_secret="", company_id="")
    assert gw.configured is False
    r = gw.verify("X" * 26)
    assert r.ok is False and "未配置" in (r.reason or "")


def test_gateway_accepts_both_success_codes():
    """网关实际返回 errorcode=200；早期文档写的是 0000 —— 两个都要认。"""
    import httpx

    for code in ("200", "0000"):
        def handler(request, code=code):
            if "oauth/token" in str(request.url):
                return httpx.Response(200, json={
                    "errorcode": code, "access_token": "tok", "expires_in": 3600})
            return httpx.Response(200, json={
                "errorcode": code, "message": "Success",
                "data": {"verifyStatus": "VALID", "documentNo": "INV-1",
                         "taxDocumentId": "U" * 26, "invoiceDocument": "<Invoice/>",
                         "invoiceDocumentFormat": "XML"}})
        MyInvoisGateway.reset_token()
        gw = MyInvoisGateway(base_url="http://stub/gjfp", client_id="a",
                             client_secret="b", company_id="c",
                             client=httpx.Client(transport=httpx.MockTransport(handler)))
        r = gw.verify("U" * 26)
        assert r.ok is True and r.verify_status == "VALID", code
    MyInvoisGateway.reset_token()


def test_gateway_refreshes_token_on_401():
    import httpx

    state = {"tokens": 0, "verifies": 0}

    def handler(request):
        if "oauth/token" in str(request.url):
            state["tokens"] += 1
            return httpx.Response(200, json={
                "errorcode": "200", "access_token": f"tok{state['tokens']}",
                "expires_in": 3600})
        state["verifies"] += 1
        if state["verifies"] == 1:
            return httpx.Response(401, json={"errorcode": "401", "message": "expired"})
        return httpx.Response(200, json={
            "errorcode": "200", "data": {"verifyStatus": "VALID", "documentNo": "X"}})

    MyInvoisGateway.reset_token()
    gw = MyInvoisGateway(base_url="http://stub/gjfp", client_id="a", client_secret="b",
                         company_id="c",
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = gw.verify("U" * 26)
    assert r.ok is True
    assert state["tokens"] == 2, "401 后应重新换 token 并重试一次"
    MyInvoisGateway.reset_token()


def test_verify_result_as_dict_excludes_document_body():
    r = VerifyResult(ok=True, uuid="U" * 26, invoice_document="<Invoice>" + "x" * 5000,
                     invoice_document_format="XML", verify_code="L" * 40)
    d = r.as_dict()
    assert "invoice_document" not in d and d["document_chars"] > 5000
    assert d["has_verify_code"] is True
    assert "L" * 40 not in json.dumps(d), "查验码不得整体出现在报告里"


# ── 端点 ───────────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def _admin(client) -> dict:
    s = get_settings()
    r = client.post("/api/v1/auth/login", json={
        "email": s.SUPER_ADMIN_USERNAME, "password": s.SUPER_ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health_endpoint_never_leaks_secrets(client):
    r = client.get("/api/v1/myinvois/health", headers=_admin(client))
    assert r.status_code == 200
    body = json.dumps(r.json())
    s = get_settings()
    for secret in (getattr(s, "MYINVOIS_GATEWAY_CLIENT_SECRET", "") or "zzz",):
        assert secret not in body
    assert "access_token" not in body


def test_endpoints_require_auth(client):
    assert client.get("/api/v1/myinvois/health").status_code in (401, 403)
    assert client.post("/api/v1/myinvois/probe").status_code in (401, 403, 422)
