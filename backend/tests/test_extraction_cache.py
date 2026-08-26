"""提取结果缓存 —— 同一份文件 15 分钟内不重复烧模型。

缓存最危险的失效模式不是"没命中"，而是"命中了错的"。这组用例按四条
隔离边界来写：文件不同 / 模板不同 / client 不同 / 已过期，任一条不成立
都必须**不命中**。另外锁住两个改写：traceId 与 sourceFileHash 必须是
本次请求的值，不能把上一次的带出来。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.extraction_cache import ExtractionCache
from app.services import extraction_cache as xcache

CLIENT = "TEST_CacheClient"
OTHER = "TEST_CacheClient_B"
TPL = "7"


def _payload(number: str = "INV-1", src_hash: str = "orig-hash") -> dict:
    return {
        "errcode": "0000",
        "description": "Success",
        "traceId": "originaltrace01",
        "docPages": 3,
        "data": [{
            "header": {"basic": {
                "invoiceNumber": number, "sourceFileHash": src_hash}},
            "detail": {},
        }],
    }


@pytest.fixture(autouse=True)
def _wipe(db_session):
    for r in db_session.query(ExtractionCache).all():
        db_session.delete(r)
    db_session.commit()
    yield
    for r in db_session.query(ExtractionCache).all():
        db_session.delete(r)
    db_session.commit()


def _store(db, *, client=CLIENT, tpl=TPL, chash="hash-A", payload=None):
    xcache.store(db, client_id=client, template_id=tpl, chash=chash,
                 payload=payload or _payload(), doc_pages=3)


def _lookup(db, *, client=CLIENT, tpl=TPL, chash="hash-A",
            trace="newtrace0000001", src=""):
    return xcache.lookup(db, client_id=client, template_id=tpl, chash=chash,
                         trace_id=trace, source_file_hash=src)


# ── 命中 ─────────────────────────────────────────────────────────────────────

def test_same_file_hits_and_returns_pages(db_session):
    _store(db_session)
    hit = _lookup(db_session)
    assert hit is not None
    payload, pages = hit
    assert payload["data"][0]["header"]["basic"]["invoiceNumber"] == "INV-1"
    assert pages == 3


def test_hit_rewrites_trace_id(db_session):
    """缓存的是识别结果，不是那一次请求 —— 沿用旧 traceId 会让排障指错链路。"""
    _store(db_session)
    payload, _ = _lookup(db_session, trace="abcdef0123456789")
    assert payload["traceId"] == "abcdef0123456789"
    assert payload["traceId"] != "originaltrace01"


def test_hit_rewrites_source_file_hash(db_session):
    """sourceFileHash 要回填**本次**调用方传的值，不能带出上一次的。"""
    _store(db_session, payload=_payload(src_hash="first-caller-hash"))
    payload, _ = _lookup(db_session, src="second-caller-hash")
    assert payload["data"][0]["header"]["basic"]["sourceFileHash"] == \
        "second-caller-hash"


def test_caller_without_file_hash_gets_empty_not_stale(db_session):
    """对接方基本不传 fileHash（实测 118 次里只有 8 次）——留空，别串味。"""
    _store(db_session, payload=_payload(src_hash="someone-elses"))
    payload, _ = _lookup(db_session, src="")
    assert payload["data"][0]["header"]["basic"]["sourceFileHash"] == ""


def test_hit_counter_increments(db_session):
    _store(db_session)
    _lookup(db_session)
    _lookup(db_session)
    row = db_session.query(ExtractionCache).one()
    db_session.refresh(row)
    assert row.hits == 2
    assert row.last_hit_at is not None


# ── 四条隔离边界，任一不成立都不能命中 ────────────────────────────────────────

def test_different_content_misses(db_session):
    _store(db_session, chash="hash-A")
    assert _lookup(db_session, chash="hash-B") is None


def test_different_template_misses(db_session):
    """同一份文件在不同模板下抽取的字段完全不同，串了就是错数据。"""
    _store(db_session, tpl="7")
    assert _lookup(db_session, tpl="9") is None


def test_different_client_misses(db_session):
    """按 client 隔离：命中与否会泄露"别人是否传过同一份文件"。"""
    _store(db_session, client=CLIENT)
    assert _lookup(db_session, client=OTHER) is None


def test_expired_entry_misses(db_session):
    _store(db_session)
    row = db_session.query(ExtractionCache).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    assert _lookup(db_session) is None


# ── 不该被缓存的 ─────────────────────────────────────────────────────────────

def test_failed_result_is_not_cached(db_session):
    """失败结果不入缓存 —— 重试可能会成功，缓存住等于把失败钉死 15 分钟。"""
    bad = {"errcode": "5000", "description": "boom", "data": [], "traceId": "x"}
    xcache.store(db_session, client_id=CLIENT, template_id=TPL,
                 chash="hash-fail", payload=bad, doc_pages=1)
    assert _lookup(db_session, chash="hash-fail") is None


def test_disabled_by_flag(db_session, monkeypatch):
    from app.core.config import get_settings

    _store(db_session)
    monkeypatch.setattr(get_settings(), "EXTRACT_CACHE_ENABLED", False,
                        raising=False)
    assert _lookup(db_session) is None


# ── 健壮性 ───────────────────────────────────────────────────────────────────

def test_lookup_fails_open_on_corrupt_row(db_session):
    """缓存永远不该让主流程失败 —— 存的 JSON 坏了就当没命中。"""
    _store(db_session)
    row = db_session.query(ExtractionCache).one()
    row.result_json = "{not json"
    db_session.commit()
    assert _lookup(db_session) is None


def test_purge_removes_only_expired(db_session):
    _store(db_session, chash="fresh")
    _store(db_session, chash="stale")
    stale = db_session.query(ExtractionCache).filter(
        ExtractionCache.content_hash == "stale").one()
    stale.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    assert xcache.purge_expired(db_session) == 1
    left = db_session.query(ExtractionCache).all()
    assert len(left) == 1 and left[0].content_hash == "fresh"


def test_content_hash_is_sha256_of_bytes():
    import hashlib

    data = uuid.uuid4().bytes
    assert xcache.content_hash(data) == hashlib.sha256(data).hexdigest()
