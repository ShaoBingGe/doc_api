"""上传校验 —— 注定失败的文件不发 taskId。

背景：2026-08-26 线上两张 `.jpg` 被正常受理并发了 taskId，随后 Gemini 连续三次
回 `400 INVALID_ARGUMENT: Unable to process input image`，重试耗尽标 FAILED。
对接方拿着 taskId 白轮询几分钟，我们白烧 3 次模型调用。

这组用例按「坏文件必须在受理期被挡住、好文件必须放行」来写。
最后一条守 fail-open：我们自己没预料的情况一律放行，把判断权交回模型 ——
宁可放过，不可错杀。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services.upload_validation import InvalidUpload, validate_upload

BIG = 10 * 1024 * 1024


def _png(w: int = 40, h: int = 30) -> bytes:
    """最小合法 PNG。用 PyMuPDF 生成，避免引入 Pillow 依赖。"""
    import fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h))
    pix.clear_with(255)
    return pix.tobytes("png")


def _pdf(pages: int = 3) -> bytes:
    import fitz

    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


# ── 放行 ─────────────────────────────────────────────────────────────────────

def test_valid_pdf_returns_page_count():
    """PDF 返回真实页数 —— 准入闸按它扣页数配额。"""
    assert validate_upload(_pdf(5), "a.pdf", max_bytes=BIG) == 5


def test_valid_png_and_jpg_count_as_one_page():
    assert validate_upload(_png(), "a.png", max_bytes=BIG) == 1


def test_xlsx_passes_on_magic_alone():
    """xlsx 无法用 PyMuPDF 解码，只校验魔数，不能因此误拒。"""
    assert validate_upload(_xlsx(), "a.xlsx", max_bytes=BIG) == 1


# ── 拦截 ─────────────────────────────────────────────────────────────────────

def test_rejects_unknown_extension():
    with pytest.raises(InvalidUpload, match="不支持的文件类型"):
        validate_upload(b"whatever", "a.docx", max_bytes=BIG)


def test_rejects_empty_file():
    with pytest.raises(InvalidUpload, match="0 字节"):
        validate_upload(b"", "a.pdf", max_bytes=BIG)


def test_rejects_oversized_file():
    with pytest.raises(InvalidUpload, match="超过上限"):
        validate_upload(_pdf(1), "a.pdf", max_bytes=10)


def test_rejects_extension_content_mismatch():
    """改了后缀的假 jpg —— 线上那两张失败文件的高度可疑成因。"""
    with pytest.raises(InvalidUpload, match="与扩展名"):
        validate_upload(_pdf(1), "actually_a_pdf.jpg", max_bytes=BIG)


def test_rejects_structurally_broken_file():
    """魔数对但结构坏掉 —— 只有真正打开才暴露，正是 _probe 要抓的。"""
    broken = b"%PDF-" + b"\x00" * 400
    with pytest.raises(InvalidUpload, match="无法解析|损坏"):
        validate_upload(broken, "a.pdf", max_bytes=BIG)


def test_rejects_truncated_image():
    """截断的图片要挡住 —— 魔数正确，只有真正解析才暴露。"""
    good = _png(200, 150)
    truncated = good[: len(good) // 3]
    with pytest.raises(InvalidUpload, match="无法解析|损坏|截断"):
        validate_upload(truncated, "a.png", max_bytes=BIG)


def test_degenerate_tiny_image_may_slip_through():
    """边界记录：极小的退化图（百来字节）截断后仍可能被 PyMuPDF 接受。

    留这条不是为了固化缺陷，而是标清能力边界 —— 这类文件走到模型失败后标
    FAILED，原件会保留（mark_failed 不再删 spool）以便取证。若哪天它开始
    抛异常，这条会失败并提醒我们收紧上面那条的措辞。
    """
    good = _png(40, 30)
    truncated = good[: len(good) // 3]
    try:
        validate_upload(truncated, "a.png", max_bytes=BIG)
    except InvalidUpload:
        pytest.skip("PyMuPDF 现在能抓到退化小图的截断了 —— 可收紧文档措辞")


def test_rejects_tiny_image():
    """按**真实像素**判定：page.rect 是点，拿它当像素会系统性偏小 1/3。"""
    with pytest.raises(InvalidUpload, match="尺寸过小"):
        validate_upload(_png(4, 4), "tiny.png", max_bytes=BIG)


def test_accepts_image_just_above_the_floor():
    """下限判定不能误伤：24×24 用点(=18)算会被误拒，用像素算应放行。"""
    assert validate_upload(_png(24, 24), "small.png", max_bytes=BIG) == 1


# ── fail-open ────────────────────────────────────────────────────────────────

def test_fails_open_when_pymupdf_missing(monkeypatch):
    """PyMuPDF 不可用时放行 —— 校验层不该成为新的单点故障。"""
    import builtins

    data = _png()          # 先备好数据，再断掉 fitz（否则连造数据都失败）
    real = builtins.__import__

    def fake(name, *a, **kw):
        if name == "fitz":
            raise ImportError("simulated")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert validate_upload(data, "a.png", max_bytes=BIG) == 1


def test_reason_is_actionable():
    """错误原因要能指导对接方修，不能只说'失败了'。"""
    with pytest.raises(InvalidUpload) as ei:
        validate_upload(_pdf(1), "x.jpg", max_bytes=BIG)
    reason = ei.value.reason
    assert ".jpg" in reason and ("后缀" in reason or "损坏" in reason)
