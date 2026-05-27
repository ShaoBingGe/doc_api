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
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.document import Document
from app.processors.base import extract_json
from app.processors.factory import ProcessorFactory

logger = logging.getLogger(__name__)


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

    for sid in sample_document_ids:
        sid_str = str(sid)
        try:
            doc = db.get(Document, sid)
            if not doc:
                raise NotFoundError(f"sample document {sid} not found")
            if not doc.storage_path:
                raise ValueError(f"document {sid} has no storage_path")

            parsed = _run_single(
                file_path=doc.storage_path,
                composed_prompt=composed_prompt,
                composed_schema=composed_schema,
                processor_spec=processor_spec,
                model_name=model_name,
            )
            outputs[sid_str] = parsed
        except Exception as exc:
            logger.warning("OCR failed for sample %s: %s", sid, exc)
            outputs[sid_str] = {"_error": str(exc)[:500]}
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
