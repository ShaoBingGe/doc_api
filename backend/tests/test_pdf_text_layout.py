"""PDF 文本层解析器 —— 版面重建与 OCR 降级判据。

核心用例是「表格型 PDF 的标签/值分离」：PDF 按绘制顺序存文本，表格票据常先画
整列标签、再画整列值，`get_text("text")` 拿到的标签与值完全错位。真实语料
（testing/INV LX 系列）即如此，把那种文本喂给模型会诱导它把值配错字段。
"""

from __future__ import annotations

import pathlib

import fitz

from app.processors.pdf_text import (
    MIN_CHARS,
    TextQuality,
    extract_layout_text,
)


def _save(doc, tmp_path: pathlib.Path, name: str = "t.pdf") -> str:
    p = tmp_path / name
    doc.save(p)
    doc.close()
    return str(p)


def test_table_layout_labels_and_values_are_paired(tmp_path):
    """标签列与值列分两批绘制时，重排后必须回到同一行。

    这是市面表格型 PDF 的典型结构，也是本模块存在的理由。
    """
    doc = fitz.open()
    page = doc.new_page()
    rows = [("INVOICE NO.", "LX 633431"), ("ORDER NO.", "359869"),
            ("DATE", "15/07/2025"), ("CURRENCY", "MYR")]
    # 先画所有标签（x=72），再画所有值（x=300）——刻意制造绘制顺序≠视觉顺序
    for i, (label, _) in enumerate(rows):
        page.insert_text((72, 100 + i * 20), label, fontsize=10)
    for i, (_, value) in enumerate(rows):
        page.insert_text((300, 100 + i * 20), value, fontsize=10)
    path = _save(doc, tmp_path)

    raw = ""
    with fitz.open(path) as d:
        raw = d[0].get_text("text")
    # 前提确认：原始文本流确实是错位的
    assert raw.index("CURRENCY") < raw.index("LX 633431"), "构造的样本应当是错位的"

    text, q = extract_layout_text(path, 16)
    assert q.usable, q
    for label, value in rows:
        line = next((ln for ln in text.split("\n") if label in ln), None)
        assert line is not None, f"缺少 {label} 所在行"
        assert value in line, f"{label} 与 {value} 未被重排到同一行：{line!r}"


def test_columns_are_separated_by_wide_gap(tmp_path):
    """跨列用连续空格标示，便于模型看出列结构（而非误读成一个词）。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "ITEM", fontsize=10)
    page.insert_text((400, 100), "AMOUNT", fontsize=10)
    text, q = extract_layout_text(_save(doc, tmp_path), 16)
    if q.usable:
        line = next(ln for ln in text.split("\n") if "ITEM" in ln)
        assert "   " in line, f"跨列应有多空格间隔：{line!r}"


def test_scanned_pdf_falls_back_to_ocr(tmp_path):
    """纯图 PDF（扫描件）→ 不可用，调用方降级为纯 OCR。"""
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 300))
    pix.clear_with(255)
    page.insert_image(fitz.Rect(0, 0, 400, 300), pixmap=pix)
    text, q = extract_layout_text(_save(doc, tmp_path), 16)
    assert text == "" and not q.usable
    assert q.reason == "no_text_layer"


def test_sparse_text_falls_back(tmp_path):
    """扫描件上盖的水印/页码这类零星文本，不足以校对 → 降级。"""
    doc = fitz.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 72), "CamScanner", fontsize=9)
    text, q = extract_layout_text(_save(doc, tmp_path), 16)
    assert not q.usable
    assert q.reason in ("no_text_layer", "too_sparse"), q


def test_pages_limited_to_what_model_sees(tmp_path):
    """文本层页数不得超过送图页数，否则模型会"读到"没看过的页。"""
    doc = fitz.open()
    for i in range(8):
        doc.new_page().insert_text(
            (72, 72), f"PAGE-{i + 1} " + "INVOICE TOTAL 100.00 " * 6, fontsize=9)
    text, q = extract_layout_text(_save(doc, tmp_path), 3)
    assert q.usable, q
    assert "第 3 页" in text
    assert "第 4 页" not in text
    assert "PAGE-4" not in text


def test_quality_dataclass_is_informative():
    s = str(TextQuality(False, "garbled", chars=10, garbled_ratio=0.5))
    assert "garbled" in s and "usable=False" in s


def test_min_chars_threshold_is_enforced(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "x" * (MIN_CHARS // 4), fontsize=10)
    _, q = extract_layout_text(_save(doc, tmp_path), 16)
    assert not q.usable


def test_missing_file_is_handled(tmp_path):
    _, q = extract_layout_text(str(tmp_path / "nope.pdf"), 16)
    assert not q.usable and q.reason == "extract_error"
