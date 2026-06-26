"""P2 — OcrSkill library storage layer (CRUD + private/global scoping). Token-free."""
import uuid

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


def test_create_and_scope_private_vs_global(db_session):
    from app.ocr_optimizer.service import skill_service as svc

    api_a = uuid.uuid4()
    api_b = uuid.uuid4()
    g = svc.create_skill(db_session, name=f"金额去千分位-{uuid.uuid4().hex[:6]}",
                         content="所有金额去千分位与货币符号", api_def_id=None)
    p = svc.create_skill(db_session, name=f"A私有-{uuid.uuid4().hex[:6]}",
                         content="A 公司发票号取右上角", api_def_id=api_a)

    a_names = {s.name for s in svc.list_skills(db_session, api_a)}
    assert g.name in a_names and p.name in a_names        # A sees global + its own
    b_names = {s.name for s in svc.list_skills(db_session, api_b)}
    assert g.name in b_names and p.name not in b_names     # B sees global, NOT A's private
    only_global = {s.name for s in svc.list_skills(db_session, None)}
    assert g.name in only_global and p.name not in only_global


def test_duplicate_name_rejected(db_session):
    from app.core.exceptions import ValidationError
    from app.ocr_optimizer.service import skill_service as svc

    nm = f"dup-{uuid.uuid4().hex[:6]}"
    svc.create_skill(db_session, name=nm, content="x", api_def_id=None)
    with pytest.raises(ValidationError):
        svc.create_skill(db_session, name=nm, content="y", api_def_id=None)


def test_delete_soft_deactivates(db_session):
    from app.ocr_optimizer.service import skill_service as svc

    sk = svc.create_skill(db_session, name=f"tmp-{uuid.uuid4().hex[:6]}",
                          content="z", api_def_id=None)
    svc.delete_skill(db_session, sk.id)
    assert sk.name not in {s.name for s in svc.list_skills(db_session, None)}


def test_attach_skill_to_module(db_session):
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus
    from app.ocr_optimizer.service import skill_service as svc

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
    sk = svc.create_skill(db_session, name=f"sk-{uuid.uuid4().hex[:6]}",
                          content="规则", api_def_id=apid)
    mod = svc.attach_skill_to_module(db_session, ver.id, "invoice_number", sk.id)
    assert str(sk.id) in [str(x) for x in (mod.skill_ids or [])]
    # idempotent
    mod2 = svc.attach_skill_to_module(db_session, ver.id, "invoice_number", sk.id)
    assert len([x for x in mod2.skill_ids if str(x) == str(sk.id)]) == 1
