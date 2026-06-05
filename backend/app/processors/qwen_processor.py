"""
Qwen / 阿里云百炼 (DashScope) document processor.

为什么需要它：大陆云实例无法访问 Google Gemini，改用阿里云 DashScope 的
`qwen-vl-ocr`（视觉 OCR）做生产识别，`qwen-plus`（文本）做反思/优化。

走 DashScope 的 **OpenAI 兼容端点**（`/compatible-mode/v1/chat/completions`），
只用 httpx（已是依赖），不额外引入 openai SDK。

输入处理（对齐 DocumentProcessor 契约）：
  - PDF  → 用 PyMuPDF 渲染每页为 PNG（base64 image_url）喂视觉模型；
  - 图片 → 直接 base64；
  - .txt → 纯文本对话（反思/优化器的 llm_text_completion 走这里），用文本模型。

对传入的 model_name 做鲁棒处理：非 qwen 名称（如历史模板里残留的
"gemini-2.5-flash"）一律忽略，回退到配置的 QWEN_MODEL / QWEN_TEXT_MODEL。
"""

from __future__ import annotations

import base64
import logging
import pathlib
from typing import Optional

import httpx

from app.processors.base import DocumentProcessor

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF — self-contained PDF renderer (no poppler needed)
    _PYMUPDF = True
except ImportError:  # pragma: no cover
    _PYMUPDF = False
    logger.warning("PyMuPDF not available; Qwen processor can't render PDFs. pip install pymupdf")

# Keep requests bounded: most invoices are 1-2 pages.
_MAX_PAGES = 5
_RENDER_DPI = 150
_HTTP_TIMEOUT = 180.0


class QwenProcessor(DocumentProcessor):
    """DashScope (Qwen) processor — vision OCR + text completion."""

    def __init__(self, model_name: str | None = None):
        from app.core.config import get_settings

        s = get_settings()
        self.api_key = s.QWEN_API_KEY
        if not self.api_key:
            raise ImportError("QWEN_API_KEY is not configured")
        self.base_url = (s.DASHSCOPE_BASE_URL or "").rstrip("/")
        self.vision_model = s.QWEN_MODEL or "qwen-vl-ocr"
        self.text_model = s.QWEN_TEXT_MODEL or "qwen-plus"
        # Honour an explicit qwen model name; ignore foreign names (gemini, …).
        self._explicit = model_name if (model_name or "").lower().startswith("qwen") else None

    # ── helpers ────────────────────────────────────────────────────────────

    def _pick_model(self, *, vision: bool) -> str:
        if self._explicit:
            is_vl = "vl" in self._explicit.lower()
            if vision == is_vl:
                return self._explicit
        return self.vision_model if vision else self.text_model

    def _pdf_to_png_data_urls(self, path: str) -> list[str]:
        if not _PYMUPDF:
            raise ImportError("PyMuPDF required to OCR PDFs (pip install pymupdf)")
        urls: list[str] = []
        with fitz.open(path) as doc:
            for i, page in enumerate(doc):
                if i >= _MAX_PAGES:
                    break
                pix = page.get_pixmap(dpi=_RENDER_DPI)
                png = pix.tobytes("png")
                urls.append("data:image/png;base64," + base64.b64encode(png).decode())
        return urls

    @staticmethod
    def _image_to_data_url(path: str) -> str:
        suffix = pathlib.Path(path).suffix.lower()
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
        data = pathlib.Path(path).read_bytes()
        return f"data:{mime};base64," + base64.b64encode(data).decode()

    def _chat(self, *, model: str, messages: list, as_json: bool) -> str:
        body: dict = {"model": model, "messages": messages}
        # qwen text models support OpenAI-style json_object; vision models may not.
        if as_json and "vl" not in model.lower():
            body["response_format"] = {"type": "json_object"}
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            logger.warning("Unexpected DashScope response shape: %s", str(data)[:300])
            return ""

    # ── DocumentProcessor API ──────────────────────────────────────────────

    def process_document(
        self,
        file_path: str,
        instruction: str,
        runtime_config: Optional[dict] = None,
    ) -> str:
        runtime_config = runtime_config or {}
        as_json = runtime_config.get("response_mime_type", "application/json") != "text/plain"
        suffix = pathlib.Path(file_path).suffix.lower()

        # ── text completion (reflection / optimizer) ──
        if suffix == ".txt":
            user_text = pathlib.Path(file_path).read_text(encoding="utf-8", errors="replace")
            messages = [
                {"role": "system", "content": instruction},
                {"role": "user", "content": user_text},
            ]
            return self._chat(model=self._pick_model(vision=False), messages=messages, as_json=as_json)

        # ── vision OCR (production extraction) ──
        if suffix == ".pdf":
            image_urls = self._pdf_to_png_data_urls(file_path)
        elif suffix in (".png", ".jpg", ".jpeg"):
            image_urls = [self._image_to_data_url(file_path)]
        else:
            raise ValueError(f"QwenProcessor: unsupported file type {suffix!r}")

        content: list[dict] = [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
        content.append({"type": "text", "text": instruction})
        messages = [{"role": "user", "content": content}]
        return self._chat(model=self._pick_model(vision=True), messages=messages, as_json=as_json)

    def get_model_version(self) -> str:
        return f"qwen|{self.vision_model}"
