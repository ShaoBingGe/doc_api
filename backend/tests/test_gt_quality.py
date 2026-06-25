"""GT quality validation — empty / contradiction / type / missing / cross-sample."""

import uuid

import pytest

from app.ocr_optimizer.service.gt_quality import validate_gt_quality


@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _api_with_docs(db, n=2):
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document

    api = ApiDefinition(
        id=uuid.uuid4(), name="gtq", api_code=f"gtq-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.draft.value, version=1,
        processor_type="mock", model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api)
    db.flush()
    docs = []
    for i in range(n):
        d = Document(id=uuid.uuid4(), filename=f"d{i}.pdf", file_type="pdf",
                     file_size=1, storage_path=f"/tmp/d{i}", status="completed",
                     api_definition_id=api.id)
        docs.append(d)
    db.add_all(docs)
    api.config = {"sample_document_ids": [str(d.id) for d in docs]}
    db.commit()
    return api, docs


def _gt(db, doc_id, field_name, value, ftype="string"):
    from app.models.annotation import Annotation, AnnotationSource
    db.add(Annotation(id=uuid.uuid4(), document_id=doc_id, field_name=field_name,
                      field_value=value, field_type=ftype,
                      source=AnnotationSource.manual.value, is_corrected=True))
    db.commit()


def test_clean_gt_is_ok(db_session):
    api, docs = _api_with_docs(db_session, 2)
    for d in docs:
        _gt(db_session, d.id, "docType", "invoice")
        _gt(db_session, d.id, "currency", "JPY")
        _gt(db_session, d.id, "totalAmount", "1000", ftype="number")
    res = validate_gt_quality(db_session, api.id)
    assert res["ok"] is True
    assert res["checked_samples"] == 2
    assert res["counts"]["error"] == 0


def test_empty_gt_flagged(db_session):
    api, docs = _api_with_docs(db_session, 1)
    _gt(db_session, docs[0].id, "docType", "invoice")
    _gt(db_session, docs[0].id, "invoiceNumber", "   ")  # whitespace-only
    res = validate_gt_quality(db_session, api.id)
    assert any(w["field"] == "invoiceNumber" and w["level"] == "warning"
               for w in res["warnings"])


def test_number_type_mismatch_is_error(db_session):
    api, docs = _api_with_docs(db_session, 1)
    _gt(db_session, docs[0].id, "docType", "invoice")
    _gt(db_session, docs[0].id, "totalAmount", "abc", ftype="number")
    res = validate_gt_quality(db_session, api.id)
    assert res["ok"] is False
    assert any(w["field"] == "totalAmount" and w["level"] == "error"
               for w in res["warnings"])


def test_intra_doc_contradiction_is_error(db_session):
    api, docs = _api_with_docs(db_session, 1)
    _gt(db_session, docs[0].id, "docType", "invoice")
    _gt(db_session, docs[0].id, "currency", "JPY")
    _gt(db_session, docs[0].id, "currency", "USD")  # same field, conflicting
    res = validate_gt_quality(db_session, api.id)
    assert res["ok"] is False
    assert any(w["field"] == "currency" and w["level"] == "error"
               for w in res["warnings"])


def test_missing_doctype_warns(db_session):
    api, docs = _api_with_docs(db_session, 1)
    _gt(db_session, docs[0].id, "invoiceNumber", "123")
    res = validate_gt_quality(db_session, api.id)
    assert any(w["field"] == "docType" and w["level"] == "warning"
               for w in res["warnings"])


def test_cross_sample_currency_divergence_is_info(db_session):
    api, docs = _api_with_docs(db_session, 2)
    _gt(db_session, docs[0].id, "docType", "invoice")
    _gt(db_session, docs[0].id, "currency", "JPY")
    _gt(db_session, docs[1].id, "docType", "invoice")
    _gt(db_session, docs[1].id, "currency", "USD")
    res = validate_gt_quality(db_session, api.id)
    assert any(w["field"] == "currency" and w["level"] == "info"
               for w in res["warnings"])
    assert res["ok"] is True  # info doesn't fail the gate


def test_sample_without_gt_warns(db_session):
    api, docs = _api_with_docs(db_session, 2)
    _gt(db_session, docs[0].id, "docType", "invoice")
    # docs[1] has no GT at all
    res = validate_gt_quality(db_session, api.id)
    assert any("无任何已审视 GT" in w["message"] for w in res["warnings"])
