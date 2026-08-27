"""整份文档一张票据都没识别出来 → 判失败，而不是返回一份空壳成功。

背景：2026-08-26 下午的 203 个异步任务里有 39 个（19%）返回 `errcode=0000`
但 `data` 里每一条 `docType` 都是 `other` —— 对接方拿到的是「成功」，
实际上什么都没识别到，只能自己去数 docType。这里把判定挪到服务端。

暂定码：同步 4009 / 异步 A0704（尚未与对接方确认，见两处常量的注释）。
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import open_api
from app.core.config import get_settings
from app.main import app
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.async_task import AsyncTask, TaskStatus
from app.models.open_api_client import OpenApiClient
from app.services import async_task_service as tasksvc
from app.services import open_api_mapper as mapper
from app.services import open_api_auth as auth
from tests.conftest import minimal_pdf

CLIENT_ID = "TEST_NoInvoiceClient"
SECRET = "no-invoice-secret-123"
TEMPLATE_ID = 9917


# ── 判定谓词 ─────────────────────────────────────────────────────────────────

def _entity(doc_type):
    return {"header": {"basic": {"docType": doc_type} if doc_type else {}}}


@pytest.mark.parametrize("entities, expected", [
    ([], True),                                             # 模型什么都没返回
    ([_entity("other")], True),                             # 单页全是 other
    ([_entity("other")] * 25, True),                        # 25 页全是 other
    ([_entity("other"), _entity("receipt")], False),        # 混合：有 receipt
    ([_entity("invoice"), _entity("other")], False),        # 混合：有 invoice
    ([_entity("receipt")], False),
    ([_entity(None)], True),                                # 连 docType 都没有
    ([_entity(None), _entity("invoice")], False),
])
def test_has_no_billing_entity(entities, expected):
    assert mapper.has_no_billing_entity({"data": entities}) is expected


def test_missing_data_key_counts_as_no_billing_entity():
    assert mapper.has_no_billing_entity({}) is True
    assert mapper.has_no_billing_entity({"data": None}) is True


# ── 同步端点 ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def seeded(db_session):
    import hashlib
    import uuid

    if db_session.query(OpenApiClient).filter(
            OpenApiClient.client_id == CLIENT_ID).first() is None:
        db_session.add(OpenApiClient(
            id=uuid.uuid4(), client_id=CLIENT_ID, client_secret=SECRET,
            client_secret_hash=hashlib.sha256(SECRET.encode()).hexdigest(),
            name=CLIENT_ID, tenant_id=None, is_active=True,
        ))

    api = db_session.query(ApiDefinition).filter(
        ApiDefinition.external_template_id == TEMPLATE_ID).first()
    if api is None:
        api = ApiDefinition(
            id=uuid.uuid4(), name="test-no-invoice",
            api_code=f"test-no-invoice-{uuid.uuid4().hex[:6]}",
            description="", status=ApiDefinitionStatus.active.value, version=1,
            response_schema={"type": "ARRAY"}, processor_type="mock",
            model_name="mock", external_template_id=TEMPLATE_ID, tenant_id=None,
        )
        db_session.add(api)
    db_session.commit()
    return {"api": api}


@pytest.fixture(autouse=True)
def _no_result_cache(monkeypatch):
    """关掉结果缓存：本文件所有用例用的是同一份 minimal_pdf 字节，缓存按内容
    sha256 去重，会把第二条起的请求短路成上一条的结果（实测：全 other 的用例
    先跑，后面的用例全被喂回它的响应）。缓存本身由下面那条用例单独验。"""
    monkeypatch.setattr(get_settings(), "EXTRACT_CACHE_ENABLED", False,
                        raising=False)


def _token(client: TestClient) -> str:
    import hashlib
    import time

    ts = int(time.time())
    sign = hashlib.md5(f"{CLIENT_ID}{SECRET}{ts}".encode()).hexdigest()
    r = client.post("/base/oauth/token", json={
        "client_id": CLIENT_ID, "timestamp": ts, "sign": sign})
    return r.json()["access_token"]


def _analyze(client: TestClient, token: str, name: str):
    return client.post(
        f"/ai/knowledge/nlpService/document/analyze?access_token={token}",
        data={"templateId": str(TEMPLATE_ID), "fileHash": name,
              "clientId": CLIENT_ID},
        files={"file": (f"{name}.pdf", io.BytesIO(minimal_pdf()),
                        "application/pdf")})


def _stub_structured(monkeypatch, structured):
    from app.services import extract_service as svc
    monkeypatch.setattr(svc, "_run_processor",
                        lambda **kw: ({}, structured, "mock", None))


def test_sync_all_other_returns_4009_not_empty_success(
        client, seeded, monkeypatch):
    """全 other 时同步端点返 4009，而不是 errcode=0000 + 一堆空壳。"""
    _stub_structured(monkeypatch, [{"docType": "other"} for _ in range(3)])

    body = _analyze(client, _token(client), "all-other").json()

    assert body["errcode"] == auth.ERR_NO_INVOICE_FOUND == "4009"
    assert "未识别到有效票据" in body["description"]


def test_sync_one_receipt_among_others_still_succeeds(
        client, seeded, monkeypatch):
    """只要有一张票，其余全是 other 也照常成功——判定的是「一张都没有」。"""
    _stub_structured(monkeypatch, [
        {"docType": "other"},
        {"docType": "receipt", "totalAmount": 12.5, "currency": "MYR"},
        {"docType": "other"},
    ])

    body = _analyze(client, _token(client), "one-receipt").json()

    assert body["errcode"] == "0000"
    assert len(body["data"]) == 3


def test_sync_no_invoice_result_is_not_cached(client, seeded, monkeypatch):
    """空结果不进缓存：否则同一份文件 15 分钟内重传也拿不到重新识别的机会。"""
    from app.services import extraction_cache as xcache

    monkeypatch.setattr(get_settings(), "EXTRACT_CACHE_ENABLED", True,
                        raising=False)
    stored = []
    monkeypatch.setattr(xcache, "store",
                        lambda *a, **kw: stored.append(kw) or None)
    _stub_structured(monkeypatch, [{"docType": "other"}])

    body = _analyze(client, _token(client), "no-cache").json()

    assert body["errcode"] == "4009"
    assert stored == [], "未识别到票据的结果不应写入结果缓存"


def test_sync_switch_off_restores_empty_shell_success(
        client, seeded, monkeypatch):
    """开关置 False 时逐字节回到旧行为（0000 + 空壳），保证可一键回退。"""
    monkeypatch.setattr(get_settings(), "FAIL_WHEN_NO_INVOICE_FOUND", False,
                        raising=False)
    _stub_structured(monkeypatch, [{"docType": "other"}])

    body = _analyze(client, _token(client), "switch-off").json()

    assert body["errcode"] == "0000"
    assert len(body["data"]) == 1


# ── 异步端点 ─────────────────────────────────────────────────────────────────

def _fake_task(db) -> AsyncTask:
    import uuid
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    task = AsyncTask(
        id=str(uuid.uuid4()), client_id=CLIENT_ID, template_id=str(TEMPLATE_ID),
        file_name="x.pdf", file_hash="h", content_hash="c", spool_path="",
        page_count=3, status=TaskStatus.RUNNING,
        started_at=now, expires_at=now + timedelta(days=10),
    )
    db.add(task)
    db.commit()
    return task


def test_async_all_other_marks_task_failed(db_session, seeded):
    from app.services import async_task_worker as worker

    task = _fake_task(db_session)
    api_def = db_session.query(ApiDefinition).filter_by(
        external_template_id=TEMPLATE_ID).first()

    worker._finalize_completed(
        db_session, task, api_def,
        [{"docType": "other"}, {"docType": "other"}], 3)

    db_session.refresh(task)
    assert task.status == TaskStatus.FAILED
    assert task.errcode == tasksvc.ERR_NO_INVOICE_FOUND == "A0704"
    assert "未识别到有效票据" in (task.error_message or "")
    assert task.result_json in (None, ""), "失败任务不应带 result"


def test_async_no_invoice_does_not_consume_retries(db_session, seeded):
    """这是终局判定不是异常：直接 FAILED，不退回 PENDING 重跑烧配额。"""
    from app.services import async_task_worker as worker

    task = _fake_task(db_session)
    api_def = db_session.query(ApiDefinition).filter_by(
        external_template_id=TEMPLATE_ID).first()

    worker._finalize_completed(
        db_session, task, api_def, [{"docType": "other"}], 1)

    db_session.refresh(task)
    assert task.status == TaskStatus.FAILED
    assert (task.retry_count or 0) == 0


def test_async_mixed_document_still_completes(db_session, seeded):
    from app.services import async_task_worker as worker

    task = _fake_task(db_session)
    api_def = db_session.query(ApiDefinition).filter_by(
        external_template_id=TEMPLATE_ID).first()

    worker._finalize_completed(
        db_session, task, api_def,
        [{"docType": "other"}, {"docType": "invoice", "invoiceNumber": "A1"}], 2)

    db_session.refresh(task)
    assert task.status == TaskStatus.COMPLETED
    assert json.loads(task.result_json)["errcode"] == "0000"


def test_async_legacy_errcode_points_at_the_sync_twin():
    """两套码在同一服务里并存；legacyErrcode 必须指向同步侧的对应码。"""
    assert tasksvc.LEGACY_OF[tasksvc.ERR_NO_INVOICE_FOUND] == auth.ERR_NO_INVOICE_FOUND


# ── other 记录不得携带金额（防重复计账）────────────────────────────────────────

def test_other_entity_carries_no_amount():
    """`docType: other` 只保留 docType / page —— 模型多吐的金额一律丢弃。

    背景：2026-08-27 实测一份 MY 文档「第1页 SEBUTHARGA 报价单 + 第2页 INVOIS 发票」，
    两页金额同为 RM800。报价单按 §1.1.1 判 other，但 qwen 仍把 totalAmount=800
    吐在 other 记录上；下游若不按 docType 过滤就会把同一笔钱加两遍。
    schema 的 other 分支本就只声明 docType/page，这里做确定性兜底。
    """
    out = mapper.map_entity({
        "docType": "other", "page": [1],
        "totalAmount": 800, "currency": "MYR", "nameOfInvoice": "SEBUTHARGA",
        "billFromName": "G CONCEPT EMPIRE", "invoiceNumber": "QUO-(07/26)(2)",
        "detailOfGoodsOrServices": [{"articleName": "Menu Petang", "netAmount": 750}],
    }, source_file_hash="h1")

    basic = out["header"]["basic"]
    assert basic["docType"] == "other"
    assert basic["page"] == "[1]" or basic["page"] == [1] or "1" in str(basic["page"])
    assert basic["totalAmount"] == "", "other 记录不得带金额"
    assert basic["nameOfInvoice"] == ""
    assert basic["currency"] == ""
    assert out["header"]["billFrom"]["billFromName"] == ""
    assert out["detail"]["detailOfGoodsOrServices"] == [], "other 记录不得带明细"
    assert basic["sourceFileHash"] == "h1", "sourceFileHash 是平台注入的溯源字段，须保留"


def test_invoice_entity_keeps_everything():
    """只影响 other —— invoice/receipt 一个字段都不能少。"""
    out = mapper.map_entity({
        "docType": "invoice", "page": [2], "totalAmount": 800,
        "currency": "MYR", "nameOfInvoice": "INVOIS",
        "billFromName": "G CONCEPT EMPIRE",
        "detailOfGoodsOrServices": [{"articleName": "Menu Petang", "netAmount": 750}],
    }, source_file_hash="h1")
    basic = out["header"]["basic"]
    assert basic["totalAmount"] == "800"
    assert basic["nameOfInvoice"] == "INVOIS"
    assert out["header"]["billFrom"]["billFromName"] == "G CONCEPT EMPIRE"
    assert len(out["detail"]["detailOfGoodsOrServices"]) == 1


def test_quotation_plus_invoice_totals_only_once():
    """端到端语义：报价单+发票同一 PDF，全量求和只应得到 800 而不是 1600。"""
    payload = mapper.build_response(
        [{"docType": "other", "page": [1], "totalAmount": 800, "currency": "MYR"},
         {"docType": "invoice", "page": [2], "totalAmount": 800, "currency": "MYR"}],
        trace_id="t" * 16, doc_pages=2, source_file_hash="h")
    total = sum(float(((e.get("header") or {}).get("basic") or {}).get("totalAmount") or 0)
                for e in payload["data"])
    assert total == 800.0, f"重复计账：合计 {total}，应为 800"
    assert mapper.has_no_billing_entity(payload) is False, "有一张真发票，不算全 other"
