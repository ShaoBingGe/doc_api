"""ADR-002 P-A —— 优化器 typed-edit 契约（schema + flag-gated 指令）。"""
from types import SimpleNamespace

import pytest


def test_schema_parses_typed_edits():
    from app.ocr_optimizer.service.module_optimizer import ModuleOptimizerOutput

    out = ModuleOptimizerOutput.model_validate({
        "aggregate_diff": {"differences_description": "d", "differences_reason_analysis": "r"},
        "edits": [
            {"op": "append", "target": "currency", "content": "货币统一 ISO 4217",
             "source_type": "failure", "kind": "SKILL_DEFECT"},
        ],
    })
    assert out.edits and out.edits[0].op == "append"
    assert out.edits[0].target == "currency"


def test_schema_backward_compat_without_edits():
    from app.ocr_optimizer.service.module_optimizer import ModuleOptimizerOutput

    out = ModuleOptimizerOutput.model_validate({
        "aggregate_diff": {"differences_description": "", "differences_reason_analysis": ""},
        "new_ocr_prompt": "rewrite",
    })
    assert out.edits is None             # absent → None (wholesale-rewrite mode)
    assert out.new_ocr_prompt == "rewrite"


def test_schema_still_forbids_skill_fields():
    from pydantic import ValidationError

    from app.ocr_optimizer.service.module_optimizer import ModuleOptimizerOutput

    with pytest.raises(ValidationError):
        ModuleOptimizerOutput.model_validate({
            "aggregate_diff": {"differences_description": "", "differences_reason_analysis": ""},
            "new_skills": [{"name": "x"}],   # forbidden
        })


def _patch_llm(monkeypatch, captured):
    import app.ocr_optimizer.service.module_optimizer as mo

    def fake_llm(*, system_instruction, **kw):
        captured["system"] = system_instruction
        return {
            "aggregate_diff": {"differences_description": "", "differences_reason_analysis": ""},
            "new_ocr_prompt": None,
            "edits": [{"op": "append", "target": "currency", "content": "rule"}],
        }

    monkeypatch.setattr(mo, "llm_text_completion", fake_llm)
    monkeypatch.setattr(mo, "_build_user_prompt", lambda *a, **k: "user")


def test_flag_on_appends_typed_instruction_and_returns_edits(monkeypatch):
    import app.ocr_optimizer.service.module_optimizer as mo
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "SKILL_TYPED_EDITS", True, raising=False)
    cap = {}
    _patch_llm(monkeypatch, cap)
    out = mo.optimize_module(
        module=SimpleNamespace(module_key="currency"), iteration=None, history=[],
        processor_spec="mock", model_name=None,
    )
    assert "TYPED-EDIT MODE" in cap["system"]            # instruction appended
    assert out["edits"] and out["edits"][0]["op"] == "append"


def test_flag_off_keeps_plain_instruction(monkeypatch):
    import app.ocr_optimizer.service.module_optimizer as mo
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "SKILL_TYPED_EDITS", False, raising=False)
    cap = {}
    _patch_llm(monkeypatch, cap)
    out = mo.optimize_module(
        module=SimpleNamespace(module_key="currency"), iteration=None, history=[],
        processor_spec="mock", model_name=None,
    )
    assert "TYPED-EDIT MODE" not in cap["system"]         # unchanged system prompt
    # edits still parsed if the LLM returns them, but prompt didn't ask
    assert out["edits"] and out["edits"][0]["target"] == "currency"
