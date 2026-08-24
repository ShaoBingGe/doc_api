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

# 单次调用送入模型的最大页数。**超出部分直接丢弃，不做分片再合并**
# （分片会打断跨页票据的上下文，且成本翻倍；产品决策为「超过的部分不予识别」）。
# 从 5 提到 16：实测「整月发票扫成一个 PDF、每页一张」的场景很常见，
# 5 页截断会让第 6 张起的发票根本进不了模型视野（testing/DOC_07_15_25006 6 页 6 张即为此例）。
MAX_PAGES = 16
_MAX_PAGES = MAX_PAGES  # 向后兼容旧引用

# 文本层的提取与质量判据集中在 app.processors.pdf_text（阈值也在那里）。

_TEXT_LAYER_BLOCK = """

━━━━━━ 该 PDF 的嵌入文本层（字符精确，已按版面坐标重排）━━━━━━
下列文字直接取自 PDF 内部文本对象，**不经 OCR，字符准确**；并已按版面坐标
重建阅读顺序（先按行、行内从左到右），同一行内的**连续多个空格表示跨列**。

使用规则：
1. **字符以文本层为准**：当你从图像读出的数字或编号（发票号、注册号/SSM、税号、
   金额、日期、PO/DO 号）与文本层中对应内容不一致时，**采用文本层的值**——
   图像可能因印刷重叠、字号过小或扫描模糊而误读单个字符。
2. **版面归属仍以图像为准**：表格的行列对应、哪个值属于哪个字段、多张票据的
   切分边界，都以图像所见为准。文本层的换行与空格只是排版还原，不构成语义。
3. 注意「标签与值可能分列」：本文本层已尽力还原为 `标签 : 值` 同行，但若某行只有
   标签或只有值，请结合图像判断其归属，**不要把相邻行的值硬配给某个标签**。
4. 文本层中**没有出现**的内容不要凭空引入；文本层与图像都没有的字段，按缺失处理。

{text}
━━━━━━ 文本层结束 ━━━━━━
"""
_RENDER_DPI = 150
# qwen3-vl-plus 实测 ~34s/页；16 页单次调用可达 ~9 分钟，故超时同步放宽到 10 分钟。
# 注意：前端 OCR_TIMEOUT（api-client.ts）若仍为 5 分钟，长文档在 UI 侧会先超时，
# 但后端会跑完并落库——开放平台走 HTTP 直连，不受前端超时影响。
_HTTP_TIMEOUT = 600.0


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

    @staticmethod
    def _is_vision_name(name: str) -> bool:
        """qwen 系视觉模型命名：含 "vl"（qwen-vl-plus / qwen3-vl-flash）或
        含 "ocr"（qwen3.5-ocr / qwen-vl-ocr）。曾用「仅 vl」判定，导致
        qwen3.5-ocr 被误判为文本模型：评测时显式传入被静默回退到
        QWEN_MODEL（评测结果被偷换），生产时被误加 response_format 而
        返回空 content。"""
        n = (name or "").lower()
        return "vl" in n or "ocr" in n

    def _pick_model(self, *, vision: bool) -> str:
        if self._explicit:
            if vision == self._is_vision_name(self._explicit):
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

    @staticmethod
    def _extract_text_layer(path: str) -> tuple[str, object]:
        """抽取 PDF 文本层（按版面坐标重排），→ (text, TextQuality)。

        解析细节与降级判据见 `app.processors.pdf_text`。这里只做转调：
        文本层可用就注入，不可用（扫描件 / 稀疏 / 乱码）返回空串走纯 OCR。
        """
        from app.processors.pdf_text import extract_layout_text

        return extract_layout_text(path, _MAX_PAGES)

    def _chat(self, *, model: str, messages: list, as_json: bool) -> str:
        # temperature=0：评测确定性。优化轮的版本对比（门口认证 / 单调守护 /
        # 终轮确认）都是单次打分，采样噪声会翻转「哪个版本更好」的判定；
        # 与 GeminiProcessor 的默认 temperature=0 对齐。
        body: dict = {"model": model, "messages": messages, "temperature": 0}
        # response_format=json_object 只给纯文本模型：DashScope 视觉模型
        # （vl/ocr 命名）带此参数会返回 200 + 空 content（qwen3.5-ocr 实测）。
        # 已知降级：qwen 链路无法强制 runtime_config["response_schema"]
        # （DashScope 不支持 json_schema 结构化输出于视觉模型）——输出形状
        # 完全依赖 prompt 内的紧凑 schema 树（composer 已在数组根家族下
        # 显式声明「输出为 JSON 数组」）。Gemini 链路才有 schema 硬约束。
        if as_json and not self._is_vision_name(model):
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
            # 文本层优先：数字版 PDF 的文本对象字符精确，用来纠正图像上的
            # 单字符误读。取不到或不可信（扫描件/稀疏/乱码）时自动降级为纯 OCR。
            text_layer, quality = self._extract_text_layer(file_path)
            name = pathlib.Path(file_path).name
            if text_layer:
                instruction = instruction + _TEXT_LAYER_BLOCK.format(text=text_layer)
                logger.info("text layer attached (%s): %s", name, quality)
            else:
                logger.info("text layer unusable, falling back to OCR only (%s): %s",
                            name, quality)
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
