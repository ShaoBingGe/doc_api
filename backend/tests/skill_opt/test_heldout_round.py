"""L1 integration — held-out gate INSIDE a real _run_one_round (token-free).

Proves the production wiring: with SKILL_HELDOUT_GATE on, a round scores/selects
on the held-out VAL split (val samples deliberately wrong → low overall_accuracy),
while with it off the round scores on all samples. Mock OCR (monkeypatched) →
zero VLM/LLM. Closes the flag-ON integration gap left by the unit tests.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings


@pytest.fixture()
def mock_env(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "DEFAULT_PROCESSOR", "mock", raising=False)
    monkeypatch.setattr(s, "LLM_FALLBACK_CHAIN", "mock|", raising=False)
    monkeypatch.setattr(s, "SKILL_HELDOUT_VAL_FRAC", 0.4, raising=False)
    yield s


def _seed(db, n=5):
    from app.models.annotation import Annotation
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document, DocumentStatus
    from app.ocr_optimizer.models import (
        OcrModule, OcrPromptVersion, PromptVersionStatus,
        OcrOptimizationRun, RunStatus,
    )

    api = ApiDefinition(
        id=uuid.uuid4(), name="heldout-src", api_code=f"ho-{uuid.uuid4().hex[:8]}",
        status=ApiDefinitionStatus.active, processor_type="mock", model_name=None,
        config={"sample_document_ids": []},
    )
    db.add(api)
    ver = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="1",
        status=PromptVersionStatus.active.value,
        composed_prompt="extract", composed_schema={"type": "object"},
    )
    db.add(ver)
    db.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=ver.id, module_key="invoice_number",
        display_name="发票号", json_path="$.invoiceNumber", ocr_prompt="find",
        description="no", schema_fragment={"type": "string"}, order_index=1, status="active",
    ))
    sample_ids, gts = [], {}
    for i in range(n):
        doc = Document(
            id=uuid.uuid4(), filename=f"s{i}.pdf", file_type="pdf", file_size=10,
            status=DocumentStatus.completed, storage_path=f"/tmp/ho-{i}.pdf",
            api_definition_id=api.id,
        )
        db.add(doc)
        db.add(Annotation(
            id=uuid.uuid4(), document_id=doc.id, field_name="invoiceNumber",
            field_value=f"INV-{i:03d}", field_type="string", source="manual", is_corrected=True,
        ))
        sample_ids.append(doc.id)
        gts[str(doc.id)] = {"invoiceNumber": f"INV-{i:03d}"}
    run = OcrOptimizationRun(
        id=uuid.uuid4(), api_definition_id=api.id, starting_version_id=ver.id,
        status=RunStatus.running.value, sample_document_ids=[str(s) for s in sample_ids],
        llm_provider="mock|",
    )
    db.add(run)
    db.commit()
    return api, ver, run, sample_ids, gts


def _patch_ocr(monkeypatch, sample_ids, gts, n_correct):
    """Mock OCR: first n_correct samples match GT (train), the rest are WRONG
    (the held-out val split)."""
    from app.ocr_optimizer.service import run_orchestrator

    def fake_ocr(db, *, sample_document_ids, composed_prompt, composed_schema,
                 processor_spec, model_name, **kw):
        out = {}
        for idx, sid in enumerate(sample_document_ids):
            gt_val = gts[str(sid)]["invoiceNumber"]
            out[str(sid)] = {"invoiceNumber": gt_val if idx < n_correct else "WRONG"}
        return out

    monkeypatch.setattr(run_orchestrator.ocr_runner, "run_ocr_on_samples", fake_ocr)


def test_heldout_gate_round_scores_on_val(db_session, mock_env, monkeypatch):
    """Flag ON: trailing 2 of 5 samples (val) are wrong → overall_accuracy is the
    VAL score (0.0), NOT the all-sample 0.6. Flag OFF: it's the all-sample 0.6."""
    from app.ocr_optimizer.service.run_orchestrator import _run_one_round

    api, ver, run, sample_ids, gts = _seed(db_session, n=5)
    _patch_ocr(monkeypatch, sample_ids, gts, n_correct=3)  # 3 train ok, 2 val wrong

    # Flag OFF → all-sample accuracy = 3/5 = 0.6
    monkeypatch.setattr(mock_env, "SKILL_HELDOUT_GATE", False, raising=False)
    rnd_off = _run_one_round(
        db_session, run=run, round_num=1, api_def=api, current_version=ver,
        sample_ids=sample_ids, ground_truths=gts,
        metrics={"total_ocr_calls": 0, "total_llm_calls": 0}, enable_meta=False,
    )
    assert abs(rnd_off.overall_accuracy - 0.6) < 1e-6

    # Flag ON → held-out val accuracy (the 2 trailing wrong) = 0.0
    monkeypatch.setattr(mock_env, "SKILL_HELDOUT_GATE", True, raising=False)
    rnd_on = _run_one_round(
        db_session, run=run, round_num=2, api_def=api, current_version=ver,
        sample_ids=sample_ids, ground_truths=gts,
        metrics={"total_ocr_calls": 0, "total_llm_calls": 0}, enable_meta=False,
    )
    assert abs(rnd_on.overall_accuracy - 0.0) < 1e-6
