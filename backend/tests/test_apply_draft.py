"""A3：pending_edits_service.apply_draft 的 6-case 分发单测.

commit_draft_to_overlay 路由的 100 行分发下沉后，逐 case 验证 apply_draft
把 body 正确路由到 record_* 并返回最新 overlay（行为与旧路由一致：
返回 get_overlay，而非累积的中间 dict）。
"""
from __future__ import annotations

import uuid

import pytest

from app.services import pending_edits_service as pes


def _setup(db, name="applydraft"):
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document

    api = ApiDefinition(
        id=uuid.uuid4(), name=name, api_code=f"ad-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.draft.value, version=1,
        processor_type="mock", model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api)
    db.flush()
    doc = Document(
        id=uuid.uuid4(), filename="d.pdf", file_type="pdf", file_size=1,
        storage_path="/tmp/ad", status="completed", api_definition_id=api.id,
    )
    db.add(doc)
    db.commit()
    return api, doc


@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _ann(db, doc_id, field_name, value="x"):
    from app.models.annotation import Annotation, AnnotationSource, FieldType
    db.add(Annotation(
        id=uuid.uuid4(), document_id=doc_id, field_name=field_name,
        field_value=value, field_type=FieldType.string.value,
        source=AnnotationSource.ai_detected.value, is_corrected=False,
    ))
    db.commit()


def test_apply_draft_returns_fresh_overlay_not_intermediate(db_session):
    api, _ = _setup(db_session)
    out = pes.apply_draft(db_session, api.id, {})
    # 空 body → 空 overlay 的规范形状（与旧路由 return get_overlay 一致）
    assert set(out.keys()) >= {"renames", "added_fields", "deleted_fields",
                               "modifications", "field_constraints", "field_feedback"}


def test_case1_rename_records_and_cascades(db_session):
    api, doc = _setup(db_session)
    _ann(db_session, doc.id, "billFromName", "ACME")
    out = pes.apply_draft(db_session, api.id,
                          {"old_name": "billFromName", "new_name": "supplierName"})
    assert out["renames"] == {"billFromName": "supplierName"}
    # cascade 改了标注行
    from app.models.annotation import Annotation
    names = {a.field_name for a in db_session.query(Annotation)
             .filter(Annotation.document_id == doc.id).all()}
    assert "supplierName" in names and "billFromName" not in names


def test_case2_add_field(db_session):
    api, _ = _setup(db_session)
    out = pes.apply_draft(db_session, api.id,
                          {"new_name": "supplierTier", "field_type": "string",
                           "description": "tier"})
    assert any(f["field_name"] == "supplierTier" for f in out["added_fields"])


def test_case3_modification(db_session):
    api, doc = _setup(db_session)
    out = pes.apply_draft(db_session, api.id, {"modification": {
        "document_id": str(doc.id), "field_name": "currency", "value": "MYR"}})
    assert out["modifications"][str(doc.id)]["currency"] == "MYR"


def test_case4_delete_field_cascades(db_session):
    api, doc = _setup(db_session)
    _ann(db_session, doc.id, "junk")
    out = pes.apply_draft(db_session, api.id, {"deleted": True, "field_name": "junk"})
    assert "junk" in out["deleted_fields"]
    from app.models.annotation import Annotation
    left = db_session.query(Annotation).filter(
        Annotation.document_id == doc.id, Annotation.field_name == "junk").count()
    assert left == 0


def test_case5_field_constraint(db_session):
    api, _ = _setup(db_session)
    out = pes.apply_draft(db_session, api.id, {"field_constraint": {
        "field_name": "invoiceNumber", "type": "number",
        "strip_chars": [" ", "-"], "strip_non_numeric": True}})
    fc = out["field_constraints"]["invoiceNumber"]
    assert fc["type"] == "number" and fc["strip_non_numeric"] is True


def test_case6_field_feedback(db_session):
    api, _ = _setup(db_session)
    out = pes.apply_draft(db_session, api.id, {"field_feedback": {
        "field_name": "currency", "text": "取右上角币种代码"}})
    assert out["field_feedback"]["currency"] == "取右上角币种代码"


def test_multiple_cases_in_one_body(db_session):
    """一个 body 同时含 add + modification + feedback → 全部生效。"""
    api, doc = _setup(db_session)
    out = pes.apply_draft(db_session, api.id, {
        "new_name": "supplierTier",
        "modification": {"document_id": str(doc.id), "field_name": "c", "value": "1"},
        "field_feedback": {"field_name": "c", "text": "hint"},
    })
    assert any(f["field_name"] == "supplierTier" for f in out["added_fields"])
    assert out["modifications"][str(doc.id)]["c"] == "1"
    assert out["field_feedback"]["c"] == "hint"
