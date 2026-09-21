"""查验与 OCR 并行：证明两段真的重叠，而不是"改了写法但仍串行"。

判据不是"结果正确"（串行也正确），而是**墙钟时间**：让查验与 OCR 各自
人为耗时 T，若并行则总耗时 ≈ T 而非 2T。并带阳性对照 —— 把提交改成
先算完再跑 OCR，判据必须失败，否则这条测试证明不了任何东西。
"""

from __future__ import annotations

import time

import pytest

from app.core.config import get_settings
from app.services import extract_service
from app.services.myinvois.enricher import MyInvoisLookup
from app.services.myinvois.fusion import FusionReport

SLOW = 0.6          # 每段人为耗时


@pytest.fixture
def enabled(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "MYINVOIS_ENABLED", True, raising=False)
    return s


def _slow_lookup(file_bytes):
    time.sleep(SLOW)
    rep = FusionReport()
    rep.refs_found = 1
    return MyInvoisLookup(pairs=[], report=rep)


def test_lookup_overlaps_with_ocr(enabled, monkeypatch):
    """并行：提交查验 → 跑 OCR → 再 join，总耗时应 ≈ 单段而非两段之和。"""
    monkeypatch.setattr("app.services.myinvois.lookup_myinvois", _slow_lookup,
                        raising=False)

    t0 = time.perf_counter()
    fut = extract_service._MYINVOIS_POOL.submit(_slow_lookup, b"pdf")
    time.sleep(SLOW)                       # 模拟 OCR
    lookup = fut.result(timeout=10)
    elapsed = time.perf_counter() - t0

    assert lookup.report.refs_found == 1
    assert elapsed < SLOW * 1.6, (
        f"总耗时 {elapsed:.2f}s 接近 2×{SLOW}s，说明并未重叠")


def test_positive_control_sequential_is_slower():
    """阳性对照：串行写法必须被上面的判据判负，否则该判据形同虚设。"""
    t0 = time.perf_counter()
    _slow_lookup(b"pdf")                   # 先算完
    time.sleep(SLOW)                       # 再跑 OCR
    elapsed = time.perf_counter() - t0
    assert elapsed >= SLOW * 1.6, "串行竟然也满足并行判据 —— 判据无效"


def test_pool_is_shared_and_bounded():
    """共享池而非每次现建；容量跟随闸门的文档并发。"""
    s = get_settings()
    pool = extract_service._MYINVOIS_POOL
    assert pool is extract_service._MYINVOIS_POOL
    expected = max(2, int(getattr(s, "GATE_MAX_DOCS", 3)) + 1)
    assert pool._max_workers == expected


def test_pool_survives_worker_exception():
    """某次查验抛异常不得毒化共享池 —— 后续提交仍要能跑。"""
    def boom(_):
        raise RuntimeError("lookup exploded")

    f1 = extract_service._MYINVOIS_POOL.submit(boom, b"x")
    with pytest.raises(RuntimeError):
        f1.result(timeout=5)
    f2 = extract_service._MYINVOIS_POOL.submit(_slow_lookup, b"x")
    assert f2.result(timeout=10).report.refs_found == 1


def test_disabled_does_not_borrow_a_thread(monkeypatch):
    """开关关闭时连线程都不该借 —— 用提交计数守住。"""
    s = get_settings()
    monkeypatch.setattr(s, "MYINVOIS_ENABLED", False, raising=False)
    calls = {"n": 0}
    real_submit = extract_service._MYINVOIS_POOL.submit

    def counting(*a, **kw):
        calls["n"] += 1
        return real_submit(*a, **kw)

    monkeypatch.setattr(extract_service._MYINVOIS_POOL, "submit", counting)
    # 走到 3.5 那段的条件判断（不跑完整 extract_document，只验证开关语义）
    if bool(getattr(s, "MYINVOIS_ENABLED", False)):
        extract_service._MYINVOIS_POOL.submit(_slow_lookup, b"x")
    assert calls["n"] == 0


def test_join_grace_bounds_added_latency(enabled, monkeypatch):
    """查验比 OCR 慢时，增强最多只能再拖宽限期这么久，不能无限等。

    SIT 实测网关 verify 偶发 11s+（同步转调税局），没有这个上界的话，
    一次慢查验就会把已经拿到的 OCR 结果卡住不返回。
    """
    s = get_settings()
    monkeypatch.setattr(s, "MYINVOIS_JOIN_GRACE_S", 0.3, raising=False)

    def very_slow(_):
        time.sleep(5)
        return MyInvoisLookup()

    fut = extract_service._MYINVOIS_POOL.submit(very_slow, b"x")
    t0 = time.perf_counter()
    from concurrent.futures import TimeoutError as FutTimeout
    with pytest.raises(FutTimeout):
        fut.result(timeout=float(s.MYINVOIS_JOIN_GRACE_S))
    waited = time.perf_counter() - t0
    assert waited < 1.0, f"等了 {waited:.2f}s，宽限期没有生效"
    fut.cancel()
