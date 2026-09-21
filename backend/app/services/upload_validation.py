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
  3. **魔数可识别** —— 内容必须是某个受支持类型；**不要求与扩展名一致**
  4. **真的能打开** —— 用 PyMuPDF 实际解析，抓结构性损坏
  5. **像素下限** —— 小到不可能是票据的图（1×1 之类）

## 后缀写错不再拒收（2026-09-02）

早先第 3 步要求"魔数与扩展名一致"，把 PNG 存成 `.jpg` 的文件判 `5000` 拒收。
但手机截图/相册导出改错后缀极常见，内容本身完全能识别——拒收是错杀。
现在改为**以内容为准**：魔数认出真实类型就放行，并由 `sniff_ext()` 把真实
类型回传给调用方，让落盘时用**正确的后缀**命名。

这一步是必须的：两个处理器都按文件后缀推 MIME
（`gemini_processor.mime_map` / `qwen_processor`），若只放宽校验而不纠正后缀，
PNG 会被当 `image/jpeg` 发给模型，失败只是从受理期挪到了模型 400——更晚更贵。

仍然**保留扩展名白名单**（第 1 步）：`.docx` 与 `.xlsx` 共用 ZIP 魔数
（`PK\x03\x04`），纯靠内容无法区分，去掉白名单会让 docx 混进来。
"后缀写错"与"类型不支持"是两回事，前者放行、后者照拒。

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


def sniff_ext(file_bytes: bytes) -> str | None:
    """按魔数认出真实类型 → 规范扩展名（如 `".png"`）；认不出返回 None。

    落盘前用它纠正后缀：处理器按后缀推 MIME，后缀错了模型就会收到错误的
    Content-Type。`.jpeg` 统一归一到 `.jpg`（同一魔数，取其一即可）。
    ZIP 系（xlsx/docx…）魔数相同，只能认到 `.xlsx`，故仍需扩展名白名单兜底。
    """
    if not file_bytes:
        return None
    for ext, magics in _MAGIC.items():
        if ext == ".jpeg":                     # 与 .jpg 同魔数，只报一个
            continue
        if any(file_bytes.startswith(m) for m in magics):
            return ext
    return None


def effective_ext(file_bytes: bytes, filename: str) -> str:
    """落盘该用的后缀：优先真实类型，认不出时退回原文件名的后缀。

    调用方（`_save_temp` / 异步 spool）用它命名临时文件，从而让下游按后缀
    推 MIME 的处理器拿到正确的类型。
    """
    return sniff_ext(file_bytes) or Path(filename or "").suffix.lower()


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

    # 以内容为准：认出真实类型就放行，**不要求与扩展名一致**（改错后缀是
    # 手机截图的常态，内容能识别就不该拒收）。认不出任何受支持类型才拒——
    # 那种字节流下游模型同样读不了，早拦早省。
    real = sniff_ext(file_bytes)
    if real is None:
        raise InvalidUpload(
            f"文件内容无法识别为受支持的类型（文件头是 "
            f"{file_bytes[:4].hex()}）——请确认文件没有损坏"
        )
    if real != ext and not (real == ".jpg" and ext == ".jpeg"):
        # 只记不拒：后续落盘会用真实类型命名，下游 MIME 因此正确。
        logger.info("上传文件后缀 %s 与实际类型 %s 不符，按实际类型处理", ext, real)

    if real not in _DECODABLE:
        return 1

    return _probe(file_bytes, real)


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
