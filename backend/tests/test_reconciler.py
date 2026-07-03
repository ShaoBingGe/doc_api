"""
Phase 4 — cross-round contradiction reconciler (req 5).
"""

from __future__ import annotations

import uuid


def test_has_accumulated_feedback():
    from app.ocr_optimizer.service.reconciler import has_accumulated_feedback
    assert has_accumulated_feedback("找发票号\n\n# 客户反馈补充\n取右上角") is True
    assert has_accumulated_feedback("找发票号") is False
    assert has_accumulated_feedback("") is False
    assert has_accumulated_feedback(None) is False


def test_reconcile_prioritizes_latest_intent(monkeypatch):
    from app.ocr_optimizer.service import reconciler

    captured = {}

    def fake_llm(*, processor_spec, model_name, system_instruction, user_prompt, as_json):
        captured["user"] = user_prompt
        return {
            "coherent_prompt": "取括号外的发票号（去掉括号内附注）",
            "dropped": ["取括号内的值"],
            "rationale": "最新意图要求括号外",
        }

    monkeypatch.setattr(reconciler, "llm_text_completion_failover", fake_llm)
    out = reconciler.reconcile_module_prompt(
        module_key="invoice_number", display_name="发票号",
        current_prompt="找发票号\n\n# 客户反馈补充\n取括号内的值",
        new_suggestions=["取括号外的值"],
        latest_intent={"corrected_value": "INV-1"},
        processor_spec="mock",
    )
    # 批次6：协调输出保留反馈 marker（消除 has_accumulated_feedback 状态机振荡）
    assert out.startswith("取括号外的发票号（去掉括号内附注）")
    assert "# 客户反馈补充" in out
    # the LLM saw both the old (contradictory) prompt and the latest intent
    assert "取括号内的值" in captured["user"]
    assert "INV-1" in captured["user"]


def test_reconcile_fail_open_returns_none(monkeypatch):
    from app.ocr_optimizer.service import reconciler

    def boom(**kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(reconciler, "llm_text_completion_failover", boom)
    out = reconciler.reconcile_module_prompt(
        module_key="x", display_name="x", current_prompt="p",
        new_suggestions=["s"], processor_spec="mock",
    )
    assert out is None  # caller falls back to blind-append


def test_clone_module_uses_reconciled_prompt():
    from app.ocr_optimizer.service.customer_iteration import _clone_module
    from app.ocr_optimizer.models import OcrModule

    src = OcrModule(
        id=uuid.uuid4(), prompt_version_id=uuid.uuid4(),
        module_key="invoice_number", display_name="发票号",
        description="发票号", json_path="$[*].invoiceNumber",
        schema_fragment={"type": "STRING"}, ocr_suggestions={},
        ocr_prompt="OLD\n\n# 客户反馈补充\n取括号内", skill_ids=[], order_index=0,
        status="active",
    )
    # reconciled prompt wins over blind-append
    cloned = _clone_module(src, new_version_id=uuid.uuid4(),
                           patch={"__reconciled_prompt": "COHERENT 取括号外",
                                  "__prompt_suffix": "取括号外"})
    assert cloned.ocr_prompt == "COHERENT 取括号外"
    assert "# 客户反馈补充" not in cloned.ocr_prompt

    # without reconciled prompt → blind-append (fallback unchanged)
    cloned2 = _clone_module(src, new_version_id=uuid.uuid4(),
                            patch={"__prompt_suffix": "取括号外"})
    assert "# 客户反馈补充" in cloned2.ocr_prompt
    assert cloned2.ocr_prompt.endswith("取括号外")
