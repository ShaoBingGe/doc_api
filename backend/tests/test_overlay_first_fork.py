"""
Phase 23 — Overlay-as-single-source-of-truth integration tests.

Locks the 5-invariant contract for the customize → fork pipeline:

  After customize completes, all of the following MUST agree about the
  customer's final field set:

    1. OcrModule.module_key       (new active version's modules)
    2. composed_prompt            (LLM-facing schema reference)
    3. ProcessingResult.structured_data top-level keys
    4. Annotation.field_name      (per-doc GT labels)
    5. /required-fields response  (what the UI shows)

  All five derive from pending_edits — the SINGLE SOURCE OF TRUTH. If
  any of them drifts, the user sees inconsistent "field names" across
  workspace / JSON / optimizer / saved API. Phase 19 collapsed fork onto
  source; Phase 23 makes overlay the canonical input rather than diffs
  scraped from in-memory drafts.
"""

from __future__ import annotations

import uuid


# ── Test fixtures ─────────────────────────────────────────────────────────────


def _setup_full_apidef(db, *, with_modules=True):
    """Create an ApiDef + 3 sample docs + an active OcrPromptVersion + a
    starter module set that mirrors what preset_init would emit.

    Returns (api_def, [doc1, doc2, doc3], version, modules).
    """
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document
    from app.ocr_optimizer.models import (
        OcrModule, OcrPromptVersion, PromptVersionStatus, VersionOrigin,
    )

    api_def = ApiDefinition(
        id=uuid.uuid4(),
        name="phase23_my_test",
        api_code=f"my-test-{uuid.uuid4().hex[:6]}",
        description="",
        status=ApiDefinitionStatus.draft.value,
        version=1,
        processor_type="mock",
        model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api_def)
    db.flush()

    docs = []
    for i in range(3):
        d = Document(
            id=uuid.uuid4(),
            filename=f"sample_{i+1}.pdf",
            file_type="pdf",
            file_size=1,
            storage_path=f"/tmp/sample_{i+1}.pdf",
            status="completed",
            api_definition_id=api_def.id,
        )
        db.add(d)
        docs.append(d)

    api_def.config = {"sample_document_ids": [str(d.id) for d in docs]}

    version = None
    modules = []
    if with_modules:
        version = OcrPromptVersion(
            id=uuid.uuid4(),
            api_definition_id=api_def.id,
            version="1",
            status=PromptVersionStatus.active.value,
            origin=VersionOrigin.init.value,
            composed_prompt="(stub)",
            composed_schema={"type": "object"},
            notes="seed v1",
        )
        db.add(version)
        api_def.prompt_version_id = version.id
        starter_fields = [
            ("invoice_number", "发票号码识别", "$[*].invoiceNumber"),
            ("invoice_date", "发票日期识别", "$[*].invoiceDate"),
            ("bill_from_name", "开票方名称识别", "$[*].billFromName"),
            ("bill_to_name", "收票方名称识别", "$[*].billToName"),
            ("purchase_order_number", "采购订单号识别", "$[*].purchaseOrderNumber"),
            ("delivery_order_number", "发货单号识别", "$[*].deliveryOrderNumber"),
            ("total_tax_amount", "总税额识别", "$[*].totalTaxAmount"),
        ]
        for i, (mk, dn, jp) in enumerate(starter_fields):
            m = OcrModule(
                id=uuid.uuid4(),
                prompt_version_id=version.id,
                module_key=mk,
                display_name=dn,
                description=f"{dn} module",
                json_path=jp,
                schema_fragment={"type": "STRING"},
                ocr_suggestions={},
                ocr_prompt=f"识别 {dn}",
                skill_ids=[],
                order_index=i,
                status="active",
            )
            db.add(m)
            modules.append(m)

    db.commit()
    return api_def, docs, version, modules


# ── 23.2: fork reads overlay (not just diffs) ─────────────────────────────────


def test_fork_applies_overlay_renames_when_diffs_silent(db_session):
    """The user's #1 reported bug: overlay has billFromName→salerCompany
    but the customize diff only carries a value edit (no corrected_name).
    Phase 23.2 must rename the module purely from overlay."""
    from app.services import pending_edits_service
    from app.ocr_optimizer.service.customer_iteration import _fork_api_definition

    api_def, docs, version, modules = _setup_full_apidef(db_session)

    # Overlay: rename billFromName → salerCompany (set via FieldEditPanel +
    # "保存到模板" path). The diff list below contains NO corrected_name
    # for this field — simulates the user who never clicked into the rename
    # via the customize-submit codepath.
    pending_edits_service.record_rename(
        db_session, api_def.id, "billFromName", "salerCompany",
    )
    diffs = [
        {"kind": "edit", "module_key": "bill_from_name",
         "original_name": "billFromName",
         "corrected_name": "billFromName",  # ← silent: no rename here
         "corrected_value": "Mapei Malaysia SDN BHD"},
    ]

    new_api, new_version, new_modules = _fork_api_definition(
        db_session,
        src_api=api_def, src_version=version, src_modules=modules,
        diffs=diffs, reflections={}, user_id=None,
    )

    new_keys = {m.module_key for m in new_modules}
    new_paths = {m.json_path for m in new_modules}
    assert "saler_company" in new_keys, \
        f"Overlay-driven rename should produce saler_company; got {new_keys}"
    assert "bill_from_name" not in new_keys, \
        "Old module_key must be replaced after rename"
    assert "$[*].salerCompany" in new_paths, \
        f"json_path must follow rename; got {new_paths}"


def test_fork_applies_overlay_deletes(db_session):
    """Overlay-deleted fields must NOT appear in new modules even if a
    diff still references them (e.g. customer deleted after editing)."""
    from app.services import pending_edits_service
    from app.ocr_optimizer.service.customer_iteration import _fork_api_definition

    api_def, _docs, version, modules = _setup_full_apidef(db_session)

    pending_edits_service.record_deleted_field(
        db_session, api_def.id, "totalTaxAmount",
    )
    # Diff still has an old edit — should be ignored
    diffs = [
        {"kind": "edit", "module_key": "total_tax_amount",
         "original_name": "totalTaxAmount",
         "corrected_name": "totalTaxAmount",
         "corrected_value": "0.00"},
    ]
    _, _, new_modules = _fork_api_definition(
        db_session,
        src_api=api_def, src_version=version, src_modules=modules,
        diffs=diffs, reflections={}, user_id=None,
    )
    new_keys = {m.module_key for m in new_modules}
    assert "total_tax_amount" not in new_keys


def test_fork_creates_modules_for_overlay_adds(db_session):
    """Overlay added_fields must become real OcrModule rows on the new
    version, even if the customize diff list is empty."""
    from app.services import pending_edits_service
    from app.ocr_optimizer.service.customer_iteration import _fork_api_definition

    api_def, _docs, version, modules = _setup_full_apidef(db_session)

    pending_edits_service.record_added_field(
        db_session, api_def.id,
        field_name="supplierTier",
        field_type="string",
        description="A/B/C tier",
    )
    diffs: list[dict] = []  # empty — overlay carries the intent

    _, _, new_modules = _fork_api_definition(
        db_session,
        src_api=api_def, src_version=version, src_modules=modules,
        diffs=diffs, reflections={}, user_id=None,
    )
    new_keys = {m.module_key for m in new_modules}
    assert "supplier_tier" in new_keys


# ── 23.3: structured_data key rewrite ─────────────────────────────────────────


def test_structured_data_rewrite_renames_top_level_keys():
    """Phase 23.3 — _rewrite_structured_data_keys updates top-level
    keys per renames map; idempotent on re-apply; ignores nested
    objects (renames are top-level by design)."""
    from app.services.document_service import _rewrite_structured_data_keys

    sd = [{
        "billFromName": "PANASONIC",
        "billToName": "PP CHIN HIN",
        "invoiceNumber": "ABC123",
        "detailOfGoodsOrServices": [
            {"description": "item1", "billFromName": "nested-should-not-change"},
        ],
    }]
    renames = {"billFromName": "salerCompany", "billToName": "buyerCompany"}
    out = _rewrite_structured_data_keys(sd, renames)
    assert "salerCompany" in out[0] and "billFromName" not in out[0]
    assert "buyerCompany" in out[0] and "billToName" not in out[0]
    assert out[0]["invoiceNumber"] == "ABC123"
    # Nested top-level: the array-item billFromName should remain
    # untouched (overlay renames target top-level fields).
    assert out[0]["detailOfGoodsOrServices"][0]["billFromName"] == "nested-should-not-change"

    # idempotent
    again = _rewrite_structured_data_keys(out, renames)
    assert again == out


def test_structured_data_rewrite_handles_dict_shape():
    """structured_data is normally list[dict], but legacy paths may pass
    a single dict. Helper must handle both."""
    from app.services.document_service import _rewrite_structured_data_keys

    sd = {"billFromName": "X"}
    out = _rewrite_structured_data_keys(sd, {"billFromName": "salerCompany"})
    assert out == {"salerCompany": "X"}


# ── Bonus: verify the helper that ties it together post-fork ──────────────────


def test_post_customize_sweep_rewrites_all_docs(db_session):
    """After fork, the post-customize sweep must update structured_data
    on every ProcessingResult of every doc bound to the source ApiDef."""
    from app.models.document import ProcessingResult
    from app.services import pending_edits_service
    from app.ocr_optimizer.service.customer_iteration import (
        _rewrite_all_docs_structured_data,
    )

    api_def, docs, _version, _modules = _setup_full_apidef(db_session)

    # Seed each doc with a ProcessingResult containing the old key
    for i, doc in enumerate(docs):
        pr = ProcessingResult(
            id=uuid.uuid4(),
            document_id=doc.id,
            version=1,
            processor_type="mock",
            model_name="mock",
            prompt_used="",
            raw_output={"text": ""},
            structured_data=[{"billFromName": f"vendor_{i}",
                             "invoiceNumber": f"INV-{i}"}],
            inferred_schema={},
            processing_time_ms=0,
        )
        db_session.add(pr)
    db_session.commit()

    pending_edits_service.record_rename(
        db_session, api_def.id, "billFromName", "salerCompany",
    )

    n = _rewrite_all_docs_structured_data(
        db_session, api_def.id, {"billFromName": "salerCompany"},
    )
    assert n == 3  # one rewrite per doc

    # Verify each doc's latest structured_data has the new key
    for doc in docs:
        pr = (
            db_session.query(ProcessingResult)
            .filter(ProcessingResult.document_id == doc.id)
            .order_by(ProcessingResult.version.desc())
            .first()
        )
        assert "salerCompany" in pr.structured_data[0]
        assert "billFromName" not in pr.structured_data[0]
