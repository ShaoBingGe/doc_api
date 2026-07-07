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


def test_attach_recomposes_prompt_so_skill_actually_renders(db_session, monkeypatch):
    """Regression: extraction uses the STATIC composed_prompt; attaching a skill
    must re-compose it, else 'attach to field' is a silent no-op."""
    from app.core.config import get_settings
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus
    from app.ocr_optimizer.service import skill_service

    monkeypatch.setattr(get_settings(), "SKILL_LIBRARY_RENDER", True, raising=False)
    apid = uuid.uuid4()
    ver = OcrPromptVersion(id=uuid.uuid4(), api_definition_id=apid, version="1",
                           status=PromptVersionStatus.active.value,
                           composed_prompt="ORIGINAL", composed_schema={})
    db_session.add(ver)
    db_session.add(OcrModule(id=uuid.uuid4(), prompt_version_id=ver.id,
                             module_key="total_amount", display_name="总额",
                             json_path="$.totalAmount", ocr_prompt="找总额", description="d",
                             schema_fragment={"type": "number"}, order_index=1, status="active"))
    db_session.commit()
    sk = skill_service.create_skill(db_session, name=f"sk-{uuid.uuid4().hex[:6]}",
                                    content="所有金额千分位取整", api_def_id=apid)
    assert "千分位取整" not in ver.composed_prompt                    # before attach
    skill_service.attach_skill_to_module(db_session, ver.id, "total_amount", sk.id)
    db_session.refresh(ver)
    assert "千分位取整" in ver.composed_prompt                        # after attach → recomposed
    assert "技能库补充" in ver.composed_prompt


def test_delete_skill_recomposes_so_content_retires(db_session, monkeypatch):
    """attach 侧的镜像回归：composed_prompt 是静态快照——归档技能行本身
    不会让已烤进 prompt 的内容退场，delete_skill 必须重组引用它的版本。"""
    from app.core.config import get_settings
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus
    from app.ocr_optimizer.service import skill_service

    monkeypatch.setattr(get_settings(), "SKILL_LIBRARY_RENDER", True, raising=False)
    apid = uuid.uuid4()
    ver = OcrPromptVersion(id=uuid.uuid4(), api_definition_id=apid, version="1",
                           status=PromptVersionStatus.active.value,
                           composed_prompt="ORIGINAL", composed_schema={})
    db_session.add(ver)
    db_session.add(OcrModule(id=uuid.uuid4(), prompt_version_id=ver.id,
                             module_key="total_amount", display_name="总额",
                             json_path="$.totalAmount", ocr_prompt="找总额", description="d",
                             schema_fragment={"type": "number"}, order_index=1, status="active"))
    db_session.commit()
    sk = skill_service.create_skill(db_session, name=f"sk-{uuid.uuid4().hex[:6]}",
                                    content="临时规则Y须退场", api_def_id=apid)
    skill_service.attach_skill_to_module(db_session, ver.id, "total_amount", sk.id)
    db_session.refresh(ver)
    assert "临时规则Y须退场" in ver.composed_prompt                   # baked in

    skill_service.delete_skill(db_session, sk.id)
    db_session.refresh(ver)
    assert "临时规则Y须退场" not in ver.composed_prompt               # retired on delete
    assert "技能库补充" not in ver.composed_prompt                    # last skill gone → block gone
