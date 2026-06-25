"""Every edit-intent class must reach an intent-specialized reflection skill.

Before this, NORMALIZE / RETARGET / CASE_ONLY all collapsed into the single
generic `value_mismatch` skill (only the injected evidence block differed).
These tests pin the intent → skill routing so the mapping stays complete.
"""

from app.ocr_optimizer.reflection import edit_intent
from app.ocr_optimizer.reflection.master import route


def _keys(diff):
    return {s.key for s in route(diff)}


def _edit(original_value, corrected_value, *, original_name="invoiceNumber",
          corrected_name="invoiceNumber", original_format="string",
          corrected_format="string"):
    return {
        "kind": "edit",
        "module_key": "invoice_number",
        "original_name": original_name,
        "corrected_name": corrected_name,
        "original_value": original_value,
        "corrected_value": corrected_value,
        "original_format": original_format,
        "corrected_format": corrected_format,
    }


def test_normalize_routes_to_normalize_skill():
    # "W1 529054" → "529054" is a pure prefix deletion → NORMALIZE
    diff = _edit("W1 529054", "529054")
    assert edit_intent.classify(diff).intent == "NORMALIZE"
    keys = _keys(diff)
    assert "normalize" in keys
    assert "value_mismatch" not in keys  # no longer the catch-all


def test_retarget_routes_to_retarget_skill():
    # T+13 登録番号 wrongly grabbed instead of the request number → RETARGET
    diff = _edit("T1010001092605", "11289")
    assert edit_intent.classify(diff).intent == "RETARGET"
    keys = _keys(diff)
    assert "retarget" in keys
    assert "value_mismatch" not in keys


def test_case_only_routes_to_case_skill():
    diff = _edit("jpy", "JPY", original_name="currency", corrected_name="currency")
    assert edit_intent.classify(diff).intent == "CASE_ONLY"
    keys = _keys(diff)
    assert "case_normalize" in keys
    assert "value_mismatch" not in keys


def test_rename_only_routes_to_format_skill():
    diff = _edit("11289", "11289", original_name="invNo", corrected_name="invoiceNumber")
    assert edit_intent.classify(diff).intent == "RENAME_ONLY"
    assert "format_mismatch" in _keys(diff)


def test_type_only_routes_to_format_skill():
    diff = _edit("11289", "11289", original_format="string", corrected_format="number")
    assert edit_intent.classify(diff).intent == "TYPE_ONLY"
    assert "format_mismatch" in _keys(diff)


def test_mixed_routes_to_value_and_format_skills():
    # rename + value change together → MIXED
    diff = _edit("T1010001092605", "11289",
                 original_name="taxNo", corrected_name="invoiceNumber")
    assert edit_intent.classify(diff).intent == "MIXED"
    keys = _keys(diff)
    assert "value_mismatch" in keys   # value aspect
    assert "format_mismatch" in keys  # rename aspect


def test_empty_to_value_stays_on_empty_skill():
    # blank → value is a missed extraction, handled by empty_value (not retarget)
    diff = _edit("", "11289")
    keys = _keys(diff)
    assert "empty_value" in keys
    assert "retarget" not in keys  # original_value_is_empty guard keeps it out


def test_add_routes_to_new_field():
    diff = {"kind": "add", "corrected_name": "paymentTerm",
            "corrected_value": "30 days", "corrected_format": "string"}
    assert "new_field" in _keys(diff)
