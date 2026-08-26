"""开放平台异步接口契约测试。

对齐《异步文档处理 API 文档》：申请返 taskId、轮询取结果、A 系错误码、
批量上限 10、重启恢复。用 mock processor，零 token 消耗。

三处与同步接口的差异是最容易写错的地方，各有专门用例锁死：
  1. 申请接口的响应**没有 traceId / docPages**，data 是对象不是数组；
  2. 查询接口的 result 是**字符串**（同步响应的 JSON 文本），不是对象；
  3. 错误码是 A 系，不是同步的 4xxx。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import minimal_pdf
from fastapi.testclient import TestClient

from app.api.v1 import open_api
from app.main import app
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.async_task import AsyncTask, TaskStatus
from app.models.open_api_client import OpenApiClient
from app.services import async_task_service as tasksvc
from app.services.task_result_cache import reset_cache

CLIENT_ID = "TEST_AsyncClient01"
SECRET = "async-secret-xyz789"
TEMPLATE_ID = 9908

OTHER_CLIENT_ID = "TEST_AsyncClient02"
OTHER_SECRET = "async-secret-other"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated():
    """每个用例从干净的缓存 + 空任务表开始。

    这些用例会 commit（要走真实的 HTTP 路径），`db_session` 的 rollback 收不回来，
    残留的 PENDING 行会让 claim_next 抢到上个用例的任务（它按 created_at 取最老的，
    这是正确的 FIFO 行为，只是测试不能假设抢到的就是自己刚提交的那个）。
    """
    from app.core.database import SessionLocal

    def _wipe():
        s = SessionLocal()
        try:
            from app.models.extraction_cache import ExtractionCache
            for row in s.query(ExtractionCache).all():
                s.delete(row)
            for row in s.query(AsyncTask).all():
                if row.spool_path:
                    try:
                        os.unlink(row.spool_path)
                    except OSError:
                        pass
                s.delete(row)
            s.commit()
        finally:
            s.close()

    reset_cache()
    _wipe()
    yield
    _wipe()
    reset_cache()


@pytest.fixture()
def seeded(db_session):
    """两个 client（用于验隔离）+ 一个挂了 external_template_id 的 active API。"""
    for cid, sec in ((CLIENT_ID, SECRET), (OTHER_CLIENT_ID, OTHER_SECRET)):
        if db_session.query(OpenApiClient).filter(
                OpenApiClient.client_id == cid).first() is None:
            db_session.add(OpenApiClient(
                id=uuid.uuid4(), client_id=cid, client_secret=sec,
                client_secret_hash=hashlib.sha256(sec.encode()).hexdigest(),
                name=cid, tenant_id=None, is_active=True,
            ))

    api = db_session.query(ApiDefinition).filter(
        ApiDefinition.external_template_id == TEMPLATE_ID).first()
    if api is None:
        api = ApiDefinition(
            id=uuid.uuid4(), name="test-async-api",
            api_code=f"test-async-{uuid.uuid4().hex[:6]}",
            description="", status=ApiDefinitionStatus.active.value, version=1,
            response_schema={"type": "ARRAY"}, processor_type="mock",
            model_name="mock", external_template_id=TEMPLATE_ID, tenant_id=None,
        )
        db_session.add(api)
    db_session.commit()
    return {"api": api}


def _token(client: TestClient, *, client_id=CLIENT_ID, secret=SECRET) -> str:
    ts = str(int(time.time()))
    sign = hashlib.md5(f"{client_id}{secret}{ts}".encode()).hexdigest()
    r = client.post("/base/oauth/token", json={
        "client_id": client_id, "timestamp": ts, "sign": sign})
    return r.json()["access_token"]


def _submit(client: TestClient, token: str, *, template_id=str(TEMPLATE_ID), **extra):
    data = {"templateId": template_id, "fileHash": "hash-abc", **extra}
    return client.post(
        f"{open_api.ANALYZE_ASYNC_PATH}?access_token={token}",
        headers={"client-platform": "common"},
        data=data,
        files={"file": ("doc.pdf", io.BytesIO(minimal_pdf()), "application/pdf")},
    )


def _query(client: TestClient, token: str, task_ids: list[str]):
    return client.post(
        f"{open_api.TASKS_QUERY_PATH}?access_token={token}",
        headers={"client-platform": "common"},
        json={"taskIds": task_ids},
    )


# ── 申请接口 ─────────────────────────────────────────────────────────────────

def test_submit_returns_task_id_in_documented_envelope(client, seeded):
    """外壳逐字对齐文档：data 是对象、含 taskId，且**没有** traceId / docPages。"""
    r = _submit(client, _token(client))
    assert r.status_code == 200
    body = r.json()
    assert body["errcode"] == "0000"
    assert body["description"] == "成功"
    assert isinstance(body["data"], dict)
    uuid.UUID(body["data"]["taskId"])          # 标准 uuid 文本
    assert "traceId" not in body, "申请接口文档里没有 traceId，不要擅自补"
    assert "docPages" not in body, "申请接口文档里没有 docPages，不要擅自补"


def test_submit_carries_legacy_errcode_for_internal_triage(client, seeded):
    """A 系为主，附 legacyErrcode 供内部排查（两套码并存是既定事实）。"""
    body = _submit(client, _token(client)).json()
    assert body["legacyErrcode"] == "0000"


def test_submit_without_token_is_A0301(client, seeded):
    r = client.post(
        open_api.ANALYZE_ASYNC_PATH,
        data={"templateId": str(TEMPLATE_ID)},
        files={"file": ("d.pdf", io.BytesIO(minimal_pdf()), "application/pdf")})
    assert r.status_code == 200
    assert r.json()["errcode"] == tasksvc.ERR_UNAUTHORIZED == "A0301"


def test_submit_without_file_is_A0410(client, seeded):
    r = client.post(
        f"{open_api.ANALYZE_ASYNC_PATH}?access_token={_token(client)}",
        data={"templateId": str(TEMPLATE_ID)})
    assert r.json()["errcode"] == tasksvc.ERR_MISSING_PARAM == "A0410"


def test_submit_unknown_template_is_A0301(client, seeded):
    """越权/不存在的模板在**提交时**就挡掉，不留到 worker 才发现。"""
    r = _submit(client, _token(client), template_id="999999")
    assert r.json()["errcode"] == tasksvc.ERR_UNAUTHORIZED


def test_submit_persists_task_as_pending(client, seeded, db_session):
    task_id = _submit(client, _token(client)).json()["data"]["taskId"]
    row = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    assert row.status == TaskStatus.PENDING
    assert row.client_id == CLIENT_ID
    assert row.file_hash == "hash-abc"
    assert row.spool_path, "文件必须落盘，队列里只放路径"


def test_callback_url_is_stored_but_not_invoked_this_phase(client, seeded, db_session):
    """本期只入库不回调 —— 存下来是为了下期实现时不用重新采集。"""
    task_id = _submit(
        client, _token(client), callbackUrl="https://example.com/cb",
    ).json()["data"]["taskId"]
    row = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    assert row.callback_url == "https://example.com/cb"


# ── 查询接口 ─────────────────────────────────────────────────────────────────

def test_query_returns_map_keyed_by_task_id(client, seeded):
    """文档明确：返回 Map 而非数组，即使只查一个也是 Map。"""
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    body = _query(client, token, [task_id]).json()

    assert body["errcode"] == "0000"
    assert len(body["traceId"]) == 16
    assert isinstance(body["data"], dict)
    entry = body["data"][task_id]
    assert set(entry) == {
        "taskId", "status", "statusDesc", "requestParams", "result", "errorMessage"}
    assert entry["taskId"] == task_id
    assert set(entry["requestParams"]) == {"templateId", "language", "fileName"}


def test_query_never_exposes_internal_running_state(client, seeded, db_session):
    """RUNNING 是内部态，对外必须折叠成 PENDING（文档只定义了三个状态）。"""
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    db_session.query(AsyncTask).filter(AsyncTask.id == task_id).update(
        {"status": TaskStatus.RUNNING})
    db_session.commit()

    entry = _query(client, token, [task_id]).json()["data"][task_id]
    assert entry["status"] == "PENDING", "RUNNING 不该出现在对外响应里"
    assert entry["statusDesc"] == "待处理"


def test_completed_result_is_a_json_string_not_an_object(client, seeded, db_session):
    """文档规定 result 是**字符串**，调用方自行解析。返对象会让对接方解析失败。"""
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    tasksvc.mark_completed(db_session, task, {"errcode": "0000", "data": [{"x": 1}]})

    entry = _query(client, token, [task_id]).json()["data"][task_id]
    assert entry["status"] == "COMPLETED"
    assert entry["statusDesc"] == "已完成"
    assert isinstance(entry["result"], str), "result 必须是字符串"
    assert json.loads(entry["result"])["data"] == [{"x": 1}]
    assert entry["errorMessage"] is None


def test_failed_task_reports_error_message_and_no_result(client, seeded, db_session):
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    tasksvc.mark_failed(db_session, task, tasksvc.ERR_RPC_FAILED, "模型超时")

    entry = _query(client, token, [task_id]).json()["data"][task_id]
    assert entry["status"] == "FAILED"
    assert entry["errorMessage"] == "模型超时"
    assert entry["result"] is None


def test_query_missing_task_ids_is_A0410(client, seeded):
    r = client.post(
        f"{open_api.TASKS_QUERY_PATH}?access_token={_token(client)}", json={})
    assert r.json()["errcode"] == tasksvc.ERR_MISSING_PARAM


def test_query_over_ten_task_ids_is_A0426(client, seeded):
    """文档：最多 10 个。第 11 个必须拒，否则一次查询能拖垮单 worker 服务。"""
    ids = [str(uuid.uuid4()) for _ in range(11)]
    body = _query(client, _token(client), ids).json()
    assert body["errcode"] == tasksvc.ERR_BATCH_TOO_LARGE == "A0426"

    ids10 = ids[:10]
    assert _query(client, _token(client), ids10).json()["errcode"] == "0000"


def test_query_without_token_is_A0301(client, seeded):
    r = client.post(open_api.TASKS_QUERY_PATH, json={"taskIds": [str(uuid.uuid4())]})
    assert r.json()["errcode"] == tasksvc.ERR_UNAUTHORIZED


def test_unknown_task_id_is_silently_absent(client, seeded):
    """查不到的 id 不报错也不出现在 map 里 —— 不泄露'这个 id 存在'。"""
    body = _query(client, _token(client), [str(uuid.uuid4())]).json()
    assert body["errcode"] == "0000"
    assert body["data"] == {}


def test_client_cannot_read_another_clients_task(client, seeded):
    """租户隔离：文档没写，但缺了它谁都能凭 taskId 读走别人的识别结果。"""
    token_a = _token(client)
    task_id = _submit(client, token_a).json()["data"]["taskId"]

    token_b = _token(client, client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET)
    body = _query(client, token_b, [task_id]).json()
    assert body["errcode"] == "0000"
    assert task_id not in body["data"], "不该能查到别的 client 的任务"


def test_cache_hit_still_enforces_isolation(client, seeded, db_session):
    """缓存命中不能成为绕过隔离的后门 —— 终态结果正是被缓存的那部分。"""
    token_a = _token(client)
    task_id = _submit(client, token_a).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    tasksvc.mark_completed(db_session, task, {"errcode": "0000", "data": []})

    # A 查一次把它焐进缓存
    assert task_id in _query(client, token_a, [task_id]).json()["data"]

    token_b = _token(client, client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET)
    assert task_id not in _query(client, token_b, [task_id]).json()["data"]


# ── 轮询审计日志 ─────────────────────────────────────────────────────────────
# 访问日志只有 URL，taskId 在 JSON body 里 —— 没有这行结构化日志就无法回答
# 「结果是什么时候被取走的」。下面按「能被脚本解析」来断言，而不只是"有输出"。

def test_query_logs_delivered_task_ids(client, seeded, db_session, caplog):
    """终态任务要出现在 delivered 段，并带上状态。"""
    token = _token(client)
    done_id = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == done_id).one()
    tasksvc.mark_completed(db_session, task, {"errcode": "0000", "data": []})

    with caplog.at_level(logging.INFO, logger="app.api.v1.open_api"):
        _query(client, token, [done_id])

    line = next(r.getMessage() for r in caplog.records if "tasks/query" in r.getMessage())
    assert f"delivered=[{done_id[:8]}:COMPLETED]" in line
    assert "pending=[]" in line
    assert "missing=0" in line
    assert f"client={CLIENT_ID}" in line


def test_query_log_separates_pending_and_missing(client, seeded, caplog):
    """处理中 / 查不到 要分别归位，否则报告会把三类混成一类。"""
    token = _token(client)
    pending_id = _submit(client, token).json()["data"]["taskId"]
    ghost = str(uuid.uuid4())

    with caplog.at_level(logging.INFO, logger="app.api.v1.open_api"):
        _query(client, token, [pending_id, ghost])

    line = next(r.getMessage() for r in caplog.records if "tasks/query" in r.getMessage())
    assert "delivered=[]" in line
    assert f"pending=[{pending_id[:8]}]" in line
    assert "missing=1" in line
    assert "n=2" in line


def test_query_log_counts_foreign_task_as_missing(client, seeded, caplog):
    """别的 client 的任务算 missing —— 与"不存在"同等对待，不泄露存在性。"""
    token_a = _token(client)
    tid = _submit(client, token_a).json()["data"]["taskId"]
    token_b = _token(client, client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET)

    with caplog.at_level(logging.INFO, logger="app.api.v1.open_api"):
        _query(client, token_b, [tid])

    line = next(r.getMessage() for r in caplog.records if "tasks/query" in r.getMessage())
    assert "missing=1" in line
    assert tid[:8] not in line, "他人任务的 id 不该出现在日志里"


# ── 结果缓存 / 慢任务留档 ────────────────────────────────────────────────────

def test_second_submit_of_same_file_completes_without_model_call(
        client, seeded, db_session):
    """同一份文件二次提交：仍发 taskId，但直接 COMPLETED、零模型调用。"""
    from app.services import extraction_cache as xcache

    token = _token(client)
    first = _submit(client, token).json()["data"]["taskId"]
    t1 = db_session.query(AsyncTask).filter(AsyncTask.id == first).one()
    assert t1.content_hash, "服务端必须自算 content_hash（对接方基本不传 fileHash）"
    tasksvc.mark_completed(db_session, t1, {
        "errcode": "0000", "description": "Success", "traceId": "t" * 16,
        "docPages": 1, "data": []})

    second = _submit(client, token).json()["data"]["taskId"]
    t2 = db_session.query(AsyncTask).filter(AsyncTask.id == second).one()
    assert second != first, "仍然是一个新任务，对接方流程不变"
    assert t2.status == TaskStatus.COMPLETED, "命中缓存应直接终态"
    assert t2.spool_path == "", "不需要识别就不该落盘"
    assert t2.result_json

    entry = _query(client, token, [second]).json()["data"][second]
    assert entry["status"] == "COMPLETED"
    assert json.loads(entry["result"])["errcode"] == "0000"


def test_cache_hit_does_not_cross_clients(client, seeded, db_session):
    """B 提交同一份文件不得命中 A 的缓存（会泄露"别人传过这份文件"）。"""
    token_a = _token(client)
    tid = _submit(client, token_a).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == tid).one()
    tasksvc.mark_completed(db_session, task, {
        "errcode": "0000", "description": "Success", "traceId": "t" * 16,
        "docPages": 1, "data": []})

    token_b = _token(client, client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET)
    other = _submit(client, token_b).json()["data"]["taskId"]
    row = db_session.query(AsyncTask).filter(AsyncTask.id == other).one()
    assert row.status == TaskStatus.PENDING, "别家的缓存不该被命中"


def test_slow_task_keeps_its_source_file(client, seeded, db_session, monkeypatch):
    """识别超过阈值的任务保留原件 —— 事后能单独重跑，判断是文件慢还是排队慢。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "SLOW_TASK_KEEP_SEC", 0.0, raising=False)
    token = _token(client)
    tid = _submit(client, token).json()["data"]["taskId"]
    task = tasksvc.claim_next(db_session)
    spool = task.spool_path
    tasksvc.mark_completed(db_session, task, {
        "errcode": "0000", "description": "Success", "traceId": "t" * 16,
        "docPages": 1, "data": []})

    assert task.spool_path == spool, "慢任务的原件不该被删"
    assert os.path.exists(spool)


def test_fast_task_still_drops_its_source_file(client, seeded, db_session, monkeypatch):
    """正常速度的任务照旧删原件 —— 留档只针对慢的，不能变成全留。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "SLOW_TASK_KEEP_SEC", 9999.0, raising=False)
    token = _token(client)
    _submit(client, token)
    task = tasksvc.claim_next(db_session)
    spool = task.spool_path
    tasksvc.mark_completed(db_session, task, {
        "errcode": "0000", "description": "Success", "traceId": "t" * 16,
        "docPages": 1, "data": []})

    assert task.spool_path == ""
    assert not os.path.exists(spool)


def test_purge_stale_spools_clears_kept_files(client, seeded, db_session, monkeypatch):
    """留档过了 TTL 只删文件、不删任务行。"""
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "SLOW_TASK_KEEP_SEC", 0.0, raising=False)
    token = _token(client)
    tid = _submit(client, token).json()["data"]["taskId"]
    task = tasksvc.claim_next(db_session)
    spool = task.spool_path
    tasksvc.mark_completed(db_session, task, {
        "errcode": "0000", "description": "Success", "traceId": "t" * 16,
        "docPages": 1, "data": []})
    assert os.path.exists(spool)

    task.finished_at = datetime.now(timezone.utc) - timedelta(hours=48)
    db_session.commit()
    assert tasksvc.purge_stale_spools(db_session) == 1
    assert not os.path.exists(spool)
    assert db_session.query(AsyncTask).filter(
        AsyncTask.id == tid).one().status == TaskStatus.COMPLETED


# ── 生命周期 ─────────────────────────────────────────────────────────────────

def test_recover_orphans_requeues_interrupted_tasks(client, seeded, db_session):
    """文档第 8 条：服务重启后未完成的任务自动恢复处理。"""
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    db_session.query(AsyncTask).filter(AsyncTask.id == task_id).update(
        {"status": TaskStatus.RUNNING})
    db_session.commit()

    assert tasksvc.recover_orphans(db_session) >= 1
    row = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    assert row.status == TaskStatus.PENDING
    assert row.started_at is None


def test_claim_next_is_atomic(client, seeded, db_session):
    """同一个任务不能被抢两次 —— 抢两次就是同一份文档跑两遍模型。"""
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]

    first = tasksvc.claim_next(db_session)
    assert first is not None and first.id == task_id
    # 已是 RUNNING，第二次不该再抢到同一行
    second = tasksvc.claim_next(db_session)
    assert second is None or second.id != task_id


def test_completed_task_drops_its_spool_file(client, seeded, db_session):
    """终态后删原件 —— 不删的话 10 天的 PDF 会把磁盘吃满。"""
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    spool = task.spool_path
    assert os.path.exists(spool)

    tasksvc.mark_completed(db_session, task, {"errcode": "0000", "data": []})
    assert not os.path.exists(spool)
    assert task.spool_path == ""


def test_retry_keeps_spool_file_for_the_next_attempt(client, seeded, db_session):
    """重试要重新读文件，退回 PENDING 时**不能**删 spool。"""
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()

    assert tasksvc.requeue_for_retry(db_session, task, "模型 503") is True
    assert task.status == TaskStatus.PENDING
    assert task.retry_count == 1
    assert os.path.exists(task.spool_path), "重试还要读它，不能删"


def test_retry_gives_up_after_max_attempts(client, seeded, db_session):
    from app.core.config import get_settings

    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    task.retry_count = get_settings().ASYNC_MAX_RETRIES
    db_session.commit()

    assert tasksvc.requeue_for_retry(db_session, task, "还是失败") is False


def test_purge_expired_removes_rows_and_files(client, seeded, db_session):
    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    spool = task.spool_path
    task.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    assert tasksvc.purge_expired(db_session) >= 1
    assert db_session.query(AsyncTask).filter(AsyncTask.id == task_id).first() is None
    assert not os.path.exists(spool)


# ── 提交配额（防磁盘/队列被单一 client 打满）─────────────────────────────────

def test_per_client_queue_cap_rejects_with_1999(client, seeded, monkeypatch):
    """单 client 排队配额：超限返回 1999，且**不落盘**。"""
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ASYNC_MAX_QUEUE_PER_CLIENT", 2, raising=False)

    # spool 目录是共享的，历史遗留文件不该影响判定 —— 只看本用例的增量
    spool_dir = tasksvc._spool_dir()
    before = set(spool_dir.iterdir())

    token = _token(client)
    assert _submit(client, token).json()["errcode"] == "0000"
    assert _submit(client, token).json()["errcode"] == "0000"
    third = _submit(client, token).json()
    assert third["errcode"] == tasksvc.ERR_FAIL == "1999"
    assert "上限" in third["description"]

    # 超限的提交不留任何字节：只多出前两个成功提交的文件
    assert len(set(spool_dir.iterdir()) - before) == 2


def test_global_queue_cap_rejects_with_1999(client, seeded, monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ASYNC_MAX_QUEUE_DEPTH", 1, raising=False)

    token = _token(client)
    assert _submit(client, token).json()["errcode"] == "0000"
    assert _submit(client, token).json()["errcode"] == tasksvc.ERR_FAIL


def test_terminal_tasks_do_not_count_against_quota(client, seeded, db_session, monkeypatch):
    """配额只数 PENDING+RUNNING——已完成的任务不该挡住新提交。"""
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ASYNC_MAX_QUEUE_PER_CLIENT", 1, raising=False)

    token = _token(client)
    tid = _submit(client, token).json()["data"]["taskId"]
    task = db_session.query(AsyncTask).filter(AsyncTask.id == tid).one()
    tasksvc.mark_completed(db_session, task, {"errcode": "0000", "data": []})

    assert _submit(client, token).json()["errcode"] == "0000"


def test_oversized_upload_rejected_before_spooling(client, seeded, monkeypatch):
    """超大文件在写盘前拦下（A0700）——不能先吃磁盘再拒绝。"""
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "MAX_UPLOAD_SIZE_MB", 0, raising=False)

    spool_dir = tasksvc._spool_dir()
    before = set(spool_dir.iterdir())
    r = _submit(client, _token(client)).json()
    assert r["errcode"] == tasksvc.ERR_UPLOAD_FAILED == "A0700"
    assert set(spool_dir.iterdir()) == before, "被拒的提交不该在磁盘上留字节"


# ── 停机语义（code review 修复回归）──────────────────────────────────────────

async def test_cancel_mid_extraction_is_fast_and_requeues(client, seeded, db_session, monkeypatch):
    """提取中途取消：立即返回（不等线程跑完），任务退回 PENDING 不占重试次数。

    修复前 anyio 默认 abandon_on_cancel=False，stop_worker 会卡满整场识别
    （实测可达 200s），超过 systemd 优雅停机窗口后被 SIGKILL。
    """
    import time as _time

    from app.services import extract_service as svc_mod
    from app.services.async_task_worker import _process_one

    def _slow_extract(db, **kwargs):
        _time.sleep(2.0)  # 模拟一场跑不完的识别（取消后线程被放弃，跑完也无人接收）
        return None

    monkeypatch.setattr(svc_mod, "extract_document", _slow_extract)

    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    claimed = tasksvc.claim_next(db_session)

    job = asyncio.create_task(_process_one(claimed))
    await asyncio.sleep(0.3)  # 让提取真正进到 sleep 里
    t0 = _time.monotonic()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    elapsed = _time.monotonic() - t0

    assert elapsed < 1.0, f"取消耗时 {elapsed:.2f}s——线程没有被放弃，停机会被拖死"
    row = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    db_session.refresh(row)
    assert row.status == TaskStatus.PENDING, "中途取消应退回 PENDING 由下次启动接手"
    assert row.retry_count == 0, "停机不该消耗重试次数"


async def test_cancel_after_extraction_persists_the_paid_result(client, seeded, db_session, monkeypatch):
    """取消打在提取完成之后（如出闸的 await 上）：结果必须落库，不能丢弃重跑。

    修复前统一退回 PENDING——已经计费的识别被丢掉，重启后同一份文档再买一次。
    """
    from contextlib import asynccontextmanager

    from app.services import async_task_worker as worker

    class _CancelOnExitGate:
        @asynccontextmanager
        async def slot(self, pages, *, timeout=None):
            try:
                yield
            finally:
                raise asyncio.CancelledError()  # 模拟停机取消打在出闸时刻

    monkeypatch.setattr(worker, "get_gate", lambda: _CancelOnExitGate())

    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    claimed = tasksvc.claim_next(db_session)

    with pytest.raises(asyncio.CancelledError):
        await worker._process_one(claimed)

    row = db_session.query(AsyncTask).filter(AsyncTask.id == task_id).one()
    db_session.refresh(row)
    assert row.status == TaskStatus.COMPLETED, "提取已完成（已计费），结果必须保留"
    assert json.loads(row.result_json)["errcode"] == "0000"


# ── 端到端 ───────────────────────────────────────────────────────────────────

async def test_worker_processes_task_to_completion(client, seeded, db_session):
    """提交 → worker 处理 → 轮询拿到可解析的 result。"""
    from app.services.async_task_worker import _process_one

    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]

    claimed = tasksvc.claim_next(db_session)
    assert claimed is not None
    await _process_one(claimed)

    entry = _query(client, token, [task_id]).json()["data"][task_id]
    assert entry["status"] == "COMPLETED", entry["errorMessage"]
    payload = json.loads(entry["result"])
    # 与同步接口同一个 mapper，外壳应当一致
    assert set(payload) == {"errcode", "description", "data", "traceId", "docPages"}
    assert payload["errcode"] == "0000"


async def test_missing_spool_file_fails_the_task_not_the_worker(client, seeded, db_session):
    """落盘文件被外力删掉时任务标 FAILED，worker 继续活着。"""
    from app.services.async_task_worker import _process_one

    token = _token(client)
    task_id = _submit(client, token).json()["data"]["taskId"]
    claimed = tasksvc.claim_next(db_session)
    os.unlink(claimed.spool_path)

    await _process_one(claimed)

    entry = _query(client, token, [task_id]).json()["data"][task_id]
    assert entry["status"] == "FAILED"
    assert "丢失" in entry["errorMessage"]
