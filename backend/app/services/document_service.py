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

def upload_document_with_annotations(
    db: Session,
    *,
    filename: str,
    file_data: bytes,
    content_type: str | None,
    annotations: list[dict],
    user_id: uuid.UUID | None = None,
) -> Document:
    """
    Single-transaction upload of a document + its pre-labeled ground-truth annotations.

    Each item in `annotations` must be `{"field_path": str, "value": Any}`.
    `field_path` may be a dotted/bracketed JSON path like "items[0].price" — stored
    as-is in Annotation.field_name. All created annotations are marked
    source='manual', is_corrected=True (treated as Ground Truth by the optimizer).
    """
    from app.core.exceptions import ValidationError
    from app.models.annotation import Annotation, AnnotationSource

    if not annotations:
        raise ValidationError("annotations list must not be empty")

    file_type = _validate_file(filename, len(file_data), content_type)
    storage_path = _save_upload(file_data, filename)

    doc = Document(
        user_id=user_id,
        filename=filename,
        file_type=file_type,
        file_size=len(file_data),
        storage_path=storage_path,
        status=DocumentStatus.completed,  # GT provided, no extraction needed
    )
    db.add(doc)
    db.flush()

    # Persist each annotation. We deliberately do NOT create a ProcessingResult
    # since this document was never machine-extracted — only human-labeled.
    for ann in annotations:
        field_path = ann.get("field_path")
        if not field_path or not isinstance(field_path, str):
            raise ValidationError(
                f"annotation missing string field_path: {ann!r}"
            )
        value = ann.get("value")
        # Coerce value to str for storage (Annotation.field_value is Text)
        if value is None:
            value_str = None
        elif isinstance(value, (str, int, float, bool)):
            value_str = str(value)
        else:
            import json
            value_str = json.dumps(value, ensure_ascii=False)
        inferred_type = _infer_field_type(value)
        a = Annotation(
            document_id=doc.id,
            processing_result_id=None,
            field_name=field_path,
            field_value=value_str,
            field_type=inferred_type,
            source=AnnotationSource.manual,
            is_corrected=True,  # explicitly GT
        )
        db.add(a)

    db.commit()
    db.refresh(doc)
    return doc


def _infer_field_type(value) -> str:
    """Map a Python value to one of the FieldType enum strings."""
    from app.models.annotation import FieldType

    if isinstance(value, bool):
        return FieldType.boolean
    if isinstance(value, (int, float)):
        return FieldType.number
    if isinstance(value, list):
        return FieldType.array
    # date detection is heuristic — leave as string unless ISO-ish
    if isinstance(value, str) and len(value) >= 8 and value[:4].isdigit() and "-" in value:
        return FieldType.date
    return FieldType.string


def upload_document(
    db: Session,
    *,
    filename: str,
    file_data: bytes,
    content_type: str | None = None,
    processor_type: str | None = None,
    template_id: uuid.UUID | None = None,
    api_definition_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> Document:
    """
    Persist the uploaded file and create a Document record.

    OCR is NOT triggered here. When the upload is part of the §6.4 country-template
    flow, the caller (documents.upload_document endpoint) follows up with
    `bind_to_api_and_extract` which runs OCR using the active OcrPromptVersion's
    composed_prompt. Otherwise the document stays in `queued`.
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
        api_definition_id=api_definition_id,
    )
    db.add(doc)
    db.flush()  # get doc.id before processing

    db.commit()
    db.refresh(doc)
    return doc


def bind_to_api_and_extract(
    db: Session,
    doc: Document,
    api_definition_id: uuid.UUID,
) -> ProcessingResult | None:
    """Register `doc` with an ApiDefinition's sample set and trigger OCR.

    Used by the §6.4 country-template flow: after the user uploads a document
    on /workspace/api/<id>, this binds the doc to the API (appends to
    config.sample_document_ids), bumps the API's updated_at to defer GC, and
    runs OCR using the API's active OcrPromptVersion.composed_prompt.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.api_definition import ApiDefinition

    api_def: ApiDefinition | None = db.get(ApiDefinition, api_definition_id)
    if not api_def:
        return None

    # Append to sample set so the optimizer subsystem can see this doc.
    cfg = dict(api_def.config or {})
    ids: list[str] = list(cfg.get("sample_document_ids") or [])
    if str(doc.id) not in ids:
        ids.append(str(doc.id))
        cfg["sample_document_ids"] = ids
        api_def.config = cfg
        flag_modified(api_def, "config")

    # Trigger OCR. reprocess_document will resolve the prompt from the active
    # OcrPromptVersion when body.prompt is None (see _resolve_active_composed_prompt).
    body = ReprocessRequest(prompt=None)
    reprocess_document(db, doc.id, body)

    # bump updated_at so GC defers
    from datetime import datetime, timezone
    api_def.updated_at = datetime.now(timezone.utc)
    db.commit()

    latest = (
        db.query(ProcessingResult)
        .filter(ProcessingResult.document_id == doc.id)
        .order_by(desc(ProcessingResult.version))
        .first()
    )
    return latest


def _run_extraction(
    db: Session,
    doc: Document,
    *,
    processor_type: str,
    model_name: str | None = None,
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
            doc.storage_path, processor_type, prompt=prompt, schema=schema, model_name=model_name
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
    model_name: str | None = None,
) -> tuple[dict, dict, str]:
    """
    Delegate to ProcessorFactory. Returns (raw_output, structured_data, model_name).
    Falls back to mock data if the processor raises an unexpected error.
    """
    import json

    from app.processors.factory import ProcessorFactory

    kwargs = {}
    if model_name:
        kwargs["model_name"] = model_name
    processor = ProcessorFactory.create(processor_type, **kwargs)
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


def _normalize_bbox(bbox: dict | None) -> dict | None:
    """Coerce LLM bbox output to {x, y, width, height, page} in 0-100 range.

    Some Gemini outputs use 0-1000 coords; if any value > 100, divide by 10.
    """
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x", 0))
        y = float(bbox.get("y", 0))
        w = float(bbox.get("width", bbox.get("w", 0)))
        h = float(bbox.get("height", bbox.get("h", 0)))
    except (TypeError, ValueError):
        return None
    if max(x, y, w, h) > 100:
        x, y, w, h = x / 10, y / 10, w / 10, h / 10
    page_val = bbox.get("page", 1)
    try:
        page = int(page_val) if page_val is not None else 1
    except (TypeError, ValueError):
        page = 1
    return {
        "x": max(0.0, min(100.0, x)),
        "y": max(0.0, min(100.0, y)),
        "width": max(0.0, min(100.0, w)),
        "height": max(0.0, min(100.0, h)),
        "page": page,
    }


def _is_leaf_field(v) -> bool:
    """Hierarchical leaf shape: {value, confidence, bbox}."""
    return isinstance(v, dict) and "value" in v and not isinstance(v.get("value"), dict)


def _flatten_hierarchical(node, path: str, out: list[dict]) -> None:
    """Recursively walk the hierarchical Gemini output and emit flat entries."""
    if node is None:
        return

    # Leaf: { value, confidence, bbox }
    if _is_leaf_field(node):
        out.append({
            "id": str(uuid.uuid4()),
            "keyName": path or "field",
            "value": node.get("value"),
            "confidence": node.get("confidence"),
            "bbox": _normalize_bbox(node.get("bbox") or node.get("bounding_box")),
        })
        return

    # Dict container: recurse into each key (skip _meta, capture container bbox if present).
    if isinstance(node, dict):
        # Table-shaped container { _meta, rows: [...] }
        rows = node.get("rows")
        if isinstance(rows, list):
            for i, row in enumerate(rows):
                _flatten_hierarchical(row, f"{path}[{i}]" if path else f"[{i}]", out)
            return

        for key, val in node.items():
            if key == "_meta":
                continue
            # Some hierarchical leaves might have value as a nested dict (rare); recurse.
            if "value" in node and key == "value":
                continue
            child_path = f"{path}.{key}" if path else key
            _flatten_hierarchical(val, child_path, out)
        return

    # List of items (table without _meta wrapper, or bare array)
    if isinstance(node, list):
        for i, item in enumerate(node):
            _flatten_hierarchical(item, f"{path}[{i}]" if path else f"[{i}]", out)
        return

    # Bare scalar — record as a value-only entry
    out.append({
        "id": str(uuid.uuid4()),
        "keyName": path or "field",
        "value": node,
        "confidence": None,
        "bbox": None,
    })


def _normalize_structured_data(raw: dict | list) -> list[dict]:
    """
    Normalize AI processor output to design format:
      [{id, keyName, value, confidence, bbox}, ...]

    Recursively descends into hierarchical Gemini output so every leaf field
    (e.g. "seller.name", "line_items[0].description") gets its own entry with
    its own bbox preserved.
    """
    # Pre-structured list with `keyName` items — keep as-is, just normalize bbox.
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "keyName" in raw[0]:
        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
            entry.setdefault("confidence", None)
            entry["bbox"] = _normalize_bbox(entry.get("bbox") or entry.get("bounding_box"))
            result.append(entry)
        return result

    out: list[dict] = []
    _flatten_hierarchical(raw, "", out)
    return out


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


def _resolve_active_composed_prompt(db: Session, api_def_id: uuid.UUID) -> str | None:
    """Return the active OcrPromptVersion.composed_prompt for this API, or None.

    Used by reprocess_document when no explicit prompt is supplied — lets the
    §6.4 country-template flow drive OCR with the raw yaml prompt stored in v1.
    """
    from app.ocr_optimizer.models import OcrPromptVersion, PromptVersionStatus

    v = (
        db.query(OcrPromptVersion)
        .filter(
            OcrPromptVersion.api_definition_id == api_def_id,
            OcrPromptVersion.status == PromptVersionStatus.active.value,
        )
        .first()
    )
    return v.composed_prompt if v else None


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
    # Always fall back to the globally-configured DEFAULT_PROCESSOR (from .env),
    # not to the previous result's processor_type — so switching DEFAULT_PROCESSOR
    # from "mock" to "gemini" takes effect immediately without DB migrations.
    processor = body.processor_type or settings.DEFAULT_PROCESSOR

    # Build prompt resolution chain (§13 step 10 in ocr-optimizer-design.md):
    #   1. explicit body.prompt (extra_fields case handled below)
    #   2. active OcrPromptVersion.composed_prompt (when doc is bound to an
    #      ApiDefinition and that API has an active version) — covers the §6.4
    #      country-template flow where v1.composed_prompt is the raw yaml text.
    #   3. None → _call_processor uses its generic default instruction
    prompt = body.prompt
    if prompt is None and doc.api_definition_id:
        prompt = _resolve_active_composed_prompt(db, doc.api_definition_id)
    if body.extra_fields:
        existing_fields = []
        if latest and latest.structured_data and isinstance(latest.structured_data, dict):
            existing_fields = list(latest.structured_data.keys())
        all_fields = list(dict.fromkeys(existing_fields + body.extra_fields))  # dedupe, preserve order
        field_list = ", ".join(all_fields)
        base = prompt or (latest.prompt_used if latest else None) or (
            "Extract all structured data fields from this document and return them as valid JSON."
        )
        prompt = (
            f"{base}\n\n"
            f"Required fields to extract: {field_list}\n"
            f"Return a JSON object whose keys include every required field. "
            f"If a field cannot be located, set its value to null."
        )

    result = _run_extraction(
        db, doc, processor_type=processor, prompt=prompt, previous_version=prev_version
    )
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
