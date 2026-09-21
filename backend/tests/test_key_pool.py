"""Gemini 多账号 key 池：均匀调度 + 配额熔断 + 横向扩展。

池不碰网络（lease 不建 client，client() 才惰性建），全部用例零 token。
"""

from __future__ import annotations

import threading

import pytest

from app.core.config import Settings, get_settings
from app.processors import key_pool as kp
from app.processors.key_pool import KeyPool, is_quota_error


@pytest.fixture(autouse=True)
def _fresh_pool():
    kp.reset_pools()
    yield
    kp.reset_pools()


# ── 配置解析 ─────────────────────────────────────────────────────────────────

def test_keys_parse_semicolon_comma_dedup_and_order():
    s = Settings(GEMINI_API_KEYS="k1; k2,k1 ;k3", GEMINI_API_KEY="ignored")
    assert s.gemini_api_keys == ["k1", "k2", "k3"]


def test_keys_fall_back_to_single_key():
    s = Settings(GEMINI_API_KEYS="", GEMINI_API_KEY="solo")
    assert s.gemini_api_keys == ["solo"]


def test_no_keys_means_empty_list():
    assert Settings(GEMINI_API_KEYS="", GEMINI_API_KEY="").gemini_api_keys == []


def test_factory_is_available_accepts_multi_key_config(monkeypatch):
    """只配 GEMINI_API_KEYS（不配单数版）时，gemini 必须算「可用」——
    否则 resolve_spec 会把双账号配置整体降级到 mock。"""
    from app.processors.factory import ProcessorFactory

    s = get_settings()
    monkeypatch.setattr(s, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(s, "GEMINI_API_KEYS", "k1;k2", raising=False)
    if "gemini" in ProcessorFactory._build_registry():
        assert ProcessorFactory.is_available("gemini") is True
    monkeypatch.setattr(s, "GEMINI_API_KEYS", "", raising=False)
    assert ProcessorFactory.is_available("gemini") is False


# ── 调度：均匀性 ─────────────────────────────────────────────────────────────

def test_sequential_calls_alternate_between_two_keys():
    """串行场景（每次一进一出）：平局按累计次数轮转 → 严格交替。"""
    pool = KeyPool(["a", "b"])
    seen = []
    for _ in range(6):
        with pool.lease() as lease:
            seen.append(lease.key)
    assert seen == ["a", "b", "a", "b", "a", "b"]


def test_new_lease_avoids_the_busy_key():
    """最少在途优先：a 被一个慢任务占着时，后续请求全部落到 b——
    这正是轮询做不到的（轮询会把第 3 个请求再砸给 a）。"""
    pool = KeyPool(["a", "b"])
    slow = pool.lease()          # a: in_flight=1，不释放
    assert slow.key == "a"
    with pool.lease() as l2:
        assert l2.key == "b"
        with pool.lease() as l3:   # a、b 各 1 在途，平局按 total → a
            assert l3.key == "a"
    slow.__exit__(None, None, None)


def test_three_keys_spread_evenly_under_concurrency():
    """10 个并发槽位摊到 3 个账号：占用数最多差 1（4/3/3）。"""
    pool = KeyPool(["a", "b", "c"])
    leases = [pool.lease() for _ in range(10)]
    counts = {}
    for l in leases:
        counts[l.key] = counts.get(l.key, 0) + 1
    assert sorted(counts.values()) == [3, 3, 4]
    for l in leases:
        l.__exit__(None, None, None)


def test_threaded_leases_stay_balanced_and_leak_free():
    """80 线程并发租/还后：计数归零、累计次数均匀（线程安全冒烟）。"""
    pool = KeyPool(["a", "b"])
    barrier = threading.Barrier(8)

    def work():
        barrier.wait()
        for _ in range(10):
            with pool.lease():
                pass

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats = pool.stats()
    assert all(s["in_flight"] == 0 for s in stats), "in_flight 泄漏"
    totals = [s["total"] for s in stats]
    assert sum(totals) == 80
    assert abs(totals[0] - totals[1]) <= 8, f"累计分布失衡: {totals}"


# ── 熔断 ─────────────────────────────────────────────────────────────────────

class _Quota429(Exception):
    pass


def _burn(pool, err=None):
    """跑一次 lease；err 非空则模拟该次调用抛错。"""
    lease = pool.lease()
    key = lease.key
    lease.__exit__(type(err) if err else None, err, None)
    return key


def test_quota_error_cools_the_key_and_shifts_traffic():
    pool = KeyPool(["a", "b"])
    burned = _burn(pool, _Quota429("429 RESOURCE_EXHAUSTED: quota exceeded"))
    # 冷却期内接下来的请求全部落到另一个 key
    other = "b" if burned == "a" else "a"
    for _ in range(4):
        assert _burn(pool) == other


def test_non_quota_error_does_not_cool_the_key():
    """400 坏文件是这份文档的问题，不该让账号背锅。"""
    pool = KeyPool(["a", "b"])
    _burn(pool, ValueError("400 INVALID_ARGUMENT: Unable to process input image"))
    seen = {_burn(pool) for _ in range(2)}
    assert seen == {"a", "b"}, "非配额错误不应把 key 踢出轮转"


def test_cooldown_expires_and_key_rejoins(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(kp.time, "monotonic", lambda: fake_now[0])
    pool = KeyPool(["a", "b"])
    burned = _burn(pool, _Quota429("429"))
    fake_now[0] += 31.0          # 首次退避 30s，过期后应归队
    seen = {_burn(pool) for _ in range(3)}
    assert burned in seen


def test_backoff_doubles_and_resets_on_success(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(kp.time, "monotonic", lambda: fake_now[0])
    pool = KeyPool(["a"])   # 单 key：每次都租到它
    _burn(pool, _Quota429("429"))
    first = pool._states[0].cooldown_until - fake_now[0]
    fake_now[0] += first + 1
    _burn(pool, _Quota429("429"))
    second = pool._states[0].cooldown_until - fake_now[0]
    assert second == pytest.approx(first * 2)
    fake_now[0] += second + 1
    _burn(pool)                   # 成功
    assert pool._states[0].consecutive_failures == 0
    assert pool._states[0].cooldown_until == 0.0


def test_all_cooling_fails_open_to_earliest_expiry(monkeypatch):
    """全灭时不拒绝服务：选冷却到期最早的硬用。"""
    fake_now = [1000.0]
    monkeypatch.setattr(kp.time, "monotonic", lambda: fake_now[0])
    pool = KeyPool(["a", "b"])
    _burn(pool, _Quota429("429"))                 # a 冷却 30s
    fake_now[0] += 10.0
    _burn(pool, _Quota429("quota exceeded"))      # b 冷却 30s（到期更晚）
    key = _burn(pool)
    assert key == "a", "应选冷却到期最早的 key"


def test_is_quota_error_classification():
    assert is_quota_error(Exception("429 RESOURCE_EXHAUSTED"))
    assert is_quota_error(Exception("Rate limit reached for requests"))
    assert not is_quota_error(Exception("400 INVALID_ARGUMENT: bad pdf"))
    assert not is_quota_error(TimeoutError("read timed out"))


# ── 单例 ─────────────────────────────────────────────────────────────────────

def test_get_pool_reads_settings_and_is_singleton(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "GEMINI_API_KEYS", "k1;k2", raising=False)
    p1 = kp.get_pool("gemini")
    assert p1.size == 2
    assert kp.get_pool("gemini") is p1
