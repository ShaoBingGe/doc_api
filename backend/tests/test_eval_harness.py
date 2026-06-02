"""
Phase 0 — eval harness scoring tests (offline, no Gemini).

Exercises the PURE scoring path (score_outputs / benchmark_ab) with
hand-crafted OCR outputs + ground truth, so the harness numbers are pinned
without needing a real OCR backend.
"""

from __future__ import annotations

from app.ocr_optimizer.eval import (
    ModuleSpec,
    benchmark_ab,
    score_outputs,
)


# Two scalar modules at array-record root: invoiceNumber + billFromName.
# json_path "$[*].x" slices the field x out of each record.
_MODULES = [
    ModuleSpec(module_key="invoice_number", json_path="$[*].invoiceNumber",
               schema_fragment={"type": "STRING"}, display_name="发票号"),
    ModuleSpec(module_key="bill_from_name", json_path="$[*].billFromName",
               schema_fragment={"type": "STRING"}, display_name="开票方"),
]


def _gt():
    return {
        "doc1": [{"invoiceNumber": "INV-1", "billFromName": "ACME"}],
        "doc2": [{"invoiceNumber": "INV-2", "billFromName": "BETA"}],
    }


def test_score_perfect_match_is_one():
    ocr = {
        "doc1": [{"invoiceNumber": "INV-1", "billFromName": "ACME"}],
        "doc2": [{"invoiceNumber": "INV-2", "billFromName": "BETA"}],
    }
    report = score_outputs(_MODULES, ocr, _gt())
    assert report.overall_accuracy == 1.0
    mm = report.module_map()
    assert mm["invoice_number"].matched_count == 2
    assert mm["bill_from_name"].matched_count == 2


def test_score_partial_miss_lowers_only_that_module():
    # billFromName wrong on doc2 only; invoiceNumber perfect.
    ocr = {
        "doc1": [{"invoiceNumber": "INV-1", "billFromName": "ACME"}],
        "doc2": [{"invoiceNumber": "INV-2", "billFromName": "WRONG"}],
    }
    report = score_outputs(_MODULES, ocr, _gt())
    mm = report.module_map()
    assert mm["invoice_number"].accuracy == 1.0
    assert mm["bill_from_name"].accuracy < 1.0
    assert mm["bill_from_name"].matched_count == 1
    # overall is the mean of module accuracies → strictly between the two
    assert 0.0 < report.overall_accuracy < 1.0


def test_ocr_error_marker_scores_zero_and_is_collected():
    ocr = {
        "doc1": {"_error": "gemini timeout"},
        "doc2": [{"invoiceNumber": "INV-2", "billFromName": "BETA"}],
    }
    report = score_outputs(_MODULES, ocr, _gt())
    assert "doc1" in report.ocr_error_doc_ids
    mm = report.module_map()
    # doc1 scored 0 for both modules, doc2 perfect → each module mean = 0.5
    assert mm["invoice_number"].accuracy == 0.5
    assert mm["bill_from_name"].accuracy == 0.5


def test_benchmark_ab_reports_delta_and_regressions(monkeypatch):
    """benchmark_ab wiring: two prompts over identical inputs → per-module
    delta. We stub the OCR call so it stays offline and the candidate (B)
    fixes the doc2 billFromName miss that the baseline (A) had."""
    import uuid as _uuid
    from app.ocr_optimizer.service import ocr_runner

    a_out = {
        "doc1": [{"invoiceNumber": "INV-1", "billFromName": "ACME"}],
        "doc2": [{"invoiceNumber": "INV-2", "billFromName": "WRONG"}],
    }
    b_out = {
        "doc1": [{"invoiceNumber": "INV-1", "billFromName": "ACME"}],
        "doc2": [{"invoiceNumber": "INV-2", "billFromName": "BETA"}],
    }

    def fake_run(db, *, composed_prompt, **kw):
        # The candidate prompt is tagged with this marker by the test.
        return b_out if "CANDIDATE" in composed_prompt else a_out

    monkeypatch.setattr(ocr_runner, "run_ocr_on_samples", fake_run)

    result = benchmark_ab(
        None,
        modules_a=_MODULES, modules_b=_MODULES,
        sample_doc_ids=[_uuid.uuid4()],   # ignored by the stub
        prompt_a="BASELINE prompt",
        schema_a=None,
        prompt_b="CANDIDATE prompt",
        schema_b=None,
        ground_truths=_gt(),
    )
    assert result["overall_delta"] > 0
    assert result["per_module_delta"]["bill_from_name"] > 0
    assert result["regressed_modules"] == []
