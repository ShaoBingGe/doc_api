"""field_accuracy_timeline — per-round × per-field convergence dashboard data."""

import uuid

import pytest

from app.ocr_optimizer.service.persistence import field_accuracy_timeline


@pytest.fixture
def db_session():
    from app.core.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _build_run_with_rounds(db):
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.ocr_optimizer.models import (
        OcrModule, OcrModuleIteration, OcrOptimizationRound,
        OcrOptimizationRun, OcrPromptVersion, PromptVersionStatus, VersionOrigin,
    )

    api = ApiDefinition(
        id=uuid.uuid4(), name="fa", api_code=f"fa-{uuid.uuid4().hex[:6]}",
        description="", status=ApiDefinitionStatus.draft.value, version=1,
        processor_type="mock", model_name="mock",
        response_schema={"type": "object", "properties": {}},
    )
    db.add(api)
    db.flush()

    ver = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="1",
        status=PromptVersionStatus.active.value, origin=VersionOrigin.init.value,
        composed_prompt="x", composed_schema={}, country_global_text="",
    )
    db.add(ver)
    db.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=ver.id, module_key="invoice_number",
        display_name="发票号码识别", description="d", json_path="$[*].invoiceNumber",
        schema_fragment={}, ocr_suggestions={}, ocr_prompt="p", order_index=1,
    ))

    run = OcrOptimizationRun(
        id=uuid.uuid4(), api_definition_id=api.id, starting_version_id=ver.id,
        status="completed", max_rounds=3, target_accuracy=0.95,
        sample_document_ids=[], llm_provider="mock",
    )
    db.add(run)
    db.flush()

    # Two rounds, accuracy climbing 0.6 → 1.0 for the field.
    for rn, (overall, field_acc) in enumerate([(0.6, 0.6), (1.0, 1.0)], start=1):
        rnd = OcrOptimizationRound(
            id=uuid.uuid4(), run_id=run.id, round_num=rn,
            prompt_version_id=ver.id, overall_accuracy=overall, phase="completed",
        )
        db.add(rnd)
        db.flush()
        db.add(OcrModuleIteration(
            id=uuid.uuid4(), round_id=rnd.id, module_id=uuid.uuid4(),
            module_key="invoice_number", per_sample_results=[],
            aggregate_accuracy=field_acc,
        ))
    db.commit()
    return run


def test_timeline_shape_and_values(db_session):
    run = _build_run_with_rounds(db_session)
    out = field_accuracy_timeline(db_session, run.id)

    assert out["run_id"] == str(run.id)
    assert out["fields"] == [{"module_key": "invoice_number", "display_name": "发票号码识别"}]
    assert [r["round_num"] for r in out["rounds"]] == [1, 2]
    assert out["rounds"][0]["overall_accuracy"] == 0.6
    assert out["rounds"][0]["fields"]["invoice_number"] == 0.6
    assert out["rounds"][1]["fields"]["invoice_number"] == 1.0


def test_unknown_run_raises():
    from app.core.exceptions import NotFoundError
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        with pytest.raises(NotFoundError):
            field_accuracy_timeline(db, uuid.uuid4())
    finally:
        db.close()
