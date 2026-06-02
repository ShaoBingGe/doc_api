"""
Prompt System v2 — Phase 3: reflection emits a structured FieldRule and feeds
the cross-sample block to ALL skill paths (not just country agents).
"""

from __future__ import annotations


def _patch_llm(monkeypatch, capture: list):
    """Stub the reflection LLM to capture the user prompt and return a
    structured, FieldRule-aligned output."""
    from app.ocr_optimizer.reflection import reflector

    def fake_llm(*, processor_spec, model_name, system_instruction, user_prompt, as_json):
        capture.append(user_prompt)
        return {
            "rationale": "把卖方名取成了买方名",
            "fix_suggestion": "在供应商区块取第一行公司名",
            "semantic": "开票方（卖方）公司名称",
            "anchors": ["票头供应商区块", "'From:' 标签右侧"],
            "format_rule": "原文保留，不翻译",
            "disambiguation": ["买方名在 'Bill To' 区块"],
            "generalization": {
                "rule": "始终取供应商区块第一行公司名",
                "evidence_per_sample": ["s1 ACME", "s2 BETA", "s3 GAMMA"],
                "holds_for_all": True,
            },
        }

    monkeypatch.setattr(reflector, "llm_text_completion_failover", fake_llm)


def test_reflection_builds_field_rule_with_generalization(monkeypatch):
    from app.ocr_optimizer.reflection.reflector import reflect_on_diffs
    cap: list = []
    _patch_llm(monkeypatch, cap)

    diff = {
        "kind": "edit", "module_key": "bill_from_name",
        "original_name": "billFromName", "corrected_name": "billFromName",
        "original_value": "PP CHIN HIN", "corrected_value": "DAY BRIGHT WOOD",
        "original_format": "string", "corrected_format": "string",
    }
    results = reflect_on_diffs(
        [diff],
        modules_by_key={"bill_from_name": {"description": "开票方", "ocr_prompt": "找卖方"}},
        country=None,                      # force global-skill path
        cross_doc_context={
            "billFromName": [
                {"doc_id": "d1", "doc_filename": "s1.pdf", "value": "ACME", "is_corrected": True},
                {"doc_id": "d2", "doc_filename": "s2.pdf", "value": "BETA", "is_corrected": True},
            ],
        },
    )
    res = results["bill_from_name"]
    # free-text path still populated
    assert res.fix_suggestions and "供应商区块" in res.fix_suggestions[0]
    # structured FieldRule captured
    fr = res.field_rule
    assert fr is not None
    assert fr.semantic.startswith("开票方")
    assert "票头供应商区块" in fr.anchors
    assert fr.generalization is not None and fr.generalization.holds_for_all is True
    # skeleton renders the cross-sample rule
    assert "跨样本规则（已覆盖全部样本）" in fr.render_skeleton()


def test_cross_doc_block_reaches_global_skill(monkeypatch):
    """The captured prompt for a global skill must include the cross-sample
    block (Phase 3 broadened this beyond country agents)."""
    from app.ocr_optimizer.reflection.reflector import reflect_on_diffs
    cap: list = []
    _patch_llm(monkeypatch, cap)

    diff = {
        "kind": "edit", "module_key": "invoice_number",
        "original_name": "invoiceNumber", "corrected_name": "invoiceNumber",
        "original_value": "WRONG", "corrected_value": "DB-4535",
        "original_format": "string", "corrected_format": "string",
    }
    reflect_on_diffs(
        [diff],
        modules_by_key={"invoice_number": {"description": "发票号", "ocr_prompt": "找发票号"}},
        country=None,
        cross_doc_context={
            "invoiceNumber": [
                {"doc_id": "d1", "doc_filename": "s1.pdf", "value": "DB-4535", "is_corrected": True},
            ],
        },
    )
    assert cap, "LLM was not invoked"
    assert any("跨样本对照" in p for p in cap), "cross-sample block missing from global skill prompt"
