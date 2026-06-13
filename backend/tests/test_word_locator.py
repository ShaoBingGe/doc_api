"""字段焦点定位（第一步）: 词坐标匹配的确定性测试。

locate_value 是纯算法（无 LLM / 无网络），必须可穷举回归——它决定了
点击字段时焦点放大落点准不准。
"""
from __future__ import annotations

from app.services import word_locator as wl
from app.services.word_locator import Word


def _w(text, x, y, w=5.0, h=2.0, page=1):
    return Word(text=text, x=x, y=y, w=w, h=h, page=page)


# 一张合成票面的词序列（坐标 0-100）
WORDS = [
    _w("Invoice", 5, 5), _w("No.", 12, 5), _w("IV-852866", 20, 5),
    _w("Date", 5, 10), _w("31/07/2025", 12, 10),
    _w("Total", 5, 20), _w("RM", 12, 20), _w("6,000.00", 16, 20),
    _w("PP", 5, 30), _w("CHIN", 9, 30), _w("HIN", 14, 30), _w("SDN", 18, 30), _w("BHD", 23, 30),
    _w("Currency", 5, 40), _w("MYR", 15, 40),
]


# ── extract_words 边界 ──────────────────────────────────────────────────────

def test_extract_words_non_pdf_returns_empty():
    assert wl.extract_words("/tmp/whatever.png") == []
    assert wl.extract_words("/tmp/nope.jpg") == []


# ── 文本匹配 ────────────────────────────────────────────────────────────────

def test_single_word_exact():
    bb = wl.locate_value("IV-852866", WORDS)
    assert bb and bb["page"] == 1
    assert abs(bb["x"] - 20) < 0.1 and abs(bb["y"] - 5) < 0.1


def test_multi_word_company_name():
    bb = wl.locate_value("PP CHIN HIN SDN BHD", WORDS)
    assert bb is not None
    # 包围盒应横跨 5 个词：x 起于 PP(5)，右边界到 BHD(23+5=28)
    assert abs(bb["x"] - 5) < 0.1
    assert bb["width"] >= 22


def test_currency_enum():
    bb = wl.locate_value("MYR", WORDS)
    assert bb and abs(bb["x"] - 15) < 0.1


# ── 数值匹配（格式容忍）──────────────────────────────────────────────────────

def test_number_thousands_separator():
    # 值规范化为 6000.0，票面是 "6,000.00" → 数值相等命中
    bb = wl.locate_value("6000.0", WORDS)
    assert bb and abs(bb["x"] - 16) < 0.1


def test_number_with_currency_prefix_split():
    # "RM" "6,000.00" 两词拼接，按数值匹配 6000
    bb = wl.locate_value("6000", WORDS)
    assert bb is not None


# ── 未命中 → None（前端据此降级）───────────────────────────────────────────

def test_miss_returns_none():
    assert wl.locate_value("NONEXISTENT-XYZ", WORDS) is None


def test_iso_date_matches_dmy_native_format():
    # 日期专项匹配：GT 转 ISO（2025-07-31），票面 31/07/2025（DMY）→ 命中。
    bb = wl.locate_value("2025-07-31", WORDS)
    assert bb is not None
    assert abs(bb["x"] - 12) < 0.1 and abs(bb["y"] - 10) < 0.1


def test_date_parser_variants():
    from app.services.word_locator import _parse_date_candidates as p
    assert (2025, 7, 9) in p("09/07/2025")      # DMY
    assert (2025, 7, 9) in p("9 Jul 2025")       # 月名
    assert (2025, 7, 9) in p("Jul 9, 2025")      # 月名在前
    assert (2025, 7, 9) in p("2025-07-09")       # ISO 原样
    assert p("not a date") == set()


def test_empty_inputs():
    assert wl.locate_value("", WORDS) is None
    assert wl.locate_value("anything", []) is None


# ── 批量 locate_fields ──────────────────────────────────────────────────────

def test_locate_fields_fills_only_missing():
    fields = [
        {"keyName": "invoiceNumber", "value": "IV-852866", "bbox": None},
        {"keyName": "currency", "value": "MYR", "bbox": None},
        {"keyName": "preset", "value": "X", "bbox": {"x": 1, "y": 1, "width": 1, "height": 1, "page": 1}},
        {"keyName": "missing", "value": "NOPE-123", "bbox": None},
    ]
    located, total = wl.locate_fields(fields, WORDS)
    assert total == 4
    assert located == 3  # invoiceNumber + currency 命中 + preset 已有
    assert fields[0]["bbox"] is not None
    assert fields[3]["bbox"] is None          # 未命中保持 None（降级）
    assert fields[2]["bbox"]["x"] == 1         # 已有坐标不被覆盖


def test_locate_fields_empty_words_noop():
    fields = [{"keyName": "a", "value": "IV-852866", "bbox": None}]
    located, total = wl.locate_fields(fields, [])
    assert (located, total) == (0, 1)
    assert fields[0]["bbox"] is None
