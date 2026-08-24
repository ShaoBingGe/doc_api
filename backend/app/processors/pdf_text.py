"""PDF 文本层解析 —— 按版面坐标重建阅读顺序，并评估可用性。

## 为什么不能直接用 `page.get_text()`

PDF 里的文本对象按**绘制顺序**存储，与视觉顺序无关。表格型票据（市面上占比很高）
常常先绘制整列标签、再绘制整列值，`get_text("text")` 拿到的是：

    PAGE / DATE / ORDER NO. / INVOICE NO. / CUSTOMER      ← 标签全堆在前
    1 / : / : / 633431 / PP CHIN HIN SDN BHD              ← 值全堆在后

标签与值完全错位。把这种文本喂给模型，比不给还糟——会诱导它把值配错字段。

本模块用带坐标的 `get_text("words")` 重建视觉顺序（先按 y 聚行、行内按 x 排序、
列间距用空格保留），上例即还原成 `INVOICE NO. : LX 633431`。

## 与 OCR 的关系

文本层是**首选**来源：它直接来自 PDF 文本对象，不经 OCR，字符精确。
仅当文本层缺失或质量不可信时（扫描件、纯图 PDF、字体未嵌入导致的乱码），
才退回纯视觉 OCR —— 即 `TextQuality.usable is False` 的情形。
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    _PYMUPDF = True
except ImportError:  # pragma: no cover
    _PYMUPDF = False

# ── 质量阈值 ─────────────────────────────────────────────────────────────────
MIN_CHARS = 80          # 少于此量视为噪声（水印/页眉），无校对价值
MIN_WORDS_PER_PAGE = 8  # 每页词数下限：低于此说明主体是图，文本只是零星标注
MAX_GARBLED_RATIO = 0.12   # 乱码字符占比上限（字体未嵌入 / CID 未映射）
MAX_CHARS = 12000       # 注入上限 ≈ 3k token

# 行聚类与分列：以页面字号自适应，不用固定像素值
_Y_TOL_RATIO = 0.6      # 同一行的 y 容差 = 中位字高 × 该系数
_COL_GAP_RATIO = 1.2    # 超过 中位字宽 × 该系数 视为跨列，插入多空格

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class TextQuality:
    """文本层可用性评估结果。"""

    usable: bool
    reason: str
    chars: int = 0
    pages_with_text: int = 0
    garbled_ratio: float = 0.0

    def __str__(self) -> str:  # 便于日志
        return (f"usable={self.usable} reason={self.reason} chars={self.chars} "
                f"pages={self.pages_with_text} garbled={self.garbled_ratio:.1%}")


def _garbled_ratio(text: str) -> float:
    """不可映射/替换字符的占比。

    字体未嵌入或 CID 映射缺失时，PyMuPDF 会吐出 U+FFFD 或私有区字符——
    这类文本看着有内容，实际全是错的，必须判定为不可用。
    """
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        if ch in "�￾":
            bad += 1
        elif "" <= ch <= "":  # 私有使用区
            bad += 1
        elif unicodedata.category(ch) in ("Co", "Cn"):
            bad += 1
    return bad / len(text)


def _page_layout_text(page) -> str:
    """单页：按坐标重建阅读顺序。

    `get_text("words")` 返回 (x0, y0, x1, y1, word, block_no, line_no, word_no)。
    我们**只用坐标**分行分列，不信任 block/line 编号——表格型 PDF 里同一视觉行
    常常横跨多个 block，按 block 组织会重新引入错位。
    """
    words = page.get_text("words")
    if not words:
        return ""

    heights = [w[3] - w[1] for w in words if w[3] > w[1]]
    widths = [(w[2] - w[0]) / max(len(w[4]), 1) for w in words if w[2] > w[0] and w[4]]
    med_h = statistics.median(heights) if heights else 10.0
    med_w = statistics.median(widths) if widths else 5.0
    y_tol = max(med_h * _Y_TOL_RATIO, 1.0)
    col_gap = max(med_w * _COL_GAP_RATIO, 3.0)

    rows: dict[int, list] = defaultdict(list)
    for w in words:
        rows[int(w[1] / y_tol)].append(w)

    lines: list[str] = []
    for key in sorted(rows):
        ws = sorted(rows[key], key=lambda w: w[0])
        parts: list[str] = []
        prev_x1: float | None = None
        for w in ws:
            if prev_x1 is not None:
                # 跨列用 3 空格标示，使 LLM 能看出列对齐；行内正常词距用 1 空格
                parts.append("   " if (w[0] - prev_x1) > col_gap else " ")
            parts.append(w[4])
            prev_x1 = w[2]
        line = "".join(parts).rstrip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_layout_text(path: str, max_pages: int) -> tuple[str, TextQuality]:
    """→ (重建版面顺序的文本, 质量评估)。

    `max_pages` 必须与实际送给模型的图片页数一致——不能让文本层引用模型
    没看到的页面，否则模型可能"提取"出图上根本没有的票据。
    """
    if not _PYMUPDF:
        return "", TextQuality(False, "pymupdf_unavailable")

    try:
        parts: list[str] = []
        pages_with_text = 0
        total_words = 0
        n_pages = 0
        with fitz.open(path) as doc:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                n_pages += 1
                t = _page_layout_text(page)
                total_words += len(page.get_text("words") or [])
                if t.strip():
                    pages_with_text += 1
                    parts.append(f"--- 第 {i + 1} 页 ---\n{t}")
        text = "\n\n".join(parts).strip()
    except Exception:  # noqa: BLE001 — 文本层取不到不该影响主流程
        logger.debug("layout text extraction failed: %s", path, exc_info=True)
        return "", TextQuality(False, "extract_error")

    text = _CONTROL_RE.sub("", text)
    chars = len(text)
    garbled = _garbled_ratio(text)

    if chars < MIN_CHARS:
        return "", TextQuality(False, "no_text_layer", chars, pages_with_text, garbled)
    if n_pages and (total_words / n_pages) < MIN_WORDS_PER_PAGE:
        # 有文本但极稀疏：多半是扫描件上盖的水印/页码，主体仍是图
        return "", TextQuality(False, "too_sparse", chars, pages_with_text, garbled)
    if garbled > MAX_GARBLED_RATIO:
        # 字体未嵌入：文本"有"但全是错的，用了反而误导模型
        return "", TextQuality(False, "garbled", chars, pages_with_text, garbled)

    if chars > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…（文本层过长已截断，其余以图像为准）"

    return text, TextQuality(True, "ok", chars, pages_with_text, garbled)
