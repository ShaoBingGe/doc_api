"""
Golden loop (req 3): deviations → diffs → reflection.
"""

from __future__ import annotations


def test_load_golden_reads_committed_my_set():
    from app.ocr_optimizer.eval.golden_loop import load_golden
    g = load_golden("MY")
    assert len(g) > 0, "committed MY golden set should load"
    # each entry: GT is a non-empty list-wrapped record
    any_entry = next(iter(g.values()))
    assert isinstance(any_entry["gt"], list) and any_entry["gt"]
    assert any_entry["fields"] >= 12   # completeness threshold


def test_deviations_to_diffs_shape():
    from app.ocr_optimizer.eval.golden_loop import deviations_to_diffs
    devs = [
        {"module_key": "invoice_number", "doc_id": "d1",
         "expected": ["INV-1"], "got": ["WRONG"]},
        {"module_key": "currency", "doc_id": "d1",
         "expected": ["MYR"], "got": [None]},
    ]
    mbk = {
        "invoice_number": {"display_name": "invoiceNumber", "json_path": "$[*].invoiceNumber"},
        "currency": {"display_name": "currency", "json_path": "$[*].currency"},
    }
    diffs = deviations_to_diffs(devs, mbk)
    assert len(diffs) == 2
    d0 = diffs[0]
    assert d0["kind"] == "edit"
    assert d0["module_key"] == "invoice_number"
    assert d0["original_value"] == "WRONG"      # model's wrong output
    assert d0["corrected_value"] == "INV-1"     # golden answer (unwrapped)
    assert d0["source"] == "golden"


def test_reflect_on_golden_routes_to_reflection(monkeypatch):
    """A golden deviation flows through deviations_to_diffs into the existing
    reflection machinery and yields a structured FieldRule."""
    from app.ocr_optimizer.reflection import reflector
    from app.ocr_optimizer.eval.golden_loop import reflect_on_golden

    def fake_llm(*, processor_spec, model_name, system_instruction, user_prompt, as_json):
        return {
            "rationale": "取错了相邻字段",
            "fix_suggestion": "取 'Invoice No.' 右侧的编号",
            "semantic": "发票唯一编号",
            "anchors": ["'Invoice No.' 右侧"],
            "format_rule": "字母+数字，保留原文",
            "generalization": {"rule": "始终取票头 Invoice No. 后的串", "holds_for_all": True},
        }
    monkeypatch.setattr(reflector, "llm_text_completion_failover", fake_llm)

    deviations = [{"module_key": "invoice_number", "doc_id": "d1",
                   "expected": ["INV-1"], "got": ["WRONG"]}]
    mbk = {"invoice_number": {"display_name": "invoiceNumber",
                              "json_path": "$[*].invoiceNumber",
                              "description": "发票号", "ocr_prompt": "找发票号"}}

    results = reflect_on_golden(
        None, country=None, deviations=deviations,
        modules_by_key=mbk, processor_spec="mock",
    )
    res = results["invoice_number"]
    assert res.fix_suggestions and "Invoice No." in res.fix_suggestions[0]
    assert res.field_rule is not None
    assert res.field_rule.generalization.holds_for_all is True


def test_reflect_on_golden_empty_when_no_deviations():
    from app.ocr_optimizer.eval.golden_loop import reflect_on_golden
    assert reflect_on_golden(None, country="MY", deviations=[], modules_by_key={}) == {}
