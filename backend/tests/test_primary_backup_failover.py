"""主力 gemini / 备份 qwen：解析链 + 运行时切换。

两台服务器跑**同一份配置**（PRIMARY=gemini, BACKUP=qwen）；差异只在各自
配了哪些 key。大陆机器没有 gemini key → 主力天然不可用，直接走 qwen，
**不发起任何一次注定超时的 gemini 请求**——这条由
`test_no_primary_key_means_backup_without_network_attempt` 锁死。
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.processors import key_pool as kp
from app.processors.factory import ProcessorFactory as PF
from app.services import extract_service as svc


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    kp.reset_pools()
    s = get_settings()
    monkeypatch.setattr(s, "PRIMARY_PROCESSOR", "gemini", raising=False)
    monkeypatch.setattr(s, "BACKUP_PROCESSOR", "qwen", raising=False)
    monkeypatch.setattr(s, "ENABLE_BACKUP_FAILOVER", True, raising=False)
    monkeypatch.setattr(s, "PRIMARY_SATURATION_PER_KEY", 4, raising=False)
    yield
    kp.reset_pools()


def _keys(monkeypatch, *, gemini: str = "", qwen: str = ""):
    s = get_settings()
    monkeypatch.setattr(s, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(s, "QWEN_API_KEY", "", raising=False)
    monkeypatch.setattr(s, "GEMINI_API_KEYS", gemini, raising=False)
    monkeypatch.setattr(s, "QWEN_API_KEYS", qwen, raising=False)
    kp.reset_pools()


def _procs(chain):
    return [p for p, _ in chain]


# ── 解析链 ───────────────────────────────────────────────────────────────────

def test_both_configured_puts_gemini_first_qwen_as_backup(monkeypatch):
    """新加坡机器：两个都配了 → 主力 gemini，qwen 挂在后面兜底。"""
    _keys(monkeypatch, gemini="g1;g2", qwen="q1")
    assert _procs(PF.resolve_chain()) == ["gemini", "qwen"]


def test_no_primary_key_means_backup_without_network_attempt(monkeypatch):
    """大陆机器：没有 gemini key → 链里**根本没有** gemini。

    这是「零成本判定主力不可用」的关键：只要 gemini 不在链里，就不会有
    任何一次 20 秒起步、注定超时的跨境请求。
    """
    _keys(monkeypatch, gemini="", qwen="q1;q2")
    chain = PF.resolve_chain()
    assert _procs(chain) == ["qwen"]
    assert "gemini" not in _procs(chain)


def test_row_preference_wins_when_available(monkeypatch):
    """客户在 API 行上显式指定的引擎排第一（红线⑥：行级是偏好不是契约，
    但可用时应当被尊重），主力/备份跟在后面。"""
    _keys(monkeypatch, gemini="g1", qwen="q1")
    assert _procs(PF.resolve_chain("qwen", "qwen-vl-plus")) == ["qwen", "gemini"]


def test_stale_row_preference_falls_through_to_primary(monkeypatch):
    """行里钉着 gemini 但本机没有 gemini key（历史行 + 大陆机器）→ 走 qwen，
    且丢弃外族 model_name。"""
    _keys(monkeypatch, gemini="", qwen="q1")
    chain = PF.resolve_chain("gemini", "gemini-2.5-flash")
    assert chain == [("qwen", None)]


def test_nothing_available_degrades_to_mock(monkeypatch):
    _keys(monkeypatch, gemini="", qwen="")
    monkeypatch.setattr(get_settings(), "DEFAULT_PROCESSOR", "gemini", raising=False)
    assert _procs(PF.resolve_chain()) == ["mock"]


def test_resolve_spec_agrees_with_chain_head(monkeypatch):
    """红线⑥：resolve_spec 仍是唯一入口，且与链首同一个答案——
    两套判据各说各话会让「调用时用 A、记账记 B」。"""
    for g, q in [("g1", "q1"), ("", "q1"), ("g1", "")]:
        _keys(monkeypatch, gemini=g, qwen=q)
        assert PF.resolve_spec(None, None)[0] == PF.resolve_chain()[0][0]


# ── 饱和切换 ─────────────────────────────────────────────────────────────────

def test_saturated_primary_moves_backup_to_front(monkeypatch):
    """每个 gemini 账号都压满 → 本次改走 qwen，gemini 退到后面仍可兜底。"""
    _keys(monkeypatch, gemini="g1;g2", qwen="q1")
    leases = [kp.get_pool("gemini").lease() for _ in range(8)]  # 每 key 4 个在途
    try:
        assert PF.is_saturated("gemini") is True
        assert _procs(PF.resolve_chain()) == ["qwen", "gemini"]
    finally:
        for l in leases:
            l.__exit__(None, None, None)


def test_one_idle_key_keeps_traffic_on_primary(monkeypatch):
    """只要还有一个账号相对空闲就不切——用 min 而非总量的意义所在：
    7 个在途听着很多，但其中一个账号只扛了 3 个，主力还接得住。"""
    _keys(monkeypatch, gemini="g1;g2", qwen="q1")
    pool = kp.get_pool("gemini")
    leases = [pool.lease() for _ in range(7)]
    try:
        assert min(s["in_flight"] for s in pool.stats()) == 3
        assert PF.is_saturated("gemini") is False
        assert _procs(PF.resolve_chain())[0] == "gemini"
    finally:
        for l in leases:
            l.__exit__(None, None, None)


def test_all_keys_cooling_counts_as_saturated(monkeypatch):
    _keys(monkeypatch, gemini="g1", qwen="q1")
    lease = kp.get_pool("gemini").lease()
    lease.__exit__(Exception, Exception("429 RESOURCE_EXHAUSTED"), None)
    assert PF.is_saturated("gemini") is True
    assert _procs(PF.resolve_chain()) == ["qwen", "gemini"]


def test_saturation_switch_can_be_disabled(monkeypatch):
    """阈值置 0 → 只有全部熔断才切，压满不切。"""
    _keys(monkeypatch, gemini="g1", qwen="q1")
    monkeypatch.setattr(get_settings(), "PRIMARY_SATURATION_PER_KEY", 0, raising=False)
    leases = [kp.get_pool("gemini").lease() for _ in range(20)]
    try:
        assert PF.is_saturated("gemini") is False
        assert _procs(PF.resolve_chain())[0] == "gemini"
    finally:
        for l in leases:
            l.__exit__(None, None, None)


def test_backup_alone_is_never_reordered(monkeypatch):
    """链里只有一个引擎时不做重排（没得换）。"""
    _keys(monkeypatch, gemini="", qwen="q1")
    leases = [kp.get_pool("qwen").lease() for _ in range(20)]
    try:
        assert _procs(PF.resolve_chain()) == ["qwen"]
    finally:
        for l in leases:
            l.__exit__(None, None, None)


# ── 运行时切换 ───────────────────────────────────────────────────────────────

def _stub_runs(monkeypatch, behaviour: dict):
    """behaviour: {processor_type: 异常实例 or 返回的 structured}。记录调用顺序。"""
    called: list[str] = []

    def fake_run(*, storage_path, processor_type, model_name, prompt, schema):
        called.append(processor_type)
        outcome = behaviour[processor_type]
        if isinstance(outcome, BaseException):
            raise outcome
        return {}, outcome, f"{processor_type}|m", None

    monkeypatch.setattr(svc, "_run_processor", fake_run)
    return called


def _run(chain):
    return svc._run_with_failover(
        chain=chain, storage_path="/tmp/x.pdf", prompt="p", schema=None)


def test_quota_exhaustion_falls_over_to_backup(monkeypatch):
    called = _stub_runs(monkeypatch, {
        "gemini": RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"),
        "qwen": [{"docType": "invoice"}],
    })
    *_, used = _run([("gemini", None), ("qwen", None)])
    assert called == ["gemini", "qwen"]
    assert used == "qwen", "记账必须记**实际执行**的引擎，不是链首"


@pytest.mark.parametrize("msg", [
    "Connection timed out", "503 Service temporarily unavailable",
    "401 UNAUTHENTICATED: API key not valid",
])
def test_provider_level_failures_fail_over(monkeypatch, msg):
    called = _stub_runs(monkeypatch, {
        "gemini": RuntimeError(msg), "qwen": [{"docType": "receipt"}]})
    _run([("gemini", None), ("qwen", None)])
    assert called == ["gemini", "qwen"]


def test_bad_document_does_not_fail_over(monkeypatch):
    """400 坏文件：换个模型同样会失败。重试只会把一次失败变两次、
    延迟和成本翻倍——2026-08-26 那两张坏 jpg 就是这种。"""
    called = _stub_runs(monkeypatch, {
        "gemini": ValueError("400 INVALID_ARGUMENT: Unable to process input image"),
        "qwen": [{"docType": "invoice"}],
    })
    with pytest.raises(ValueError):
        _run([("gemini", None), ("qwen", None)])
    assert called == ["gemini"], "文档级错误不该触发备份重试"


def test_primary_success_never_touches_backup(monkeypatch):
    called = _stub_runs(monkeypatch, {
        "gemini": [{"docType": "invoice"}],
        "qwen": RuntimeError("不该被调用"),
    })
    *_, used = _run([("gemini", None), ("qwen", None)])
    assert called == ["gemini"] and used == "gemini"


def test_failover_can_be_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "ENABLE_BACKUP_FAILOVER", False, raising=False)
    called = _stub_runs(monkeypatch, {
        "gemini": RuntimeError("429 quota"), "qwen": [{"ok": 1}]})
    with pytest.raises(RuntimeError):
        _run([("gemini", None), ("qwen", None)])
    assert called == ["gemini"]


def test_last_link_failure_propagates(monkeypatch):
    """备份也挂了 → 抛出，不吞成功。"""
    called = _stub_runs(monkeypatch, {
        "gemini": RuntimeError("429 quota"),
        "qwen": RuntimeError("429 quota"),
    })
    with pytest.raises(RuntimeError):
        _run([("gemini", None), ("qwen", None)])
    assert called == ["gemini", "qwen"]
