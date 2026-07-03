"""批次4 回归：evaluator 假通过/假失败修复。

假通过侧（真实错误被漂白，反思回路收不到信号）：
  - 日期 search 子串匹配：OCR 抓整行仍判对；
  - 布尔 truthiness：GT=True vs OCR="no" 判对；
  - 数字绝对容差 0.01：税率 0.06 vs 0.065 判对、大金额尾数错判对。
假失败侧（幻影错误驱动 optimizer 改坏正确规则）：
  - 数组按 index 硬对齐：一行多提/漏提 → 后续所有行错位记 0；
  - 非 ISO 日期格式族（DD/MM/YYYY）恒判错。
"""
from __future__ import annotations

from app.ocr_optimizer.service.evaluator import compare, _values_match


# ── 日期 ─────────────────────────────────────────────────────────────────────

def test_date_grabbing_whole_line_is_a_mismatch():
    # 历史 bug：search 子串匹配 → 判对
    assert _values_match("Date: 2025-05-02 Due Date: 2025-06-01", "2025-05-02") is False


def test_date_embedded_in_invoice_number_is_a_mismatch():
    assert _values_match("INV-2025-05-02-889", "2025-5-2") is False


def test_iso_format_variants_match():
    assert _values_match("2025-05-02", "2025/5/2") is True
    assert _values_match("2025年5月2日", "2025-05-02") is True


def test_dmy_format_matches_iso_gt():
    # MY 票面常见 DD/MM/YYYY，GT 标 ISO —— 历史恒判错
    assert _values_match("02/05/2025", "2025-05-02") is True


def test_mdy_ambiguity_matches_either_interpretation():
    assert _values_match("05/02/2025", "2025-05-02") is True   # MDY 解释
    assert _values_match("05/02/2025", "2025-02-05") is True   # DMY 解释


def test_unambiguous_dmy_does_not_match_wrong_date():
    # 31 不能是月份 → 只有 DMY 解释
    assert _values_match("31/01/2025", "2025-01-31") is True
    assert _values_match("31/01/2025", "2025-31-01") is False


# ── 布尔 ─────────────────────────────────────────────────────────────────────

def test_bool_true_vs_no_is_a_mismatch():
    # 历史 bug：bool("no") = True → 判对，布尔字段全盲
    assert _values_match("no", True) is False
    assert _values_match("false", True) is False
    assert _values_match("随便什么字符串", True) is False


def test_bool_semantic_strings_match():
    assert _values_match("yes", True) is True
    assert _values_match("no", False) is True
    assert _values_match("FALSE", False) is True
    assert _values_match(True, True) is True


# ── 数字 ─────────────────────────────────────────────────────────────────────

def test_thousand_separators_and_currency_still_match():
    assert _values_match("1,000.00", 1000) is True
    assert _values_match("RM 1,000.00".replace("RM", "$"), "1000") is True
    assert _values_match("600.00", 600.0) is True


def test_small_value_difference_is_a_mismatch():
    # 历史 bug：绝对容差 0.01 → 税率/汇率/单价差异被判对
    assert _values_match(0.06, 0.065) is False
    assert _values_match("0.005", "0.01") is False


def test_large_value_cent_error_is_a_mismatch():
    # 历史 bug：123456.784 与 123456.78 差 0.004 < 0.01 → 判对
    assert _values_match(123456.78, 123456.784) is False


# ── 数组内容对齐 ─────────────────────────────────────────────────────────────

def _row(desc, qty, price):
    return {"description": desc, "quantity": qty, "unitPrice": price}


def test_row_split_no_longer_zeroes_the_tail():
    """OCR 把第 1 行拆成两行：历史 index 对齐让后续所有行错位记 0。
    现在内容对齐：3 行真实匹配 + 1 行多提取 → 3/4，而不是 ~0。"""
    gt = [_row("Widget A", 2, 10.0), _row("Widget B", 1, 5.0), _row("Widget C", 4, 2.5)]
    ocr = [_row("Widget", 1, 10.0),  # 拆出来的碎行
           _row("Widget A", 2, 10.0), _row("Widget B", 1, 5.0), _row("Widget C", 4, 2.5)]
    matched, acc, diff = compare(ocr, gt)
    assert acc >= 0.7   # 3 完整匹配 / 4 分母（历史行为 ≈ 0.2）
    assert "多提取" in diff


def test_missing_row_gets_partial_credit_not_total_collapse():
    gt = [_row("A", 1, 1.0), _row("B", 2, 2.0), _row("C", 3, 3.0)]
    ocr = [_row("A", 1, 1.0), _row("C", 3, 3.0)]   # 漏了 B
    matched, acc, diff = compare(ocr, gt)
    assert abs(acc - 2 / 3) < 0.01
    assert "漏提取" in diff


def test_reordered_rows_match_by_content():
    gt = [_row("A", 1, 1.0), _row("B", 2, 2.0)]
    ocr = [_row("B", 2, 2.0), _row("A", 1, 1.0)]
    matched, acc, _ = compare(ocr, gt)
    assert matched and acc == 1.0


def test_identical_lists_still_perfect():
    gt = [_row("A", 1, 1.0)]
    matched, acc, _ = compare([_row("A", 1, 1.0)], gt)
    assert matched and acc == 1.0


def test_empty_lists_match():
    matched, acc, _ = compare([], [])
    assert matched and acc == 1.0
