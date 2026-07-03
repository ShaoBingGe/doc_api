"""批次3 回归：判官 verify_module_fix 必须 fail-closed。

历史 bug：LLM 异常 / 非 dict 返回（mock 降级返回 fixture list）/ verdict
非法 —— 全部默认 accept。即 LLM 越不稳、优化器输出越不可信的时刻，
「accept 才采纳」（红线④）的闸门恰好消失，坏 prompt 直写新版本。
现在任何无法完成审查的情况一律 reject（保留旧 prompt）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.ocr_optimizer.service import module_optimizer


def _module():
    return SimpleNamespace(
        module_key="invoice_number", display_name="发票号",
        ocr_prompt="find invoice number",
    )


def _iteration(failing: bool = True):
    per_sample = [{
        "sample_doc_id": "d1",
        "matched": not failing,
        "field_accuracy": 0.0 if failing else 1.0,
        "ocr_sliced": "WRONG" if failing else "INV-1",
        "ground_truth": "INV-1",
    }]
    return SimpleNamespace(per_sample_results=per_sample)


def _proposed():
    return {"new_ocr_prompt": "better prompt"}


def _verify(monkeypatch, llm_result=None, llm_exc=None):
    def _fake_llm(**kwargs):
        if llm_exc is not None:
            raise llm_exc
        return llm_result

    monkeypatch.setattr(module_optimizer, "llm_text_completion", _fake_llm)
    return module_optimizer.verify_module_fix(
        module=_module(), iteration=_iteration(), proposed=_proposed(),
        processor_spec="mock", model_name=None,
    )


def test_llm_exception_rejects(monkeypatch):
    v = _verify(monkeypatch, llm_exc=RuntimeError("429 rate limited"))
    assert v["verdict"] == "reject"
    assert "unavailable" in v["reasoning"]


def test_non_dict_response_rejects(monkeypatch):
    # mock 降级的典型形态：返回发票 fixture list
    v = _verify(monkeypatch, llm_result=[{"invoiceNumber": "INV-1"}])
    assert v["verdict"] == "reject"


def test_invalid_verdict_rejects(monkeypatch):
    v = _verify(monkeypatch, llm_result={"verdict": "maybe", "reasoning": "?"})
    assert v["verdict"] == "reject"


def test_missing_verdict_rejects(monkeypatch):
    v = _verify(monkeypatch, llm_result={"reasoning": "looks good"})
    assert v["verdict"] == "reject"


def test_explicit_accept_passes_through(monkeypatch):
    v = _verify(monkeypatch, llm_result={"verdict": "accept", "reasoning": "ok"})
    assert v["verdict"] == "accept"
    assert v["reasoning"] == "ok"


def test_explicit_reject_passes_through(monkeypatch):
    v = _verify(monkeypatch, llm_result={"verdict": "REJECT", "reasoning": "no"})
    assert v["verdict"] == "reject"


def test_no_failing_samples_accepts_without_llm(monkeypatch):
    # 无失败样本可验 → 无需审查（唯一保留的默认 accept），且绝不调 LLM
    def _boom(**kwargs):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(module_optimizer, "llm_text_completion", _boom)
    v = module_optimizer.verify_module_fix(
        module=_module(), iteration=_iteration(failing=False),
        proposed=_proposed(), processor_spec="mock", model_name=None,
    )
    assert v["verdict"] == "accept"
