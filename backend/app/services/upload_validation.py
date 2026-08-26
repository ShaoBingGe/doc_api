"""上传文件的提交期校验 —— 注定失败的文件不发 taskId。

## 为什么要有这一层

2026-08-26 线上：两张 `.jpg` 被正常受理并发了 taskId，随后 Gemini 连续三次回
`400 INVALID_ARGUMENT: Unable to process input image`，重试耗尽后标 FAILED。
对接方拿着 taskId 轮询了几分钟才知道白等。

**发出 taskId 是一个承诺。** 与其承诺之后毁约，不如在受理时就说清楚
——同样的失败，反馈从"几分钟 + 3 次模型调用"缩短到"毫秒级 + 0 次调用"。

## 校验什么

全部是本地的、确定性的、零 LLM 的检查，按代价从低到高排列，先命中先返回：

  1. 扩展名白名单
  2. 非空 / 不超上限
  3. **魔数与扩展名一致** —— 抓"改了后缀的假 jpg"，这是模型 400 的高发成因
  4. **真的能打开** —— 用 PyMuPDF 实际解析，抓结构性损坏
  5. **像素下限** —— 小到不可能是票据的图（1×1 之类）

## 不校验什么

不判断"是不是发票"、"清不清晰"——那是模型的职责，本层只回答
"这个字节流能不能被下游正常读取"。宁可放过，不可错杀：任何一步出现
我们自己没预料的异常，一律**放行**（fail-open），把判断权交回模型。

**刻意不做尺寸上限**：Gemini 的实际像素/体积上限没有公开确切数值，
凭猜设一个阈值会把本来能识别的大图误拒 —— 那比漏过一个坏文件更糟。
体积已由 `max_bytes` 兜底。

**能力边界**：正常尺寸的截断图片能被抓到（实测 200×150 的 PNG 截到 1/3
会报 `premature end of data`）；但百来字节的退化小图截断后仍可能被接受。
本层不承诺挡掉所有会让模型报错的输入 —— 剩下的仍由重试与 FAILED 兜底，
且失败原件现在会保留（见 `async_task_service.mark_failed`）以便取证。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: 与 extract_service._ALLOWED_EXTENSIONS 保持一致
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx"}

#: 扩展名 → 期望的魔数前缀（任一匹配即可）
_MAGIC = {
    ".pdf": [b"%PDF-"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".xlsx": [b"PK\x03\x04", b"PK\x05\x06"],
}

#: PyMuPDF 能直接解码的图片类型（xlsx 只做魔数校验，不解码）
_DECODABLE = {".pdf", ".png", ".jpg", ".jpeg"}

#: 单边像素下限。只拦"小到不可能是票据"的图，不设上限（理由见模块头）。
MIN_PIXELS = 16


class InvalidUpload(Exception):
    """文件在受理阶段就能判定不可用。`reason` 直接对外，要能指导对接方修。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate_upload(file_bytes: bytes, filename: str, *, max_bytes: int) -> int:
    """校验上传内容，→ 页数（PDF 为实际页数，图片恒 1）。

    不通过时抛 `InvalidUpload`，其 `reason` 面向对接方、说清"哪儿不对"。
    顺带返回页数，省掉调用方再解析一次文件。
    """
    name = filename or ""
    ext = Path(name).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidUpload(
            f"不支持的文件类型 {ext or '(无扩展名)'}，"
            f"仅接受 {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if not file_bytes:
        raise InvalidUpload("文件内容为空（0 字节）")
    if len(file_bytes) > max_bytes:
        raise InvalidUpload(
            f"文件 {len(file_bytes) / 1024 / 1024:.1f} MB 超过上限 "
            f"{max_bytes / 1024 / 1024:.0f} MB"
        )

    expected = _MAGIC.get(ext, [])
    if expected and not any(file_bytes.startswith(m) for m in expected):
        raise InvalidUpload(
            f"文件内容与扩展名 {ext} 不符（文件头是 "
            f"{file_bytes[:4].hex()}）——请确认没有改错后缀或上传了损坏的文件"
        )

    if ext not in _DECODABLE:
        return 1

    return _probe(file_bytes, ext)


def _probe(file_bytes: bytes, ext: str) -> int:
    """用 PyMuPDF 实打实地打开一遍，→ 页数。

    这一步抓的是魔数正确但内容截断/损坏的文件——它们的文件头没问题，
    只有真正解码才暴露。
    """
    try:
        import fitz
    except ImportError:  # pragma: no cover — 生产必装
        logger.warning("PyMuPDF 不可用，跳过解码校验")
        return 1

    filetype = "pdf" if ext == ".pdf" else ext.lstrip(".")
    try:
        with fitz.open(stream=file_bytes, filetype=filetype) as doc:
            n = len(doc)
            if n < 1:
                raise InvalidUpload("文件不含任何页面/图像，可能已损坏")
            if ext != ".pdf":
                # 用 get_image_info() 取**真实像素**：page.rect 是点(points)，
                # 与像素差 72/96 的比例（400×300 的图 rect 是 300×225），
                # 拿它当像素判定会系统性偏小 1/3。
                info = doc[0].get_image_info() or []
                if info:
                    w = int(info[0].get("width") or 0)
                    h = int(info[0].get("height") or 0)
                    if w < MIN_PIXELS or h < MIN_PIXELS:
                        raise InvalidUpload(f"图像尺寸过小（{w}×{h}），无法识别")
            return n
    except InvalidUpload:
        raise
    except Exception as exc:  # noqa: BLE001
        # 解码失败 = 下游模型也读不了，这正是要拦的情况
        raise InvalidUpload(
            f"文件无法解析（{type(exc).__name__}），可能已损坏或被截断"
        ) from exc


def page_count_or_default(file_bytes: bytes, filename: str) -> int:
    """尽力取页数，失败返回 1。给不做拦截、只想知道页数的调用方用。"""
    try:
        return validate_upload(file_bytes, filename, max_bytes=1 << 40)
    except InvalidUpload:
        return 1
