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

    def fake_llm(*, system_instruction, user_prompt="", **kw):
        captured["system"] = system_instruction
        captured["user"] = user_prompt
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


def test_meta_hint_prepended_to_user_prompt(monkeypatch):
    import app.ocr_optimizer.service.module_optimizer as mo

    cap = {}
    _patch_llm(monkeypatch, cap)
    mo.optimize_module(
        module=SimpleNamespace(module_key="currency"), iteration=None, history=[],
        processor_spec="mock", model_name=None,
        meta_hint="（元记忆）replace 被拒率高，优先 append",
    )
    assert cap["user"].startswith("（元记忆）") and cap["user"].endswith("user")  # prepended


def test_no_meta_hint_when_empty(monkeypatch):
    import app.ocr_optimizer.service.module_optimizer as mo

    cap = {}
    _patch_llm(monkeypatch, cap)
    mo.optimize_module(
        module=SimpleNamespace(module_key="currency"), iteration=None, history=[],
        processor_spec="mock", model_name=None, meta_hint="",
    )
    assert cap["user"] == "user"  # unchanged


# ── P-B.1 composer renders rule_edits_text ──────────────────────────────────


def _cmod(key, rule_edits=""):
    return SimpleNamespace(
        module_key=key, display_name=key, json_path=f"$.{key}",
        schema_fragment={"type": "string"}, ocr_prompt="find it",
        order_index=0, rule_edits_text=rule_edits,
    )


def test_composer_renders_rule_edits_block():
    from app.ocr_optimizer.service import composer

    m = _cmod("currency", "## [field:currency]\n- 货币统一为 ISO 4217 三字母码")
    p = composer.assemble_prompt([m], country_global=None)
    assert composer._RULE_EDITS_HEADER in p
    assert "ISO 4217" in p
    assert "## [field:currency]" not in p   # section marker stripped


def test_composer_no_rule_block_when_empty():
    from app.ocr_optimizer.service import composer

    base = composer.assemble_prompt([_cmod("x", "")], country_global=None)
    assert composer._RULE_EDITS_HEADER not in base   # empty → byte-identical


# ── P-B.2 clone carries rule_edits_text (inherit + update) ───────────────────


import pytest as _pytest  # noqa: E402


@_pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _ver_with_mod(db, rule="## [field:amount]\n- 旧规则"):
    import uuid

    from app.ocr_optimizer.models import (
        OcrModule, OcrPromptVersion, PromptVersionStatus, VersionOrigin,
    )

    ver = OcrPromptVersion(id=uuid.uuid4(), api_definition_id=uuid.uuid4(), version="1",
                           status=PromptVersionStatus.active.value, origin=VersionOrigin.init.value,
                           composed_prompt="x", composed_schema={}, country_global_text="")
    db.add(ver)
    db.add(OcrModule(id=uuid.uuid4(), prompt_version_id=ver.id, module_key="amount",
                     display_name="金额", description="d", json_path="$.amount",
                     schema_fragment={}, ocr_suggestions={}, ocr_prompt="找金额",
                     order_index=1, rule_edits_text=rule))
    db.commit()
    return ver


def _clone(db, src_ver, updates):
    import uuid

    from app.ocr_optimizer.models import OcrPromptVersion, PromptVersionStatus, VersionOrigin
    from app.ocr_optimizer.service import persistence

    new = OcrPromptVersion(id=uuid.uuid4(), api_definition_id=src_ver.api_definition_id,
                           version="2", status=PromptVersionStatus.draft.value,
                           origin=VersionOrigin.round.value, composed_prompt="",
                           composed_schema={}, country_global_text="")
    db.add(new)
    db.flush()
    mods = persistence.clone_modules_to_new_version(
        db, new_version=new, base_modules=list(src_ver.modules), updates=updates,
        keep_keys=None, add_specs=None, renames=None,
    )
    return {m.module_key: m for m in mods}


def test_clone_updates_rule_edits_text(db_session):
    ver = _ver_with_mod(db_session)
    out = _clone(db_session, ver, {"amount": {"rule_edits_text": "## [field:amount]\n- 新规则"}})
    assert "新规则" in out["amount"].rule_edits_text
    assert "旧规则" not in out["amount"].rule_edits_text


def test_clone_inherits_rule_edits_text_when_no_update(db_session):
    ver = _ver_with_mod(db_session)
    out = _clone(db_session, ver, {})  # no update → inherit
    assert "旧规则" in out["amount"].rule_edits_text


# ── P-B.2 core: build_rule_update pipeline (pure) ────────────────────────────


def test_build_rule_update_applies_and_clips():
    from app.ocr_optimizer.skilltrain.apply import build_rule_update

    edits = [
        {"op": "append", "target": "currency", "content": "统一为 JPY"},
        {"op": "bogus", "target": "currency", "content": "ignored"},   # invalid op dropped
    ]
    new_rules, clipped = build_rule_update("", edits, target_default="currency", severity=1.0)
    assert len(clipped) == 1 and clipped[0].op == "append"
    assert "统一为 JPY" in new_rules
    assert "## [field:currency]" in new_rules   # section created


def test_build_rule_update_filters_rejected():
    from app.ocr_optimizer.skilltrain.apply import build_rule_update
    from app.ocr_optimizer.skilltrain.buffer import RejectedEditBuffer
    from app.ocr_optimizer.skilltrain.types import FieldEdit

    buf = RejectedEditBuffer()
    buf.add(FieldEdit(op="append", target="currency", content="统一为 JPY"))
    edits = [{"op": "append", "target": "currency", "content": "统一为 JPY"}]  # already rejected
    new_rules, clipped = build_rule_update("", edits, target_default="currency", rej_buffer=buf)
    assert clipped == [] and new_rules == ""   # filtered → no-op
