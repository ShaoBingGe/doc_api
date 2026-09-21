"""黄金集 strict 模式的行表判据：行数 gate + 逐行关键字段匹配率。

历史问题：line_items / tax_summary 与标量字段同用整体全等（==），
浮点位数 / description 文字 / 单位写法任一处差异即判 0——实测 8 次黄金批
全 0/5，判别力为零。本文件锁新判据的语义与边界。
"""

from __future__ import annotations

from app.ocr_optimizer.eval.harness import (
    _flatten_rows,
    _is_row_table,
    _score_row_table_strict,
)

ROW = {"articleName": "PAINT 5L", "description": "PAINT 5L",
       "quantity": 6.0, "unitOfMeasure": "PAIL",
       "unitPrice": 100.0, "netAmount": 600.0}


def test_perfect_match_is_1():
    acc, ok, diff = _score_row_table_strict([dict(ROW)], [dict(ROW)])
    assert (acc, ok, diff) == (1.0, True, "")


def test_row_count_mismatch_is_gate_zero():
    acc, ok, diff = _score_row_table_strict([dict(ROW)], [dict(ROW), dict(ROW)])
    assert acc == 0.0 and not ok
    assert "row-count-mismatch" in diff


def test_key_field_partial_credit():
    """3 个关键字段对 2 个 → 该行 0.75（articleName/quantity/unitPrice 对，netAmount 错）。"""
    got = dict(ROW, netAmount=999.0)
    acc, ok, _ = _score_row_table_strict([got], [dict(ROW)])
    assert abs(acc - 0.75) < 1e-9
    assert not ok


def test_description_mismatch_does_not_penalize():
    """description 不在关键字段里：GT 把品名复制进了 description，而模板 §1.35
    要求那种情况留空——比它等于惩罚正确行为。"""
    got = dict(ROW, description="", unitOfMeasure="pail")
    acc, ok, _ = _score_row_table_strict([got], [dict(ROW)])
    assert (acc, ok) == (1.0, True)


def test_numeric_tolerance_int_vs_float():
    got = dict(ROW, quantity=6, unitPrice=100, netAmount=600)
    acc, ok, _ = _score_row_table_strict([got], [dict(ROW)])
    assert (acc, ok) == (1.0, True)


def test_nested_per_entity_rows_are_flattened():
    """切片形状是 [[票1的行...], [票2的行...]] —— 展平后按位置对齐。"""
    nested_gt = [[dict(ROW)], [dict(ROW, articleName="B", netAmount=50.0)]]
    nested_got = [[dict(ROW)], [dict(ROW, articleName="B", netAmount=50.0)]]
    acc, ok, _ = _score_row_table_strict(nested_got, nested_gt)
    assert (acc, ok) == (1.0, True)
    assert _is_row_table(nested_gt)
    assert len(_flatten_rows(nested_gt)) == 2


def test_shape_mismatch_scores_zero():
    acc, ok, diff = _score_row_table_strict("not-a-list", [dict(ROW)])
    assert acc == 0.0 and not ok and "shape-mismatch" in diff
    acc2, ok2, _ = _score_row_table_strict(None, [dict(ROW)])
    assert acc2 == 0.0 and not ok2


def test_tax_rows_use_tax_key_fields():
    gt_row = {"taxCategory": "SST", "taxRate": "10%", "tax": 290.0}
    got_ok = {"taxCategory": "SST", "taxRate": "10%", "tax": 290}
    got_bad = {"taxCategory": "SST", "taxRate": "6%", "tax": 290.0}
    assert _score_row_table_strict([got_ok], [gt_row])[0] == 1.0
    acc, ok, _ = _score_row_table_strict([got_bad], [gt_row])
    assert abs(acc - 2 / 3) < 1e-9 and not ok


def test_scalar_fields_unaffected():
    """标量字段不是行表——仍走整体全等，本判据不越权。"""
    assert not _is_row_table("INV-1")
    assert not _is_row_table(["INV-1", "INV-2"])
    assert not _is_row_table([])
