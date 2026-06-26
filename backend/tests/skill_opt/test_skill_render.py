"""P2 — skill rendering into composed prompts (composer + skill_render). Token-free."""
import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _mod(key="invoice_number"):
    return SimpleNamespace(
        module_key=key, display_name="发票号", json_path=f"$.{key}",
        schema_fragment={"type": "STRING"}, ocr_prompt="找发票号",
        ocr_suggestions=None, skill_ids=[],
    )


def test_composer_renders_skill_content_when_given():
    from app.ocr_optimizer.service import composer

    m = _mod()
    p = composer.assemble_prompt([m], country_global=None,
                                 skill_content={"invoice_number": "- 【去前缀】删除 EMS-JP- 前缀"})
    assert composer._SKILL_BLOCK_HEADER in p
    assert "去前缀" in p
    # absent → no skill block (unchanged)
    assert composer._SKILL_BLOCK_HEADER not in composer.assemble_prompt([m], country_global=None)


def test_skill_render_resolve_is_flag_gated(db_session, monkeypatch):
    from app.core.config import get_settings
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus
    from app.ocr_optimizer.service import skill_render, skill_service

    apid = uuid.uuid4()
    ver = OcrPromptVersion(id=uuid.uuid4(), api_definition_id=apid, version="1",
                           status=PromptVersionStatus.active.value,
                           composed_prompt="x", composed_schema={})
    db_session.add(ver)
    db_session.add(OcrModule(id=uuid.uuid4(), prompt_version_id=ver.id,
                             module_key="invoice_number", display_name="n",
                             json_path="$.invoiceNumber", ocr_prompt="p", description="d",
                             schema_fragment={"type": "string"}, order_index=1, status="active"))
    db_session.commit()
    sk = skill_service.create_skill(db_session, name=f"sk-{uuid.uuid4().hex[:6]}",
                                    content="规则X：取最显著标签右侧", api_def_id=apid)
    mod = skill_service.attach_skill_to_module(db_session, ver.id, "invoice_number", sk.id)

    s = get_settings()
    monkeypatch.setattr(s, "SKILL_LIBRARY_RENDER", False, raising=False)
    assert skill_render.resolve(db_session, apid, [mod]) == {}      # flag off → no-op

    monkeypatch.setattr(s, "SKILL_LIBRARY_RENDER", True, raising=False)
    out = skill_render.resolve(db_session, apid, [mod])
    assert "规则X" in out["invoice_number"]                          # flag on → injected
