"""A2：OCR 输出后处理纯函数单测（下沉 app/domain/extraction_pipeline 后首次可测）.

这批函数是每次 OCR 上传的 load-bearing 数据变换（flatten / normalize /
project / pad / rewrite），此前埋在 document_service 里无法直接测。
"""
from __future__ import annotations

from app.domain import extraction_pipeline as ep


# ── normalize_bbox ────────────────────────────────────────────────────────────

def test_normalize_bbox_scales_1000_range():
    out = ep.normalize_bbox({"x": 200, "y": 400, "width": 500, "height": 100, "page": 2})
    assert out == {"x": 20.0, "y": 40.0, "width": 50.0, "height": 10.0, "page": 2}


def test_normalize_bbox_clamps_and_defaults_page():
    out = ep.normalize_bbox({"x": -5, "y": 50, "w": 30, "h": 20})
    assert out["x"] == 0.0 and out["page"] == 1


def test_normalize_bbox_rejects_non_dict():
    assert ep.normalize_bbox(None) is None
    assert ep.normalize_bbox("nope") is None


# ── is_leaf_field（Phase-26 record-collapse 守护）───────────────────────────

def test_is_leaf_field_true_for_leaf_shapes():
    assert ep.is_leaf_field({"value": "X"}) is True
    assert ep.is_leaf_field({"value": "X", "confidence": 0.9, "bbox": None}) is True


def test_is_leaf_field_false_for_record_with_stray_value_key():
    # 真实记录里恰好有个叫 value 的字段——绝不能塌成单叶子
    assert ep.is_leaf_field({"invoiceNumber": "I-1", "value": None, "currency": "MYR"}) is False


def test_is_leaf_field_false_for_nested_value():
    assert ep.is_leaf_field({"value": {"x": 1}}) is False


# ── flatten / normalize ──────────────────────────────────────────────────────

def test_normalize_flattens_hierarchical_leaves():
    raw = {"invoiceNumber": {"value": "INV-1", "confidence": 0.9},
           "seller": {"name": {"value": "ACME"}}}
    out = ep.normalize_structured_data(raw)
    by_key = {e["keyName"]: e["value"] for e in out}
    assert by_key["invoiceNumber"] == "INV-1"
    assert by_key["seller.name"] == "ACME"
    assert all("id" in e for e in out)


def test_normalize_keeps_prestructured_list():
    raw = [{"keyName": "a", "value": 1, "bbox": None}]
    out = ep.normalize_structured_data(raw)
    assert out[0]["keyName"] == "a" and out[0]["confidence"] is None and "id" in out[0]


def test_normalize_flattens_rows_table():
    raw = {"items": {"_meta": {}, "rows": [{"desc": {"value": "A"}}, {"desc": {"value": "B"}}]}}
    out = ep.normalize_structured_data(raw)
    keys = sorted(e["keyName"] for e in out)
    assert keys == ["items[0].desc", "items[1].desc"]


# ── field_top_level ──────────────────────────────────────────────────────────

def test_field_top_level_strips_paths_and_indices():
    assert ep.field_top_level("docType") == "docType"
    assert ep.field_top_level("page[0]") == "page"
    assert ep.field_top_level("billFrom.name") == "billFrom"
    assert ep.field_top_level("detailOfGoodsOrServices[0].description") == "detailOfGoodsOrServices"
    assert ep.field_top_level("") == ""


# ── project_to_field_set ─────────────────────────────────────────────────────

def test_project_leaf_list_keeps_allowed_only():
    sd = [{"keyName": "invoiceNumber", "value": "I-1"},
          {"keyName": "junkField", "value": "x"},
          {"keyName": "lineItems[0].qty", "value": 2}]
    out = ep.project_to_field_set(sd, ["invoiceNumber", "lineItems"])
    assert [e["keyName"] for e in out] == ["invoiceNumber", "lineItems[0].qty"]


def test_project_record_dict_filters_keys():
    out = ep.project_to_field_set({"a": 1, "b": 2}, ["a"])
    assert out == {"a": 1}


def test_project_noop_on_empty_allowed():
    sd = [{"keyName": "x", "value": 1}]
    assert ep.project_to_field_set(sd, []) is sd


# ── pad_with_required_keys（幂等）────────────────────────────────────────────

def test_pad_fills_missing_and_is_idempotent():
    sd = [{"invoiceNumber": "I-1"}]
    required = ["invoiceNumber", "totalAmount"]
    padded = ep.pad_with_required_keys(sd, required)
    assert padded[0]["totalAmount"] is None
    assert ep.pad_with_required_keys(padded, required) == padded


def test_pad_noop_on_empty_required():
    sd = [{"a": 1}]
    assert ep.pad_with_required_keys(sd, []) is sd


# ── rewrite_structured_data_keys（幂等、仅顶层）──────────────────────────────

def test_rewrite_top_level_keys_idempotent():
    sd = [{"billFromName": "ACME", "total": 5}]
    out = ep.rewrite_structured_data_keys(sd, {"billFromName": "salerCompany"})
    assert out == [{"salerCompany": "ACME", "total": 5}]
    # 幂等：再套一次同映射不变
    assert ep.rewrite_structured_data_keys(out, {"billFromName": "salerCompany"}) == out


def test_rewrite_noop_on_empty_renames():
    sd = {"a": 1}
    assert ep.rewrite_structured_data_keys(sd, {}) is sd
