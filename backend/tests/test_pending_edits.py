"""
Design v8 pending_edits overlay — unit tests.

Covers:
  - pending_edits_service: record_rename / record_added_field /
    record_modification / clear_overlay
  - rename chain collapse: A→B then B→C stores A→C
  - rename round-trip: A→B then B→A drops the entry
  - cascade_rename_annotations: rename updates all docs of the ApiDef
  - document_service._augment_with_overlay: adds Part 3-style 'PENDING EDITS'
    appendix to the prompt with rename map + added-field list
  - customer_iteration._clone_module: rename propagation rewrites module_key
    + json_path + appends a §3.9 hint to ocr_prompt
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _setup_api_def(db, name="overlay_test"):
    """Create a throwaway ApiDef + Document for overlay tests."""
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document

    api_def = ApiDefinition(
        id=uuid.uuid4(),
        name=name,
        api_code=f"overlay-{uuid.uuid4().hex[:6]}",
        description="",
        status=ApiDefinitionStatus.draft.value,
        version=1,
        processor_type="mock",
        model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api_def)
    db.flush()

    doc1 = Document(
        id=uuid.uuid4(),
        filename="doc1.pdf",
        file_type="pdf",
        file_size=1,
        storage_path="/tmp/x1",
        status="completed",
        api_definition_id=api_def.id,
    )
    doc2 = Document(
        id=uuid.uuid4(),
        filename="doc2.pdf",
        file_type="pdf",
        file_size=1,
        storage_path="/tmp/x2",
        status="completed",
        api_definition_id=api_def.id,
    )
    db.add_all([doc1, doc2])
    db.commit()
    return api_def, doc1, doc2


def _add_annotation(db, doc_id, field_name, value="x"):
    from app.models.annotation import Annotation, AnnotationSource, FieldType
    ann = Annotation(
        id=uuid.uuid4(),
        document_id=doc_id,
        field_name=field_name,
        field_value=value,
        field_type=FieldType.string.value,
        source=AnnotationSource.ai_detected.value,
        is_corrected=False,
    )
    db.add(ann)
    db.commit()
    return ann


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def test_record_rename_basic(db_session):
    from app.services import pending_edits_service
    api_def, _d1, _d2 = _setup_api_def(db_session)
    overlay = pending_edits_service.record_rename(db_session, api_def.id, "foo", "bar")
    assert overlay["renames"] == {"foo": "bar"}


def test_rename_chain_collapse(db_session):
    """A→B then B→C should store A→C, not two entries."""
    from app.services import pending_edits_service
    api_def, _d1, _d2 = _setup_api_def(db_session)
    pending_edits_service.record_rename(db_session, api_def.id, "billFromName", "supplierName")
    overlay = pending_edits_service.record_rename(db_session, api_def.id, "supplierName", "vendorName")
    assert overlay["renames"] == {"billFromName": "vendorName"}
    assert "supplierName" not in overlay["renames"]


def test_rename_roundtrip_drops_entry(db_session):
    """A→B then B→A should leave renames empty."""
    from app.services import pending_edits_service
    api_def, _d1, _d2 = _setup_api_def(db_session)
    pending_edits_service.record_rename(db_session, api_def.id, "foo", "bar")
    overlay = pending_edits_service.record_rename(db_session, api_def.id, "bar", "foo")
    assert overlay["renames"] == {}


def test_record_added_field_idempotent(db_session):
    from app.services import pending_edits_service
    api_def, _d1, _d2 = _setup_api_def(db_session)
    pending_edits_service.record_added_field(db_session, api_def.id, "supplierTier", "string")
    overlay = pending_edits_service.record_added_field(db_session, api_def.id, "supplierTier", "string")
    names = [f["field_name"] for f in overlay["added_fields"]]
    assert names.count("supplierTier") == 1


def test_cascade_rename_annotations(db_session):
    """Renaming a field must update Annotation.field_name across every doc."""
    from app.services import pending_edits_service
    from app.models.annotation import Annotation

    api_def, d1, d2 = _setup_api_def(db_session)
    _add_annotation(db_session, d1.id, "invoiceNumber", "A123")
    _add_annotation(db_session, d2.id, "invoiceNumber", "B456")

    n = pending_edits_service.cascade_rename_annotations(
        db_session, api_def.id, "invoiceNumber", "invoiceNo",
    )
    assert n == 2

    after = db_session.query(Annotation.field_name).filter(
        Annotation.document_id.in_([d1.id, d2.id])
    ).all()
    assert all(name == ("invoiceNo",) for name in after)


def test_clear_overlay(db_session):
    from app.services import pending_edits_service
    api_def, _d1, _d2 = _setup_api_def(db_session)
    pending_edits_service.record_rename(db_session, api_def.id, "foo", "bar")
    pending_edits_service.record_added_field(db_session, api_def.id, "extra", "string")
    pending_edits_service.clear_overlay(db_session, api_def.id)
    overlay = pending_edits_service.get_overlay(db_session, api_def.id)
    assert overlay == {"added_fields": [], "renames": {}, "modifications": {}}


def test_augment_with_overlay_renders_rename_map(db_session):
    """The prompt augmenter must emit a '{旧→新}' mapping block for the LLM."""
    from app.services import pending_edits_service
    from app.services.document_service import _augment_with_overlay
    api_def, _d1, _d2 = _setup_api_def(db_session)
    pending_edits_service.record_rename(db_session, api_def.id, "billFromName", "supplierName")
    pending_edits_service.record_added_field(
        db_session, api_def.id, "supplierTier", "string",
        description="A/B/C tier",
    )
    base = "ORIGINAL_PROMPT_BODY"
    augmented = _augment_with_overlay(db_session, api_def.id, base)
    assert "ORIGINAL_PROMPT_BODY" in augmented
    assert "# 跨样本 Pending Edits 补充" in augmented
    assert "billFromName" in augmented and "supplierName" in augmented
    assert "→" in augmented or "->" in augmented or "JSON key `supplierName`" in augmented
    assert "supplierTier" in augmented
    assert "A/B/C tier" in augmented


def test_augment_with_overlay_passthrough_when_empty(db_session):
    """No overlay → prompt unchanged."""
    from app.services.document_service import _augment_with_overlay
    api_def, _d1, _d2 = _setup_api_def(db_session)
    base = "X" * 100
    assert _augment_with_overlay(db_session, api_def.id, base) == base


def test_clone_module_renames_module_key_and_json_path():
    """Rename in diff → module_key + json_path leaf rewritten + §3.9 hint appended."""
    from app.ocr_optimizer.service.customer_iteration import _clone_module
    from app.ocr_optimizer.models import OcrModule

    src = OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=uuid.uuid4(),
        module_key="bill_from_name",
        display_name="开票方名称识别",
        description="找开票方名称",
        json_path="$[*].billFromName",
        schema_fragment={"type": "STRING", "description": "..."},
        ocr_suggestions={},
        ocr_prompt="原 prompt 内容",
        skill_ids=[],
        order_index=10,
        status="active",
    )
    new = _clone_module(
        src,
        new_version_id=uuid.uuid4(),
        patch={"__rename_old": "billFromName", "__rename_new": "supplierName"},
    )
    assert new.module_key == "supplier_name"
    assert new.json_path == "$[*].supplierName"
    assert "supplierName" in (new.ocr_prompt or "")
    assert "billFromName" in (new.ocr_prompt or "")
    assert "§3.9" in (new.ocr_prompt or "") or "重命名" in (new.ocr_prompt or "")


def test_clone_module_no_rename_keeps_keys():
    """Without rename patch, module_key + json_path stay identical."""
    from app.ocr_optimizer.service.customer_iteration import _clone_module
    from app.ocr_optimizer.models import OcrModule

    src = OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=uuid.uuid4(),
        module_key="invoice_number",
        display_name="发票号",
        description="…",
        json_path="$[*].invoiceNumber",
        schema_fragment={"type": "STRING"},
        ocr_suggestions={},
        ocr_prompt="x",
        skill_ids=[],
        order_index=0,
        status="active",
    )
    new = _clone_module(src, new_version_id=uuid.uuid4(), patch={})
    assert new.module_key == "invoice_number"
    assert new.json_path == "$[*].invoiceNumber"


def test_global_output_contract_has_section_3_9():
    """§3.9 must be present in the platform asset (driving the LLM)."""
    from app.ocr_optimizer.service.output_contract import render_output_contract
    text = render_output_contract()
    assert "## 3.9 字段重命名传导" in text
    assert "module_key" in text or "重命名" in text
