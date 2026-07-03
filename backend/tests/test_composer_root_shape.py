"""批次1 回归：composed_schema 根形状必须跟随模块 json_path 家族。

历史 bug：`$[*].x` 路径的根级 [*] token 在 _inject 里被静默丢弃，
composed_schema 根被拼成 object。严格执行 response_schema 的模型（Gemini）
随即输出 dict，slicer 按 $[*].x 切片全部为 None，而 GT 侧 align_for_path
把 dict GT 包成 [gt] —— 每个字段每轮假 0 分，早停/判官/单调守护全部失真。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ocr_optimizer.service import composer, slicer
from app.ocr_optimizer.service.ground_truth import align_for_path


def _mod(key, json_path, frag, prompt="找到该字段", order=0):
    return SimpleNamespace(
        module_key=key, display_name=key, json_path=json_path,
        schema_fragment=frag, ocr_prompt=prompt, order_index=order,
        ocr_suggestions={}, description="",
    )


def _array_rooted_modules():
    """国家模板家族：所有字段 json_path 以 $[*] 开头。"""
    return [
        _mod("invoice_number", "$[*].invoiceNumber", {"type": "string"}, order=1),
        _mod("total", "$[*].totalAmount", {"type": "number"}, order=2),
        _mod("line_items", "$[*].lineItems[*]",
             {"type": "object", "properties": {
                 "description": {"type": "string"},
                 "quantity": {"type": "number"},
             }}, order=3),
    ]


# ── 数组根组装 ────────────────────────────────────────────────────────────────

def test_array_rooted_paths_produce_array_root_schema():
    schema = composer.assemble_schema(_array_rooted_modules())
    assert schema["type"] == "array"
    items = schema["items"]
    assert items["type"] == "object"
    assert items["properties"]["invoiceNumber"] == {"type": "string"}
    assert items["properties"]["totalAmount"] == {"type": "number"}
    # 尾随 [*]：fragment 注入 lineItems.items（勿回退到空 items bug）
    li = items["properties"]["lineItems"]
    assert li["type"] == "array"
    assert li["items"]["properties"]["quantity"] == {"type": "number"}


def test_object_rooted_paths_keep_object_root():
    mods = [
        _mod("invoice_number", "$.invoiceNumber", {"type": "string"}, order=1),
        _mod("line_items", "$.lineItems[*]",
             {"type": "object", "properties": {"quantity": {"type": "number"}}},
             order=2),
    ]
    schema = composer.assemble_schema(mods)
    assert schema["type"] == "object"
    assert schema["properties"]["invoiceNumber"] == {"type": "string"}
    assert schema["properties"]["lineItems"]["type"] == "array"


def test_mixed_root_families_raise():
    mods = [
        _mod("a", "$[*].invoiceNumber", {"type": "string"}, order=1),
        _mod("b", "$.totalAmount", {"type": "number"}, order=2),
    ]
    with pytest.raises(ValueError, match="Schema conflict"):
        composer.assemble_schema(mods)


def test_global_dollar_module_merges_into_record_under_array_root():
    mods = _array_rooted_modules() + [
        _mod("grouped", "$", {"type": "object", "properties": {
            "docType": {"type": "string"},
        }}, order=0),
    ]
    schema = composer.assemble_schema(mods)
    assert schema["type"] == "array"
    assert schema["items"]["properties"]["docType"] == {"type": "string"}


def test_whole_record_module_merges_into_items():
    # json_path 恰为 "$[*]"：fragment 即记录 schema，其 properties 并入 items
    mods = [
        _mod("record", "$[*]", {"type": "object", "properties": {
            "docType": {"type": "string"},
        }}, order=0),
        _mod("invoice_number", "$[*].invoiceNumber", {"type": "string"}, order=1),
    ]
    schema = composer.assemble_schema(mods)
    assert schema["type"] == "array"
    props = schema["items"]["properties"]
    assert props["docType"] == {"type": "string"}
    assert props["invoiceNumber"] == {"type": "string"}


def test_conflicting_array_paths_still_raise():
    mods = [
        _mod("a", "$[*].invoiceNumber", {"type": "string"}, order=1),
        _mod("b", "$[0].invoiceNumber", {"type": "string"}, order=2),
    ]
    with pytest.raises(ValueError, match="Schema conflict"):
        composer.assemble_schema(mods)


# ── 端到端 round-trip：schema 根 / OCR 输出 / slicer / GT 对齐四方一致 ─────────

def test_round_trip_array_root_slicer_and_gt_alignment():
    mods = _array_rooted_modules()
    schema = composer.assemble_schema(mods)
    # 模型按 response_schema 输出数组根
    assert schema["type"] == "array"
    ocr_output = [{
        "invoiceNumber": "INV-1",
        "totalAmount": 600.0,
        "lineItems": [{"description": "item", "quantity": 2}],
    }]
    gt_dict = {
        "invoiceNumber": "INV-1",
        "totalAmount": 600.0,
        "lineItems": [{"description": "item", "quantity": 2}],
    }
    for m in mods:
        ocr_slice = slicer.extract(ocr_output, m.json_path)
        gt_aligned = align_for_path(gt_dict, m.json_path)
        gt_slice = slicer.extract(gt_aligned, m.json_path)
        assert ocr_slice is not None, f"{m.json_path} 切 OCR 输出不能为 None"
        assert ocr_slice == gt_slice, f"{m.json_path} OCR/GT 切片必须一致"


def test_prompt_schema_tree_announces_array_root():
    text = composer.assemble_prompt(_array_rooted_modules(), country_global=None)
    assert "输出为 JSON 数组" in text
    assert "- invoiceNumber: string" in text
