"""单次模型调用硬超时（2026-08-31 生产事故回归锁）。

事故：genai.Client 默认无 HTTP 超时，Gemini 上游 503（Deadline expired）期间
一条同步请求在服务端**静默挂起 8+ 分钟**（无任何处理器日志），直到 Caddy 600s
read_timeout 掐断客户端（502）。无超时还意味着异步 worker 线程可能被一次调用
永久占死。

本文件锁三件事：
  1. Gemini 每次调用都带 http_options.timeout（毫秒，来自 GEMINI_HTTP_TIMEOUT_S），
     且是**按请求**传——key_pool 的 client 按 key 缓存，构造期参数改不动缓存实例；
  2. qwen 的 httpx.post 超时来自 QWEN_HTTP_TIMEOUT_S（与 gemini 同一套治理）；
  3. 超时类异常被 is_provider_failure 判为 provider 级故障 → 触发主备切换，
     而不是当成文档错误直接抛给调用方。
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.services.extract_service import is_provider_failure


# ── 1. Gemini 按请求携带硬超时 ────────────────────────────────────────────────

def test_gemini_request_carries_http_timeout(monkeypatch, tmp_path):
    from app.processors.gemini_processor import GeminiProcessor
    from app.processors import key_pool

    captured = {}

    class _Resp:
        text = "[]"
        usage_metadata = None

    class _Models:
        def generate_content(self, *, model, contents, config):
            captured["http_options"] = config.http_options
            return _Resp()

    class _Client:
        models = _Models()

    monkeypatch.setattr("app.processors.key_pool._Lease.client",
                        lambda self, factory: _Client())
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEYS", "k1", raising=False)
    monkeypatch.setattr(get_settings(), "GEMINI_HTTP_TIMEOUT_S", 123.0, raising=False)
    key_pool.reset_pools()

    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4")
    GeminiProcessor().process_document(str(f), "PROMPT", None)
    key_pool.reset_pools()

    ho = captured["http_options"]
    assert ho is not None, "调用必须携带 http_options（否则无超时=可无限挂起）"
    assert ho.timeout == 123_000, "超时须来自 GEMINI_HTTP_TIMEOUT_S 且单位为毫秒"


# ── 2. qwen 超时来自 config ───────────────────────────────────────────────────

def test_qwen_post_uses_config_timeout(monkeypatch, tmp_path):
    from app.processors import qwen_processor as qp

    monkeypatch.setattr(get_settings(), "QWEN_API_KEYS", "k1", raising=False)
    monkeypatch.setattr(get_settings(), "QWEN_HTTP_TIMEOUT_S", 456.0, raising=False)

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    def fake_post(url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _Resp()

    monkeypatch.setattr(qp.httpx, "post", fake_post)
    monkeypatch.setattr(qp.QwenProcessor, "_image_to_data_url",
                        staticmethod(lambda p: "data:image/png;base64,x"))

    f = tmp_path / "x.png"
    f.write_bytes(b"\x89PNG")
    qp.QwenProcessor().process_document(str(f), "PROMPT", None)

    assert captured["timeout"] == 456.0


# ── 3. 超时 → provider 级故障 → 走主备切换 ────────────────────────────────────

@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout("read timed out"),          # 我们新加的硬超时抛这个
    httpx.ConnectTimeout("connect timed out"),
    TimeoutError("operation timed out"),
    RuntimeError("503 UNAVAILABLE. Deadline expired before operation could complete."),
])
def test_timeout_errors_are_provider_failures(exc):
    assert is_provider_failure(exc), (
        f"{type(exc).__name__} 必须判为 provider 级故障——"
        "否则超时会直接抛给调用方而不是切备份重试")


def test_document_level_error_is_not_provider_failure():
    """400 坏文件不该触发备份重试（换模型同样失败，只是延迟翻倍）。"""
    assert not is_provider_failure(ValueError("400 INVALID_ARGUMENT: bad document"))
