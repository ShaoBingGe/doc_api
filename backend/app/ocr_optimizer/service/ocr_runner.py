"""
OCR runner — runs one full OCR call per sample document using a composed
prompt, and returns the parsed JSON outputs.

Why a dedicated helper:
  - centralizes the file_path → processor → JSON parse pipeline
  - lets the orchestrator easily plug in a mock / cached OCR for dev
  - records token / latency metrics in one place
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.document import Document
from app.processors.base import extract_json
from app.processors.factory import ProcessorFactory

logger = logging.getLogger(__name__)

# OCR 并发度：视觉调用是网络 IO，但 _run_single 内的 PyMuPDF 渲染吃
# CPU/内存，2G 机器上压到 3。
_OCR_CONCURRENCY = 3


def run_ocr_on_samples(
    db: Session,
    *,
    sample_document_ids: list[uuid.UUID],
    composed_prompt: str,
    composed_schema: dict | None,
    processor_spec: str,
    model_name: str | None,
) -> dict[str, Any]:
    """
    Run OCR on every sample document.

    Returns a dict {str(sample_doc_id): parsed_json_or_error_marker}.
    Failed samples are recorded as {"_error": "<msg>"} so the round still
    completes and the orchestrator can decide how to handle partial failure.
    """
    outputs: dict[str, Any] = {}

    # 预取 storage_path（主线程，DB 操作不能进工作线程）。
    specs: list[tuple[str, str]] = []
    for sid in sample_document_ids:
        sid_str = str(sid)
        doc = db.get(Document, sid)
        if not doc:
            outputs[sid_str] = {"_error": f"sample document {sid} not found"}
        elif not doc.storage_path:
            outputs[sid_str] = {"_error": f"document {sid} has no storage_path"}
        else:
            specs.append((sid_str, doc.storage_path))

    if not specs:
        return outputs

    def _ocr_one(sid_str: str, path: str) -> tuple[str, Any]:
        try:
            parsed = _run_single(
                file_path=path,
                composed_prompt=composed_prompt,
                composed_schema=composed_schema,
                processor_spec=processor_spec,
                model_name=model_name,
            )
            return sid_str, parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR failed for sample %s: %s", sid_str, exc)
            return sid_str, {"_error": str(exc)[:500]}

    # 并发 OCR：纯网络 IO（视觉模型调用），互不依赖。并发数压低到 3，
    # 因为 _run_single 内含 PyMuPDF 渲染（吃 CPU/内存），2G 机器上不宜过高。
    workers = min(_OCR_CONCURRENCY, len(specs))
    if workers <= 1:
        for sid_str, path in specs:
            k, v = _ocr_one(sid_str, path)
            outputs[k] = v
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for k, v in ex.map(lambda s: _ocr_one(*s), specs):
                outputs[k] = v
    return outputs


def _run_single(
    *,
    file_path: str,
    composed_prompt: str,
    composed_schema: dict | None,
    processor_spec: str,
    model_name: str | None,
) -> Any:
    processor = ProcessorFactory.create(processor_spec, model_name=model_name)

    runtime: dict = {}
    if composed_schema:
        runtime["response_schema"] = composed_schema

    start = time.time()
    raw_text = processor.process_document(file_path, composed_prompt, runtime or None)
    elapsed_ms = int((time.time() - start) * 1000)
    logger.debug("OCR call took %dms, returned %d chars", elapsed_ms, len(raw_text or ""))

    return _parse_json_lenient(raw_text)


def invalid_output_reason(out: Any) -> str | None:
    """判定一份 OCR 输出是否为「传输/解析级失败」——这类样本不允许参与
    质量决策（评分聚合、早停、版本比较、优化目标），否则一次网络抖动 /
    限流 / 截断就会把整轮分数打成假 0，劫持单调守护与优化方向。

    有效 = 模型真的返回了可解析的 JSON 容器（dict/list，含空 list —— 那是
    「模型判定无记录」的真实提取结果）。无效 = _error（异常）、_raw（不可
    解析）、空 dict（空响应）、非容器标量。返回 None 表示有效，否则返回
    人类可读的原因。
    """
    if out is None:
        return "no output"
    if isinstance(out, dict):
        if "_error" in out:
            return f"OCR error: {str(out.get('_error'))[:200]}"
        if "_raw" in out:
            return "output not parseable as JSON"
        if not out:
            return "empty output"
        return None
    if isinstance(out, list):
        return None
    return f"non-JSON-container output ({type(out).__name__})"


def _parse_json_lenient(raw: str | None) -> Any:
    if not raw:
        return {}
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        blocks = extract_json(text)
        if blocks:
            try:
                return json.loads(blocks[0])
            except json.JSONDecodeError:
                pass
    # Last-resort: best-effort brace extraction
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    logger.warning("OCR output not parseable as JSON: %r", text[:200])
    return {"_raw": text[:2000]}
