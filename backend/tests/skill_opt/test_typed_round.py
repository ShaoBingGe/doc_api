"""ADR-002 L1 integration — typed-edit path INSIDE a real _run_one_round (token-free).

Proves the production wiring end-to-end: with SKILL_TYPED_EDITS on, a round turns the
optimizer's `edits` into a bounded rule-section update on the NEW version's module
(frozen body), and accumulates edit-op meta into run.metrics. Mock OCR + mock
optimizer/verify → zero VLM/LLM.
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
    monkeypatch.setattr(s, "SKILL_HELDOUT_GATE", False, raising=False)
    yield s


def _seed(db, n=4):
    from app.models.annotation import Annotation
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document, DocumentStatus
    from app.ocr_optimizer.models import (
        OcrModule, OcrOptimizationRun, OcrPromptVersion, PromptVersionStatus, RunStatus,
    )

    api = ApiDefinition(
        id=uuid.uuid4(), name="typed-src", api_code=f"ty-{uuid.uuid4().hex[:8]}",
        status=ApiDefinitionStatus.active, processor_type="mock", model_name=None,
        config={"sample_document_ids": []},
    )
    db.add(api)
    ver = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="1",
        status=PromptVersionStatus.active.value,
        composed_prompt="extract", composed_schema={"type": "object"},
        country_global_text="",
    )
    db.add(ver)
    db.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=ver.id, module_key="invoice_number",
        display_name="发票号", json_path="$.invoiceNumber", ocr_prompt="find it",
        description="no", schema_fragment={"type": "string"}, order_index=1, status="active",
    ))
    sample_ids, gts = [], {}
    for i in range(n):
        doc = Document(
            id=uuid.uuid4(), filename=f"s{i}.pdf", file_type="pdf", file_size=10,
            status=DocumentStatus.completed, storage_path=f"/tmp/ty-{i}.pdf",
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


def _patch(monkeypatch, gts):
    """Mock OCR (all WRONG → field under target) + optimizer (returns typed edits)
    + verify (accept)."""
    from app.ocr_optimizer.service import run_orchestrator as ro

    def fake_ocr(db, *, sample_document_ids, **kw):
        return {str(sid): {"invoiceNumber": "WRONG"} for sid in sample_document_ids}

    def fake_optimize(*, module, iteration, history, processor_spec, model_name, meta_hint=""):
        return {
            "aggregate_diff": {"differences_description": "", "differences_reason_analysis": ""},
            "optimization_suggestion": "add a prefix rule",
            "new_ocr_suggestions": None, "new_description": None, "new_ocr_prompt": None,
            "skill_feedback": "", "meta_hint_seen": meta_hint,
            "edits": [{"op": "append", "target": module.module_key,
                       "content": "发票号必为 INV-\\d{3} 形式", "source_type": "failure",
                       "kind": "SKILL_DEFECT"}],
        }

    def fake_verify(*, module, iteration, proposed, processor_spec, model_name):
        return {"verdict": "accept", "reasoning": ""}

    monkeypatch.setattr(ro.ocr_runner, "run_ocr_on_samples", fake_ocr)
    monkeypatch.setattr(ro.module_optimizer, "optimize_module", fake_optimize)
    monkeypatch.setattr(ro.module_optimizer, "verify_module_fix", fake_verify)


def test_typed_round_writes_rule_section_and_meta(db_session, mock_env, monkeypatch):
    from app.ocr_optimizer.models import OcrModule
    from app.ocr_optimizer.service.run_orchestrator import _run_one_round

    monkeypatch.setattr(mock_env, "SKILL_TYPED_EDITS", True, raising=False)
    api, ver, run, sample_ids, gts = _seed(db_session, n=4)
    _patch(monkeypatch, gts)

    rnd = _run_one_round(
        db_session, run=run, round_num=1, api_def=api, current_version=ver,
        sample_ids=sample_ids, ground_truths=gts,
        metrics={"total_ocr_calls": 0, "total_llm_calls": 0}, enable_meta=False,
    )

    # the round produced a next version whose module carries the typed rule section
    new_mod = (
        db_session.query(OcrModule)
        .filter(OcrModule.prompt_version_id == rnd.next_version_id,
                OcrModule.module_key == "invoice_number")
        .first()
    )
    assert new_mod is not None
    assert "INV-" in (new_mod.rule_edits_text or "")          # edit applied to rule section
    assert "## [field:invoice_number]" in new_mod.rule_edits_text
    assert new_mod.ocr_prompt == "find it"                    # body FROZEN (not rewritten)

    # meta memory accumulated the accepted 'append'
    db_session.refresh(run)
    meta = (run.metrics or {}).get("meta_memory") or {}
    assert meta.get("by_op", {}).get("append", {}).get("accepted", 0) >= 1


def test_typed_round_off_uses_wholesale_path(db_session, mock_env, monkeypatch):
    """Flag OFF → rule section stays empty (wholesale-rewrite path); body may change."""
    from app.ocr_optimizer.models import OcrModule
    from app.ocr_optimizer.service.run_orchestrator import _run_one_round

    monkeypatch.setattr(mock_env, "SKILL_TYPED_EDITS", False, raising=False)
    api, ver, run, sample_ids, gts = _seed(db_session, n=4)

    from app.ocr_optimizer.service import run_orchestrator as ro

    def fake_ocr(db, *, sample_document_ids, **kw):
        return {str(sid): {"invoiceNumber": "WRONG"} for sid in sample_document_ids}

    def fake_optimize(*, module, iteration, history, processor_spec, model_name, meta_hint=""):
        return {
            "aggregate_diff": {"differences_description": "", "differences_reason_analysis": ""},
            "optimization_suggestion": "", "new_ocr_suggestions": None,
            "new_description": None, "new_ocr_prompt": "REWRITTEN BODY", "skill_feedback": "",
            "edits": None,
        }

    def fake_verify(*, module, iteration, proposed, processor_spec, model_name):
        return {"verdict": "accept", "reasoning": ""}

    monkeypatch.setattr(ro.ocr_runner, "run_ocr_on_samples", fake_ocr)
    monkeypatch.setattr(ro.module_optimizer, "optimize_module", fake_optimize)
    monkeypatch.setattr(ro.module_optimizer, "verify_module_fix", fake_verify)

    rnd = _run_one_round(
        db_session, run=run, round_num=1, api_def=api, current_version=ver,
        sample_ids=sample_ids, ground_truths=gts,
        metrics={"total_ocr_calls": 0, "total_llm_calls": 0}, enable_meta=False,
    )
    new_mod = (
        db_session.query(OcrModule)
        .filter(OcrModule.prompt_version_id == rnd.next_version_id,
                OcrModule.module_key == "invoice_number")
        .first()
    )
    assert (new_mod.rule_edits_text or "") == ""              # no rule section (OFF)
    assert new_mod.ocr_prompt == "REWRITTEN BODY"             # wholesale path applied
