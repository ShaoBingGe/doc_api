"""单 worker 服务的健壮性：识别期间其他接口必须仍然可用。

## 这组用例在防什么

改造前，`async def analyze_document` 里直接调用**同步**的
`extract_service.extract_document()`。同步函数在协程里就是纯阻塞——整场识别期间
事件循环被独占，所有其他请求（取 token、结果轮询、健康检查）全部排队。

实测后果：腾讯云跑一次 6 页识别（约 200 秒）时，另一个客户端的取 token 调用
**30 秒超时**。异步接口上线后轮询频率远高于识别，这个缺陷会被放大成"服务假死"。

修复是把提取丢进 `anyio.to_thread`。下面的用例用一个会真正 `time.sleep` 的假
processor 来复现旧场景：如果哪天有人把 `await run_extraction(...)` 改回直接调用，
`test_token_endpoint_stays_responsive_during_extraction` 会立刻失败。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
import uuid

import httpx
import pytest

from tests.conftest import minimal_pdf

from app.main import app
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.open_api_client import OpenApiClient
from app.services.extract_gate import reset_gate

CLIENT_ID = "TEST_ConcurrencyClient"
SECRET = "concurrency-secret-123"
TEMPLATE_ID = 9909

#: 假识别的耗时。够长到能观测阻塞，够短到不拖慢测试。
FAKE_EXTRACT_SEC = 0.6


@pytest.fixture()
def seeded(db_session):
    if db_session.query(OpenApiClient).filter(
            OpenApiClient.client_id == CLIENT_ID).first() is None:
        db_session.add(OpenApiClient(
            id=uuid.uuid4(), client_id=CLIENT_ID, client_secret=SECRET,
            client_secret_hash=hashlib.sha256(SECRET.encode()).hexdigest(),
            name=CLIENT_ID, tenant_id=None, is_active=True,
        ))
    if db_session.query(ApiDefinition).filter(
            ApiDefinition.external_template_id == TEMPLATE_ID).first() is None:
        db_session.add(ApiDefinition(
            id=uuid.uuid4(), name="test-concurrency-api",
            api_code=f"test-conc-{uuid.uuid4().hex[:6]}",
            description="", status=ApiDefinitionStatus.active.value, version=1,
            response_schema={"type": "ARRAY"}, processor_type="mock",
            model_name="mock", external_template_id=TEMPLATE_ID, tenant_id=None,
        ))
    db_session.commit()


@pytest.fixture()
def slow_extract(monkeypatch):
    """把提取换成一个真正阻塞线程的假实现，并记录并发峰值。"""
    from app.schemas.extract import ExtractMetadata, ExtractResponse
    from app.services import extract_service as svc

    state = {"active": 0, "peak": 0, "calls": 0}

    def _fake(db, **kwargs):
        state["calls"] += 1
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(FAKE_EXTRACT_SEC)   # 同步阻塞，模拟真实模型调用
            return ExtractResponse(
                request_id=uuid.uuid4(),
                api_code=kwargs.get("api_code", "x"),
                api_version=1,
                data={"docType": "invoice"},
                entities=[{"docType": "invoice"}],
                metadata=ExtractMetadata(
                    processor="mock", model="mock",
                    tokens_used=0, processing_time_ms=1,
                ),
            )
        finally:
            state["active"] -= 1

    monkeypatch.setattr(svc, "extract_document", _fake)
    return state


@pytest.fixture(autouse=True)
def _fresh_gate():
    reset_gate()
    yield
    reset_gate()


def _sign(ts: str) -> str:
    return hashlib.md5(f"{CLIENT_ID}{SECRET}{ts}".encode()).hexdigest()


async def _get_token(ac: httpx.AsyncClient) -> str:
    ts = str(int(time.time()))
    r = await ac.post("/base/oauth/token", json={
        "client_id": CLIENT_ID, "timestamp": ts, "sign": _sign(ts)})
    return r.json()["access_token"]


def _pdf():
    return {"file": ("d.pdf", io.BytesIO(minimal_pdf()), "application/pdf")}


async def _analyze(ac: httpx.AsyncClient, token: str):
    from app.api.v1 import open_api

    return await ac.post(
        f"{open_api.ANALYZE_PATH}?access_token={token}",
        data={"templateId": str(TEMPLATE_ID), "clientId": CLIENT_ID},
        files=_pdf(),
    )


#: 采样间隔。事件循环没被阻塞时，一次 sleep 的实际耗时应当只比它多出微秒级。
_PROBE_TICK = 0.02


async def _max_loop_lag(stop: asyncio.Event) -> float:
    """持续采样事件循环延迟，返回观测到的最大滞后（秒）。

    这是"事件循环是否可用"的直接度量。别用"另发一个请求测它的耗时"来代替：
    发请求前的那次 await 本身就会被阻塞吃掉，等它返回时阻塞往往已经结束，
    于是测出来永远是 0 —— 一个永远为真的假绿灯（本文件早期版本就踩过）。
    """
    worst = 0.0
    while not stop.is_set():
        t = time.perf_counter()
        await asyncio.sleep(_PROBE_TICK)
        worst = max(worst, time.perf_counter() - t - _PROBE_TICK)
    return worst


async def _lag_during_analyze(ac: httpx.AsyncClient, token: str) -> tuple[float, httpx.Response]:
    stop = asyncio.Event()
    probe = asyncio.create_task(_max_loop_lag(stop))
    resp = await _analyze(ac, token)
    stop.set()
    return await probe, resp


async def test_event_loop_stays_responsive_during_extraction(seeded, slow_extract):
    """识别期间事件循环必须保持可用 —— 这正是线上取 token 30s 超时的那个场景。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        token = await _get_token(ac)
        lag, resp = await _lag_during_analyze(ac, token)

        # 循环没被独占，那么识别期间也能正常取 token
        t0 = time.perf_counter()
        await _get_token(ac)
        token_latency = time.perf_counter() - t0

    assert lag < FAKE_EXTRACT_SEC / 3, (
        f"识别期间事件循环滞后 {lag:.2f}s —— 循环被独占了，"
        "检查 extract_document 是否又被直接在 async 路由里调用（应走 run_extraction）"
    )
    assert token_latency < 0.2
    # 断到 errcode 而不是只看 status_code —— 这条链路失败时也返 200，
    # 只看 status_code 的话假 processor 一崩用例就假绿了（阳性对照抓到过一次）。
    assert resp.json()["errcode"] == "0000", resp.json()["description"]
    assert slow_extract["calls"] == 1, "假 processor 必须真的被调用过"


async def test_the_blocking_detector_actually_detects_blocking(seeded, slow_extract, monkeypatch):
    """阳性对照：把提取改回"直接在协程里同步调用"，上面的判据必须报警。

    没有这条，`test_event_loop_stays_responsive_during_extraction` 可能只是在测
    一个永远成立的命题，看着绿其实什么都没守住。
    """
    from app.api.v1 import open_api
    from app.services import extract_service as svc

    async def _blocking_run_extraction(**kwargs):
        # 刻意还原改造前的写法：同步函数直接在协程里跑
        return svc.extract_document(None, **kwargs)

    monkeypatch.setattr(open_api, "run_extraction", _blocking_run_extraction)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        token = await _get_token(ac)
        lag, resp = await _lag_during_analyze(ac, token)

    assert resp.json()["errcode"] == "0000", resp.json()["description"]
    assert lag >= FAKE_EXTRACT_SEC / 2, (
        f"阻塞版本下事件循环只滞后了 {lag:.2f}s —— 说明上面那条用例的判据"
        "根本区分不出阻塞与非阻塞，它是个假绿灯"
    )


async def test_gate_caps_concurrent_extractions(seeded, slow_extract, monkeypatch):
    """并发上限是真的 —— 6 个请求同时打进来，同时在跑的不超过闸的上限。"""
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "GATE_MAX_DOCS", 2, raising=False)
    monkeypatch.setattr(s, "GATE_MAX_PAGES", 100, raising=False)
    reset_gate()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        token = await _get_token(ac)
        results = await asyncio.gather(*(_analyze(ac, token) for _ in range(6)))

    assert all(r.json()["errcode"] == "0000" for r in results), \
        [r.json()["description"] for r in results if r.json()["errcode"] != "0000"]
    assert slow_extract["calls"] == 6, "6 个请求都应当最终被处理，不能丢"
    assert slow_extract["peak"] <= 2, (
        f"同时在跑 {slow_extract['peak']} 个，超过闸的上限 2 —— 内存会按这个倍数翻"
    )


async def test_gate_wait_does_not_hold_a_db_connection(seeded, slow_extract, monkeypatch):
    """闸前等待时必须归还数据库连接。

    修复前 analyze 路由攥着 `Depends(get_db)` 的连接等闸（可达 120s），
    QueuePool 默认 5+10——十几个并发 analyze 就把池抽干，/base/oauth/token
    跟着 30s 超时：症状与事件循环阻塞一模一样，只是换了个资源在堵。
    """
    from app.core.config import get_settings
    from app.core.database import engine

    s = get_settings()
    monkeypatch.setattr(s, "GATE_MAX_DOCS", 1, raising=False)
    monkeypatch.setattr(s, "GATE_MAX_PAGES", 100, raising=False)
    monkeypatch.setattr(s, "SYNC_GATE_WAIT_SEC", 5.0, raising=False)
    reset_gate()

    from app.api.v1 import open_api
    monkeypatch.setattr(open_api, "_settings", s, raising=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        token = await _get_token(ac)
        first = asyncio.create_task(_analyze(ac, token))     # 占住唯一槽位
        await asyncio.sleep(0.1)
        second = asyncio.create_task(_analyze(ac, token))    # 在闸前排队
        await asyncio.sleep(0.15)

        # 排队请求已完成鉴权（用过连接）又尚未进闸——此刻它不得攥着连接
        checked_out = engine.pool.checkedout()
        assert checked_out == 0, (
            f"闸前等待期间仍有 {checked_out} 个连接被攥着——"
            "十几个并发就会抽干连接池，token 端点跟着超时"
        )

        r1, r2 = await asyncio.gather(first, second)
    assert r1.json()["errcode"] == "0000"
    assert r2.json()["errcode"] == "0000"


async def test_busy_gate_returns_retry_hint_not_a_hang(seeded, slow_extract, monkeypatch):
    """闸满且等不到时回明确的'稍后重试'，不是无限挂着让调用方自己超时。"""
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "GATE_MAX_DOCS", 1, raising=False)
    monkeypatch.setattr(s, "GATE_MAX_PAGES", 100, raising=False)
    monkeypatch.setattr(s, "SYNC_GATE_WAIT_SEC", 0.05, raising=False)
    reset_gate()

    # 路由读的是模块级快照，得一并改
    from app.api.v1 import open_api
    monkeypatch.setattr(open_api, "_settings", s, raising=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        token = await _get_token(ac)
        first = asyncio.create_task(_analyze(ac, token))
        await asyncio.sleep(0.1)
        second = await _analyze(ac, token)
        await first

    body = second.json()
    assert second.status_code == 200, "繁忙也走 200 + errcode，不抛 HTTP 错误"
    assert body["errcode"] != "0000"
    assert "繁忙" in body["description"]
    assert body["data"] == []
