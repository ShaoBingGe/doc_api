"""Processor availability resolution (修「迭代优化进度始终为 0」).

Root cause fixed here: ApiDefinition rows pin processor_type='gemini' at
creation; on a server where Gemini is unreachable (no GEMINI_API_KEY) the
optimization rounds' OCR all error out → every round scores 0.0 while the
workspace upload path (env-driven DEFAULT_PROCESSOR) keeps working — so the
customer sees "识别 OK 但迭代优化进度/提升始终为 0".

resolve_spec is the single entry point: preferred(if available) →
DEFAULT_PROCESSOR(if available) → mock.
"""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.processors.factory import ProcessorFactory


@pytest.fixture()
def settings_sandbox(monkeypatch):
    """Mutate the cached Settings object safely per-test."""
    s = get_settings()
    yield s, monkeypatch


def test_unavailable_preferred_falls_back_to_default(settings_sandbox):
    s, mp = settings_sandbox
    mp.setattr(s, "GEMINI_API_KEY", "", raising=False)
    mp.setattr(s, "DEFAULT_PROCESSOR", "mock", raising=False)
    proc, model = ProcessorFactory.resolve_spec("gemini", "gemini-2.5-flash")
    assert proc == "mock"
    # 降级时丢弃外族 model_name，避免 mock/qwen 收到 gemini 的模型名
    assert model is None


def test_available_preferred_is_kept(settings_sandbox):
    s, mp = settings_sandbox
    mp.setattr(s, "GEMINI_API_KEY", "fake-key-for-test", raising=False)
    proc, model = ProcessorFactory.resolve_spec("gemini", "gemini-2.5-flash")
    # gemini 包已安装（dev 环境），key 非空 → 保留偏好与模型名
    if ProcessorFactory.is_available("gemini"):
        assert proc == "gemini"
        assert model == "gemini-2.5-flash"
    else:  # gemini 包未装的环境：必须仍能落到可用处理器
        assert proc in ProcessorFactory.available_types()


def test_everything_unavailable_degrades_to_mock(settings_sandbox):
    s, mp = settings_sandbox
    mp.setattr(s, "GEMINI_API_KEY", "", raising=False)
    mp.setattr(s, "QWEN_API_KEY", "", raising=False)
    mp.setattr(s, "OPENAI_API_KEY", "", raising=False)
    mp.setattr(s, "DEFAULT_PROCESSOR", "gemini", raising=False)
    proc, model = ProcessorFactory.resolve_spec("qwen", "qwen-vl-plus")
    assert proc == "mock"
    assert model is None


def test_mock_is_always_available():
    assert ProcessorFactory.is_available("mock") is True
    assert ProcessorFactory.resolve_spec("mock", None) == ("mock", None)


def test_failover_chain_drops_unavailable_providers(settings_sandbox):
    s, mp = settings_sandbox
    from app.ocr_optimizer.service.llm_failover import get_chain

    mp.setattr(s, "GEMINI_API_KEY", "", raising=False)
    mp.setattr(s, "DEFAULT_PROCESSOR", "mock", raising=False)
    mp.setattr(s, "LLM_FALLBACK_CHAIN", "gemini|gemini-2.5-flash;mock|", raising=False)
    chain = get_chain(primary_spec="gemini", primary_model="gemini-2.5-flash")
    providers = [p for (p, _m) in chain]
    # 不可用的 gemini 被剔除；mock 兜底必在
    assert "gemini" not in providers
    assert "mock" in providers


def test_failover_chain_keeps_available_primary(settings_sandbox):
    s, mp = settings_sandbox
    from app.ocr_optimizer.service.llm_failover import get_chain

    mp.setattr(s, "GEMINI_API_KEY", "fake-key", raising=False)
    if not ProcessorFactory.is_available("gemini"):
        pytest.skip("gemini package not installed in this env")
    chain = get_chain(primary_spec="gemini", primary_model="g-model")
    assert chain[0] == ("gemini", "g-model")
    assert any(p == "mock" for (p, _m) in chain)
