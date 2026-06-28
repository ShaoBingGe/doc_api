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
    assert overlay == {
        "added_fields": [], "renames": {}, "modifications": {},
        "deleted_fields": [], "field_constraints": {}, "field_feedback": {},
    }


# ── Phase 11a: deleted_fields ─────────────────────────────────────────────────


def test_record_deleted_field_cascade(db_session):
    """Deleting a field cascades to annotation rows AND cleans overlay."""
    from app.services import pending_edits_service
    from app.models.annotation import Annotation

    api_def, d1, d2 = _setup_api_def(db_session)
    _add_annotation(db_session, d1.id, "invoiceNumber", "A123")
    _add_annotation(db_session, d2.id, "invoiceNumber", "B456")
    # Also seed an added_fields entry + a modification — both should be wiped
    pending_edits_service.record_added_field(
        db_session, api_def.id, "invoiceNumber", "string",
    )
    pending_edits_service.record_modification(
        db_session, api_def.id, d1.id, "invoiceNumber", "X",
    )

    overlay, n = pending_edits_service.record_deleted_field(
        db_session, api_def.id, "invoiceNumber",
    )
    assert n == 2  # both docs' annotations gone
    assert "invoiceNumber" in overlay["deleted_fields"]
    assert overlay["added_fields"] == []      # add+delete cancels
    assert overlay["modifications"] == {}     # value mod dropped

    # Verify the actual annotation rows are gone
    after = db_session.query(Annotation).filter(
        Annotation.document_id.in_([d1.id, d2.id])
    ).count()
    assert after == 0


def test_record_deleted_field_idempotent(db_session):
    """Deleting twice doesn't duplicate the entry."""
    from app.services import pending_edits_service
    api_def, _d1, _d2 = _setup_api_def(db_session)
    pending_edits_service.record_deleted_field(db_session, api_def.id, "foo")
    overlay, _ = pending_edits_service.record_deleted_field(
        db_session, api_def.id, "foo",
    )
    assert overlay["deleted_fields"] == ["foo"]


# ── Phase 11b: optimizer filtering ────────────────────────────────────────────


# ── Phase 13: required-fields endpoint ────────────────────────────────────────


def test_required_fields_computes_union_minus_deleted(db_session):
    """The required-fields endpoint subtracts deleted, applies renames,
    and unions overlay.added_fields."""
    from app.services import pending_edits_service
    from app.api.v1.api_defs import get_required_fields

    api_def, _d1, _d2 = _setup_api_def(db_session)

    # No active OcrPromptVersion → fields come only from overlay
    pending_edits_service.record_added_field(db_session, api_def.id, "extraField", "string")
    pending_edits_service.record_added_field(db_session, api_def.id, "anotherOne", "string")
    pending_edits_service.record_deleted_field(db_session, api_def.id, "anotherOne")

    result = get_required_fields(api_def.id, db_session)
    assert "extraField" in result["fields"]
    assert "anotherOne" not in result["fields"]


# ── Phase 14b: cross-doc context builder ──────────────────────────────────────


def test_cross_doc_context_builder_collects_per_field_samples(db_session):
    """_build_cross_doc_context_for_diffs gathers a value list per field
    name across every confirmed sample of the ApiDef."""
    from app.ocr_optimizer.service.customer_iteration import (
        _build_cross_doc_context_for_diffs,
    )

    api_def, d1, d2 = _setup_api_def(db_session)
    _add_annotation(db_session, d1.id, "invoiceNumber", "ABC123")
    _add_annotation(db_session, d2.id, "invoiceNumber", "DEF456")

    # Bind both docs as samples
    api_def.config = {"sample_document_ids": [str(d1.id), str(d2.id)]}
    db_session.commit()

    diffs = [
        {"kind": "edit", "module_key": "invoice_number",
         "original_name": "invoiceNumber", "corrected_name": "invoiceNumber"},
    ]
    ctx = _build_cross_doc_context_for_diffs(db_session, api_def.id, diffs)
    assert "invoiceNumber" in ctx
    samples = ctx["invoiceNumber"]
    assert len(samples) == 2
    values = sorted(s["value"] for s in samples)
    assert values == ["ABC123", "DEF456"]


def test_pad_with_required_keys_idempotent():
    """N4 — _pad_with_required_keys fills missing keys with null and is
    idempotent across re-application."""
    from app.services.document_service import _pad_with_required_keys

    required = ["invoiceNumber", "supplierTier", "buyerName"]
    sd = [{"invoiceNumber": "ABC123", "buyerName": "Foo"}]
    padded = _pad_with_required_keys(sd, required)
    assert padded[0]["supplierTier"] is None
    assert padded[0]["invoiceNumber"] == "ABC123"
    # idempotent
    again = _pad_with_required_keys(padded, required)
    assert again == padded


def test_pad_with_required_keys_handles_empty_required():
    """No required → pass-through."""
    from app.services.document_service import _pad_with_required_keys
    sd = [{"a": 1}]
    assert _pad_with_required_keys(sd, []) == sd


def test_compute_required_field_set_helper(db_session):
    """The new pending_edits_service.compute_required_field_set helper
    must return union(modules, added) − deleted with renames applied."""
    from app.services import pending_edits_service

    api_def, _d1, _d2 = _setup_api_def(db_session)
    pending_edits_service.record_added_field(db_session, api_def.id, "newField", "string")
    pending_edits_service.record_deleted_field(db_session, api_def.id, "billFromName")
    fields = pending_edits_service.compute_required_field_set(db_session, api_def.id)
    assert "newField" in fields
    assert "billFromName" not in fields


# ── issue-1: monotonic parity union of confirmed-observed fields ──────────────


def _add_pr(db, doc_id, structured_data, version=1):
    from app.models.document import ProcessingResult
    pr = ProcessingResult(
        id=uuid.uuid4(),
        document_id=doc_id,
        version=version,
        processor_type="mock",
        model_name="mock",
        prompt_used="",
        raw_output={"text": ""},
        structured_data=structured_data,
        inferred_schema={},
        processing_time_ms=0,
    )
    db.add(pr)
    db.commit()
    return pr


def _confirm(db, doc_id, field_name="invoiceNumber", value="X"):
    """Make a doc 'confirmed' by adding a GT annotation (source=manual)."""
    from app.models.annotation import Annotation, AnnotationSource, FieldType
    ann = Annotation(
        id=uuid.uuid4(),
        document_id=doc_id,
        field_name=field_name,
        field_value=value,
        field_type=FieldType.string.value,
        source=AnnotationSource.manual.value,
        is_corrected=True,
    )
    db.add(ann)
    db.commit()
    return ann


def test_required_set_unions_confirmed_observed_fields(db_session):
    """A field the LLM produced on a CONFIRMED sample (but that is not a
    module) must persist in the required set — so uploading more samples
    never shrinks the cross-sample field set."""
    from app.services import pending_edits_service

    api_def, d1, _d2 = _setup_api_def(db_session)
    # Observed fields come from GROUND TRUTH (confirmed annotations), not from
    # the annotation-format structured_data. Add two GT fields.
    _confirm(db_session, d1.id, field_name="invoiceNumber", value="X")
    _confirm(db_session, d1.id, field_name="nameOfInvoice", value="INVOICE")

    fields = pending_edits_service.compute_required_field_set(db_session, api_def.id)
    assert "nameOfInvoice" in fields
    assert "invoiceNumber" in fields


def test_required_set_ignores_unconfirmed_observed_fields(db_session):
    """A sample with a ProcessingResult but NO GT annotation must NOT
    contribute (only GT-confirmed fields enter the parity set)."""
    from app.services import pending_edits_service

    api_def, _d1, d2 = _setup_api_def(db_session)
    # d2 has a ProcessingResult but NO GT annotation → not confirmed.
    _add_pr(db_session, d2.id, [{"junkField": "hallucinated"}])

    fields = pending_edits_service.compute_required_field_set(db_session, api_def.id)
    assert "junkField" not in fields


def test_required_set_excludes_annotation_wrapper_keys(db_session):
    """REGRESSION (Phase-26 bug): the observed source must be GT field names,
    never the annotation/normalized wrapper keys (id/keyName/value/confidence/
    bbox). A leaked `value` field corrupted downstream OCR
    (document_service._is_leaf_field collapse)."""
    from app.services import pending_edits_service

    api_def, d1, _d2 = _setup_api_def(db_session)
    # Even a GT annotation literally named "value" must be filtered out.
    _confirm(db_session, d1.id, field_name="invoiceNumber", value="X")
    _confirm(db_session, d1.id, field_name="value", value="junk")

    fields = pending_edits_service.compute_required_field_set(db_session, api_def.id)
    assert "invoiceNumber" in fields
    for wrapper in ("id", "keyName", "value", "confidence", "bbox"):
        assert wrapper not in fields, f"{wrapper} must never enter the required set"


def test_create_manual_annotation_upserts_structured_data(db_session):
    """Saving a manual field (the '其他文件已新增字段' / missing-field flow)
    must write the value into the doc's latest structured_data so the field
    view + JSON panel show it and it leaves the 待补充 section — not just
    create an invisible Annotation row."""
    from app.schemas.annotation import CreateAnnotationRequest
    from app.services import annotation_service

    api_def, d1, _d2 = _setup_api_def(db_session)
    _add_pr(db_session, d1.id, [{"invoiceNumber": "X"}])

    annotation_service.create_annotation(
        db_session, d1.id,
        CreateAnnotationRequest(
            field_name="PO", field_value="W1538012",
            field_type="string", source="manual",
        ),
    )

    from app.models.document import ProcessingResult
    pr = (
        db_session.query(ProcessingResult)
        .filter(ProcessingResult.document_id == d1.id)
        .order_by(ProcessingResult.version.desc())
        .first()
    )
    assert pr.structured_data[0]["PO"] == "W1538012"   # value now visible
    assert pr.structured_data[0]["invoiceNumber"] == "X"  # existing kept

    # overlay still mirrors the add so other docs see it in their section
    from app.services import pending_edits_service
    overlay = pending_edits_service.get_overlay(db_session, api_def.id)
    assert any(f["field_name"] == "PO" for f in overlay["added_fields"])


def test_create_manual_annotation_none_writes_null(db_session):
    """Ticking '无此字段' (field_value=None) records the key as null so the
    field is 'covered' (present) and leaves the 待补充 list."""
    from app.schemas.annotation import CreateAnnotationRequest
    from app.services import annotation_service
    from app.models.document import ProcessingResult

    _api, d1, _d2 = _setup_api_def(db_session)
    _add_pr(db_session, d1.id, [{"invoiceNumber": "X"}])

    annotation_service.create_annotation(
        db_session, d1.id,
        CreateAnnotationRequest(
            field_name="DO", field_value=None,
            field_type="string", source="manual",
        ),
    )
    pr = (
        db_session.query(ProcessingResult)
        .filter(ProcessingResult.document_id == d1.id)
        .order_by(ProcessingResult.version.desc())
        .first()
    )
    assert "DO" in pr.structured_data[0]
    assert pr.structured_data[0]["DO"] is None


def test_required_set_delete_wins_over_confirmed_observed(db_session):
    """A field the user explicitly deleted must NOT be resurrected by the
    confirmed-observed union (user intent beats observation)."""
    from app.services import pending_edits_service

    api_def, d1, _d2 = _setup_api_def(db_session)
    _confirm(db_session, d1.id)
    _add_pr(db_session, d1.id, [{"invoiceNumber": "X", "ghostField": "v"}])
    pending_edits_service.record_deleted_field(db_session, api_def.id, "ghostField")

    fields = pending_edits_service.compute_required_field_set(db_session, api_def.id)
    assert "ghostField" not in fields
    assert "invoiceNumber" in fields


def test_cross_doc_context_dedupes_identical_values(db_session):
    """Phase 15 — when the same field has the same value on multiple docs,
    collapse to a single entry with dup_count + dup_doc_filenames."""
    from app.ocr_optimizer.service.customer_iteration import (
        _build_cross_doc_context_for_diffs,
    )

    api_def, d1, d2 = _setup_api_def(db_session)
    # SAME value on both docs — should dedup
    _add_annotation(db_session, d1.id, "billFromName", "PANASONIC SDN BHD")
    _add_annotation(db_session, d2.id, "billFromName", "PANASONIC SDN BHD")
    api_def.config = {"sample_document_ids": [str(d1.id), str(d2.id)]}
    db_session.commit()

    diffs = [
        {"kind": "edit", "module_key": "bill_from_name",
         "original_name": "billFromName", "corrected_name": "billFromName"},
    ]
    ctx = _build_cross_doc_context_for_diffs(db_session, api_def.id, diffs)
    samples = ctx["billFromName"]
    # Collapsed to 1 entry with dup_count = 2
    assert len(samples) == 1
    assert samples[0]["dup_count"] == 2
    assert samples[0]["value"] == "PANASONIC SDN BHD"
    assert len(samples[0]["dup_doc_filenames"]) == 1  # the second doc's filename


# ── Phase 14c: per-round suggestion merge ─────────────────────────────────────


def test_merge_round_suggestions_appends_history():
    """Each round's update is APPENDED to the reflections list, not replacing."""
    from app.ocr_optimizer.service.persistence import _merge_round_suggestions

    initial = {
        "semantics": "v1",
        "reflections": [
            {"round": 0, "kind": "edit", "rationale": "fork-time", "summary": "—"},
        ],
    }
    merged = _merge_round_suggestions(
        previous=initial,
        new_text={"semantics": "v2", "most_common_feature": "<digits>"},
        round_no=1,
        kind="round",
        rationale="round-1 reflection",
    )
    assert merged["semantics"] == "v2"
    # History preserved AND extended
    assert len(merged["reflections"]) == 2
    assert merged["reflections"][0]["round"] == 0
    assert merged["reflections"][1]["round"] == 1
    assert merged["reflections"][1]["rationale"] == "round-1 reflection"


def test_merge_round_suggestions_handles_string_new_text():
    """Optimizer may return plain string; helper still appends a history entry."""
    from app.ocr_optimizer.service.persistence import _merge_round_suggestions

    merged = _merge_round_suggestions(
        previous=None,
        new_text="去掉 W1 前缀，只保留 6 位数字",
        round_no=2,
        kind="round",
        rationale="多张样本都显示前缀 W1 是 PO 头",
    )
    assert merged["reflections"][0]["summary"] == "去掉 W1 前缀，只保留 6 位数字"


def test_execute_pipeline_filters_deleted_modules_and_diffs():
    """The Phase 11b filter logic must drop modules + diffs matching
    deleted_field_names, supporting both camelCase and snake_case forms.
    """
    from app.ocr_optimizer.service.customer_iteration import _snake

    # Mock src_modules (just need .module_key attribute)
    class M:
        def __init__(self, k): self.module_key = k

    src_modules = [
        M("invoice_number"),
        M("invoice_date"),
        M("bill_from_name"),
    ]
    diffs = [
        {"kind": "edit", "module_key": "invoice_number",
         "original_name": "invoiceNumber", "corrected_name": "invoiceNumber"},
        {"kind": "edit", "module_key": "invoice_date",
         "original_name": "invoiceDate", "corrected_name": "invoiceDate"},
        {"kind": "add", "module_key": "billFromCompanyName",
         "original_name": "billFromCompanyName", "corrected_name": "salerName"},
    ]
    deleted_field_names = {"invoiceNumber"}  # camelCase form

    # Replicate the filtering logic from _execute_pipeline Phase 11b
    deleted_snake = {_snake(f) for f in deleted_field_names if f}
    all_deleted = deleted_field_names | deleted_snake  # {"invoiceNumber", "invoice_number"}

    deleted_module_keys = {m.module_key for m in src_modules if m.module_key in all_deleted}
    assert deleted_module_keys == {"invoice_number"}

    surviving = [m for m in src_modules if m.module_key not in deleted_module_keys]
    assert [m.module_key for m in surviving] == ["invoice_date", "bill_from_name"]

    def _is_deleted_diff(d):
        for v in (d.get("module_key") or "",
                  d.get("original_name") or "",
                  d.get("corrected_name") or ""):
            if v and (v in all_deleted or _snake(v) in all_deleted):
                return True
        return False

    surviving_diffs = [d for d in diffs if not _is_deleted_diff(d)]
    assert len(surviving_diffs) == 2
    assert surviving_diffs[0]["module_key"] == "invoice_date"
    assert surviving_diffs[1]["module_key"] == "billFromCompanyName"


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


# ── Regression: customize pipeline must not crash on add diff with module_key

def test_add_specs_reflection_lookup_no_index_failure(db_session):
    """User-reported bug: pipeline crashed on `diffs.index(d)` for kind=add
    diffs (and Phase-7 promoted dicts).

    Repros the minimal scenario: build add_specs with a synth dict that
    isn't in `diffs`, then verify our reflection_key lookup mirrors the
    reflector's keying (module_key first, _new_{idx} fallback).
    """
    from app.ocr_optimizer.reflection.reflector import ReflectionResult

    # Mimic a customer who added a brand-new field "billFromCompanyName"
    # then renamed it to "salerName" inside the SAME draft.
    user_diff = {
        "kind": "add",
        "module_key": "bill_from_company_name",
        "original_name": "billFromCompanyName",
        "corrected_name": "salerName",
        "original_value": "HOU TIAN TRANSPORT & TRADING SDN BHD",
        "corrected_value": "HOU TIAN TRANSPORT & TRADING SDN BHD",
        "original_format": "text",
        "corrected_format": "text",
    }
    diffs = [user_diff]
    reflections = {
        # Reflector keys by module_key when present
        "bill_from_company_name": ReflectionResult(
            module_key="bill_from_company_name", kind="add", diff=user_diff,
            fix_suggestions=["customer added new field"],
            rationale_summary="新增字段",
        ),
    }

    # Replicate the lookup logic from _fork_api_definition (post-fix)
    add_specs: list[tuple[dict, str | None]] = []
    for orig_idx, d in enumerate(diffs):
        if d.get("kind") == "add":
            rk = d.get("module_key") or f"_new_{orig_idx}"
            add_specs.append((d, rk))

    # This used to crash with `diffs.index(d) is not in list` when d was
    # a Phase-7 synth. With the new tuple-based lookup it just resolves.
    resolved = []
    for d, reflection_key in add_specs:
        r = reflections.get(reflection_key)
        resolved.append((d.get("module_key"), reflection_key, r is not None))

    assert resolved == [("bill_from_company_name", "bill_from_company_name", True)]


def test_promoted_orphan_edit_uses_module_key_for_reflection(db_session):
    """Phase 7 promoted-orphan case: the synth dict's reflection should be
    looked up by the ORIGINAL module_key (which is how the reflector saw it
    when kind was still 'edit'), not by '_new_{idx}'."""
    from app.ocr_optimizer.reflection.reflector import ReflectionResult

    orphan = {
        "kind": "edit",
        "module_key": "bill_from_address",  # not in MY src_module_keys
        "original_name": "billFromAddress",
        "corrected_name": "salerAddress",
        "original_value": "Lot 10, Jalan 13/2",
        "corrected_value": "Lot 10, Jalan 13/2",
        "original_format": "text",
        "corrected_format": "text",
    }
    diffs = [orphan]
    reflections = {
        "bill_from_address": ReflectionResult(
            module_key="bill_from_address", kind="edit", diff=orphan,
            fix_suggestions=["…"], rationale_summary="…",
        ),
    }
    src_module_keys: set[str] = set()  # nothing matches

    add_specs: list[tuple[dict, str | None]] = []
    for orig_idx, d in enumerate(diffs):
        if d.get("kind") == "edit":
            mk = d.get("module_key")
            if mk and mk not in src_module_keys:
                synth = dict(d)
                synth["kind"] = "add"
                add_specs.append((synth, mk))  # reuse orig module_key

    # synth is NOT in original diffs — old code did diffs.index(synth) and
    # crashed with ValueError. New code uses the captured reflection_key.
    assert len(add_specs) == 1
    synth, rk = add_specs[0]
    assert synth is not diffs[0]
    assert rk == "bill_from_address"
    assert reflections.get(rk) is not None
