"""PDF 渲染面积上限（2026-09-15 MSD 批量实测）。

事故：`_RENDER_DPI=150` 写死、不看页面尺寸。一份 1267×2893pt 的长条扫描件
渲染出 15.5MB PNG，base64 后请求体约 20MB，DashScope 直接 400 Bad Request——
该文件在 MSD 两轮批量里都失败；另有 7 份 >10MB 上传极慢。

本文件锁两件事：
  1. 病态大版面被降采样到面积上限内（请求体不再炸）；
  2. **普通版面一个像素都不动**——修复只能对异常输入生效，不得顺手降低
     常规票据的清晰度（那会直接伤 OCR 准确率）。
"""

from __future__ import annotations

import base64

import fitz
import pytest

from app.processors.qwen_processor import (
    _MAX_RENDER_PIXELS,
    _RENDER_DPI,
    QwenProcessor,
)


def _make_pdf(tmp_path, width_pt: float, height_pt: float):
    doc = fitz.open()
    page = doc.new_page(width=width_pt, height=height_pt)
    page.insert_text((40, 60), "INVOICE 12345", fontsize=18)
    path = tmp_path / f"{int(width_pt)}x{int(height_pt)}.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _decoded_pixels(data_url: str) -> int:
    raw = base64.b64decode(data_url.split(",", 1)[1])
    pix = fitz.Pixmap(raw)
    return pix.width * pix.height


def test_normal_a4_is_not_downscaled(tmp_path):
    """A4（595×842pt）150dpi ≈ 2.2M 像素，在上限内 → 必须保持原 DPI。"""
    path = _make_pdf(tmp_path, 595, 842)
    urls = QwenProcessor()._pdf_to_png_data_urls(str(path))
    expected = round(595 * _RENDER_DPI / 72) * round(842 * _RENDER_DPI / 72)
    got = _decoded_pixels(urls[0])
    # 允许 ±2% 的取整误差，但不允许被降采样
    assert got > expected * 0.98, f"普通 A4 被降采样了：{got} vs {expected}"


def test_oversized_page_is_capped(tmp_path):
    """复刻事故版面 1267×2893pt：150dpi 下约 15.9M 像素，必须降到上限内。"""
    path = _make_pdf(tmp_path, 1267, 2893)
    urls = QwenProcessor()._pdf_to_png_data_urls(str(path))
    got = _decoded_pixels(urls[0])
    assert got <= _MAX_RENDER_PIXELS * 1.05, f"超大页未被限制：{got} 像素"
    # 也不能缩过头——缩到看不清就失去意义
    assert got >= _MAX_RENDER_PIXELS * 0.5, f"缩得过狠：{got} 像素"


def test_oversized_payload_shrinks_below_dashscope_limit(tmp_path):
    """端到端判据：事故版面的 base64 请求体必须回到 10MB 以内。"""
    path = _make_pdf(tmp_path, 1267, 2893)
    urls = QwenProcessor()._pdf_to_png_data_urls(str(path))
    total_mb = sum(len(u) for u in urls) / 1024 / 1024
    assert total_mb < 10, f"请求体仍过大：{total_mb:.1f} MB"


@pytest.mark.parametrize("w,h", [(595, 842), (612, 792), (420, 595)])
def test_common_page_sizes_unaffected(tmp_path, w, h):
    """A4 / Letter / A5 都在上限内，一律不降采样。"""
    path = _make_pdf(tmp_path, w, h)
    urls = QwenProcessor()._pdf_to_png_data_urls(str(path))
    expected = round(w * _RENDER_DPI / 72) * round(h * _RENDER_DPI / 72)
    assert _decoded_pixels(urls[0]) > expected * 0.98
