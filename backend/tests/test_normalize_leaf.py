"""
Regression: a real extraction record that contains a field literally named
`value` must NOT be misread as a single hierarchical leaf (which collapsed the
ENTIRE record to one null entry — total field-set loss). It must recurse into
per-field entries.
"""

from __future__ import annotations


def test_is_leaf_field_rejects_record_with_stray_value_key():
    from app.services.document_service import _is_leaf_field
    # genuine leaf
    assert _is_leaf_field({"value": "X"}) is True
    assert _is_leaf_field({"value": "X", "confidence": 0.9, "bbox": None}) is True
    # a real record that merely contains a `value` field → NOT a leaf
    assert _is_leaf_field({"invoiceNumber": "I-1", "value": None, "currency": "MYR"}) is False
    # value being a dict is not a leaf
    assert _is_leaf_field({"value": {"x": 1}}) is False


def test_normalize_does_not_collapse_record_with_value_key():
    from app.services.document_service import _normalize_structured_data
    rec = {"invoiceNumber": "I-061748", "totalAmount": 3963.55,
           "value": None, "currency": "MYR"}
    out = _normalize_structured_data(rec)
    names = sorted(e["keyName"] for e in out)
    # recurses into the real fields instead of producing one junk [field=null]
    assert "invoiceNumber" in names
    assert "totalAmount" in names
    assert "currency" in names
    assert names != ["field"]
    # the extracted value survives
    inv = next(e for e in out if e["keyName"] == "invoiceNumber")
    assert inv["value"] == "I-061748"
