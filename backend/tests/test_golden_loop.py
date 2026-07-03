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
            # 批次5：holds_for_all 需有证据支撑（≥2 条），否则 sanitize 会
            # 降为 False——LLM 自报「覆盖全部样本」不可信，凭证据说话。
            "generalization": {"rule": "始终取票头 Invoice No. 后的串",
                               "evidence_per_sample": ["样本1: INV-1 在 Invoice No. 右侧",
                                                       "样本2: INV-2 在 Invoice No. 右侧"],
                               "holds_for_all": True},
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


# ── comparable batches (<=5 random seeds, same core field set) ────────────────

def test_compute_core_fields_includes_common_fields():
    from app.ocr_optimizer.eval.golden_loop import compute_core_fields
    core = set(compute_core_fields("MY", threshold=0.8))
    # invoiceNumber/invoiceDate/currency/totalAmount appear on basically every
    # MY invoice → must be in the core.
    for f in ("invoiceNumber", "invoiceDate", "currency", "totalAmount"):
        assert f in core, f"{f} should be a core field"


def test_sample_batch_caps_at_5_and_covers_core():
    from app.ocr_optimizer.eval.golden_loop import sample_batch, load_golden, _top_keys
    b = sample_batch("MY", size=5, threshold=0.8, rng_seed=42)
    assert b["batch_size"] <= 5
    core = set(b["core_fields"])
    g = load_golden("MY")
    # every seed in the batch covers the full core set
    for did in b["doc_ids"]:
        assert core <= _top_keys(g[did]["gt"]), "batch seed must cover the core"


def test_sample_batch_is_reproducible_with_seed():
    from app.ocr_optimizer.eval.golden_loop import sample_batch
    a = sample_batch("MY", size=5, rng_seed=7)["doc_ids"]
    b = sample_batch("MY", size=5, rng_seed=7)["doc_ids"]
    assert a == b   # same seed → same batch (different seeds rotate docs)


def test_sample_batch_hard_cap_even_if_size_larger():
    from app.ocr_optimizer.eval.golden_loop import sample_batch
    b = sample_batch("MY", size=20, rng_seed=1)
    assert b["batch_size"] <= 5
