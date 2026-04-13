"""
DocumentService — 文档上传、存储、处理调度、查询、删除。

职责边界：
  - 文件 I/O 委托给 StorageBackend（LocalStorage 原型）
  - AI 提取委托给 ProcessorFactory（engine 层）
  - 不直接依赖 API 路由层
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import FileTooLargeError, NotFoundError, UnsupportedFileTypeError
from app.models.document import Document, DocumentStatus, ProcessingResult
from app.schemas.document import (
    DocumentDetail,
    DocumentResponse,
    HighlightsResponse,
    ProcessingResultResponse,
    RegionOcrRequest,
    RegionOcrResponse,
    ReprocessRequest,
)
from app.schemas.common import PaginatedResponse

settings = get_settings()

_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx"}
_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# ── Storage helper (simple local FS, swappable for S3) ───────────────────────

def _save_upload(file_data: bytes, filename: str) -> str:
    """Save bytes to UPLOAD_DIR and return the relative storage path."""
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Prefix with UUID to avoid collisions
    safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
    dest = upload_dir / safe_name
    dest.write_bytes(file_data)
    return str(dest)


def _delete_file(storage_path: str) -> None:
    try:
        Path(storage_path).unlink(missing_ok=True)
    except Exception:
        pass  # best-effort


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_file(filename: str, size: int, content_type: str | None = None) -> str:
    """Validate file extension, MIME type, and size. Returns file_type string."""
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"File type '{ext}' is not supported. Allowed: {', '.join(_ALLOWED_EXTENSIONS)}"
        )
    if size > settings.max_upload_bytes:
        raise FileTooLargeError(
            f"File size {size / 1024 / 1024:.1f} MB exceeds limit of {settings.MAX_UPLOAD_SIZE_MB} MB"
        )
    return ext.lstrip(".")


# ── Service functions ─────────────────────────────────────────────────────────

def upload_document(
    db: Session,
    *,
    filename: str,
    file_data: bytes,
    content_type: str | None = None,
    processor_type: str | None = None,
    template_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> Document:
    """
    Persist the uploaded file and create a Document record.
    Triggers synchronous AI processing immediately (SyncRunner prototype).
    """
    file_type = _validate_file(filename, len(file_data), content_type)
    storage_path = _save_upload(file_data, filename)

    doc = Document(
        user_id=user_id,
        filename=filename,
        file_type=file_type,
        file_size=len(file_data),
        storage_path=storage_path,
        status=DocumentStatus.queued,
    )
    db.add(doc)
    db.flush()  # get doc.id before processing

    # Trigger synchronous processing
    _run_extraction(db, doc, processor_type=processor_type or settings.DEFAULT_PROCESSOR)
    db.commit()
    db.refresh(doc)
    return doc


def _run_extraction(
    db: Session,
    doc: Document,
    *,
    processor_type: str,
    prompt: str | None = None,
    schema: dict | None = None,
    previous_version: int = 0,
) -> ProcessingResult:
    """
    Run AI extraction synchronously and persist the result.
    Wrapped in try/except so a processing failure updates Document.status=failed.
    """
    import time

    doc.status = DocumentStatus.processing
    db.flush()

    try:
        start_ms = int(time.time() * 1000)
        raw_output, raw_structured, model_name = _call_processor(
            doc.storage_path, processor_type, prompt=prompt, schema=schema
        )
        elapsed_ms = int(time.time() * 1000) - start_ms

        structured_data = _normalize_structured_data(raw_structured)
        inferred_schema = _infer_schema(raw_structured)

        result = ProcessingResult(
            document_id=doc.id,
            version=previous_version + 1,
            processor_type=processor_type,
            model_name=model_name,
            prompt_used=prompt,
            raw_output=raw_output,
            structured_data=structured_data,
            inferred_schema=inferred_schema,
            processing_time_ms=elapsed_ms,
            tokens_used=raw_output.get("usage", {}).get("total_tokens") if isinstance(raw_output, dict) else None,
        )
        db.add(result)
        db.flush()  # populate result.id before annotation FK
        _create_annotations(db, doc.id, result, structured_data)
        doc.status = DocumentStatus.completed
        return result

    except Exception as exc:
        doc.status = DocumentStatus.failed
        doc.error_message = str(exc)[:1024]
        db.flush()
        raise


def _call_processor(
    storage_path: str,
    processor_type: str,
    *,
    prompt: str | None,
    schema: dict | None,
) -> tuple[dict, dict, str]:
    """
    Delegate to ProcessorFactory. Returns (raw_output, structured_data, model_name).
    Falls back to mock data if the processor raises an unexpected error.
    """
    import json

    from app.processors.factory import ProcessorFactory

    processor = ProcessorFactory.create(processor_type)
    instruction = prompt or "Extract all structured data fields from this document."
    runtime_config = {"schema": schema} if schema else None

    raw_text = processor.process_document(storage_path, instruction, runtime_config)
    model_name = processor.get_model_version()

    # Parse JSON from the returned string
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try to extract from ```json ... ``` fences
        from app.processors.base import extract_json
        blocks = extract_json(raw_text)
        parsed = json.loads(blocks[0]) if blocks else {}

    # Normalise: processors may return a list (e.g. mock returns a list of docs)
    if isinstance(parsed, list):
        structured_data = parsed[0] if parsed else {}
    else:
        structured_data = parsed

    raw_output = {"raw_text": raw_text, "parsed": parsed}
    return raw_output, structured_data, model_name


def _mock_extraction(storage_path: str) -> tuple[dict, dict, str]:
    filename = Path(storage_path).name
    structured_data = {
        "invoice_no": "INV-2024-001",
        "invoice_date": "2024-01-15",
        "seller_name": "示例供应商有限公司",
        "buyer_name": "示例采购方有限公司",
        "total_amount": 10800.00,
        "tax_amount": 1400.00,
        "currency": "CNY",
        "items": [
            {"name": "产品 A", "quantity": 10, "unit_price": 880.0, "amount": 8800.0},
            {"name": "产品 B", "quantity": 2, "unit_price": 1000.0, "amount": 2000.0},
        ],
        "_source_file": filename,
    }
    raw_output = {"mock": True, "structured_data": structured_data}
    return raw_output, structured_data, "mock-v1"


def _normalize_structured_data(raw: dict | list) -> list[dict]:
    """
    Normalize AI processor output to design format:
      [{id, keyName, value, confidence, bbox}, ...]

    Handles both flat-dict output (most processors) and pre-structured list output.
    """
    if isinstance(raw, list):
        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "keyName" in item:
                # Already in design format — ensure id is present
                entry = dict(item)
                if "id" not in entry:
                    entry["id"] = str(uuid.uuid4())
                entry.setdefault("confidence", None)
                entry.setdefault("bbox", None)
            else:
                entry = {
                    "id": item.get("id", str(uuid.uuid4())),
                    "keyName": item.get("key", item.get("name", "")),
                    "value": item.get("value"),
                    "confidence": item.get("confidence"),
                    "bbox": item.get("bbox") or item.get("bounding_box"),
                }
            result.append(entry)
        return result

    if isinstance(raw, dict):
        result = []
        for key, value in raw.items():
            if isinstance(value, dict) and "value" in value:
                # Processor returned structured per-field dict with metadata
                entry = {
                    "id": str(uuid.uuid4()),
                    "keyName": key,
                    "value": value.get("value"),
                    "confidence": value.get("confidence"),
                    "bbox": value.get("bbox") or value.get("bounding_box"),
                }
            else:
                entry = {
                    "id": str(uuid.uuid4()),
                    "keyName": key,
                    "value": value,
                    "confidence": None,
                    "bbox": None,
                }
            result.append(entry)
        return result

    return []


def _create_annotations(
    db: Session,
    doc_id: uuid.UUID,
    result: "ProcessingResult",
    structured_data: list,
) -> None:
    """Auto-create Annotation rows for every field in normalized structured_data."""
    from app.models.annotation import Annotation, AnnotationSource, FieldType

    def _field_type(v) -> str:
        if isinstance(v, bool):
            return FieldType.boolean
        if isinstance(v, (int, float)):
            return FieldType.number
        if isinstance(v, list):
            return FieldType.array
        return FieldType.string

    for field in structured_data:
        value = field.get("value")
        confidence = field.get("confidence")
        bbox = field.get("bbox")
        ann = Annotation(
            document_id=doc_id,
            processing_result_id=result.id,
            result_version=result.version,
            field_name=field.get("keyName", ""),
            field_value=str(value) if value is not None else None,
            field_type=_field_type(value),
            source=AnnotationSource.ai_detected,
            confidence=confidence,
            bounding_box=bbox,
        )
        db.add(ann)


def _infer_schema(data: dict) -> dict:
    """
    Simple JSON Schema inference from a structured_data dict.
    Production: replace with app.engine.schema_generator.infer().
    """
    def _type_of(v) -> str:
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, int):
            return "integer"
        if isinstance(v, float):
            return "number"
        if isinstance(v, list):
            return "array"
        if isinstance(v, dict):
            return "object"
        return "string"

    def _build(d: dict) -> dict:
        props = {}
        for k, v in d.items():
            t = _type_of(v)
            if t == "object":
                props[k] = _build(v)
            elif t == "array" and v and isinstance(v[0], dict):
                props[k] = {"type": "array", "items": _build(v[0])}
            else:
                props[k] = {"type": t}
        return {"type": "object", "properties": props}

    return _build(data)


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_document(db: Session, document_id: uuid.UUID) -> Document:
    doc = db.get(Document, document_id)
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")
    return doc


def list_documents(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    file_type: str | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> PaginatedResponse[DocumentResponse]:
    q = db.query(Document)
    if status_filter:
        q = q.filter(Document.status == status_filter)
    if file_type:
        q = q.filter(Document.file_type == file_type)

    sort_col = getattr(Document, sort_by, Document.created_at)
    q = q.order_by(desc(sort_col) if sort_order == "desc" else sort_col)

    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()

    return PaginatedResponse(
        items=[DocumentResponse.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


def get_document_detail(db: Session, document_id: uuid.UUID) -> DocumentDetail:
    doc = get_document(db, document_id)
    results = (
        db.query(ProcessingResult)
        .filter(ProcessingResult.document_id == document_id)
        .order_by(ProcessingResult.version)
        .all()
    )
    detail = DocumentDetail.model_validate(doc)
    detail.processing_results = [ProcessingResultResponse.model_validate(r) for r in results]
    detail.latest_result = ProcessingResultResponse.model_validate(results[-1]) if results else None
    return detail


def get_preview_url(db: Session, document_id: uuid.UUID) -> str:
    doc = get_document(db, document_id)
    # Prototype: serve as static file; production: generate S3 presigned URL
    return f"/static/uploads/{Path(doc.storage_path).name}"


def get_processing_results(
    db: Session, document_id: uuid.UUID
) -> list[ProcessingResultResponse]:
    get_document(db, document_id)  # 404 guard
    results = (
        db.query(ProcessingResult)
        .filter(ProcessingResult.document_id == document_id)
        .order_by(ProcessingResult.version)
        .all()
    )
    return [ProcessingResultResponse.model_validate(r) for r in results]


def reprocess_document(
    db: Session,
    document_id: uuid.UUID,
    body: ReprocessRequest,
) -> ProcessingResultResponse:
    doc = get_document(db, document_id)
    latest = (
        db.query(ProcessingResult)
        .filter(ProcessingResult.document_id == document_id)
        .order_by(desc(ProcessingResult.version))
        .first()
    )
    prev_version = latest.version if latest else 0
    processor = body.processor_type or (latest.processor_type if latest else settings.DEFAULT_PROCESSOR)

    result = _run_extraction(db, doc, processor_type=processor, previous_version=prev_version)
    db.commit()
    db.refresh(result)
    return ProcessingResultResponse.model_validate(result)


def delete_document(db: Session, document_id: uuid.UUID) -> None:
    doc = get_document(db, document_id)
    _delete_file(doc.storage_path)
    db.delete(doc)
    db.commit()


def get_highlights(
    db: Session,
    document_id: uuid.UUID,
    result_id: uuid.UUID | None = None,
) -> HighlightsResponse:
    """
    Build field→bounding_box mapping from annotations.
    Falls back to empty highlights if no annotations exist yet.
    """
    from app.models.annotation import Annotation
    from app.schemas.document import FieldHighlight, BoundingBoxSchema

    get_document(db, document_id)
    q = db.query(Annotation).filter(Annotation.document_id == document_id)
    if result_id:
        q = q.filter(Annotation.processing_result_id == result_id)
    annotations = q.all()

    highlights = []
    for ann in annotations:
        bbox = None
        if ann.bounding_box:
            bbox = BoundingBoxSchema(**ann.bounding_box)
        highlights.append(
            FieldHighlight(
                field_path=ann.field_name,
                bounding_box=bbox,
                is_derived=False,
            )
        )
    return HighlightsResponse(highlights=highlights)
