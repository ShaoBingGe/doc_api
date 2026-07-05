"""
DocumentService — 文档上传、存储、处理调度、查询、删除。

职责边界：
  - 文件 I/O 委托给 StorageBackend（LocalStorage 原型）
  - AI 提取委托给 ProcessorFactory（engine 层）
  - 不直接依赖 API 路由层
"""

from __future__ import annotations

import logging
import math
import uuid
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

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
    user=None,
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

    from app.core.deps import owner_tenant_id

    file_type = _validate_file(filename, len(file_data), content_type)
    storage_path = _save_upload(file_data, filename)

    doc = Document(
        user_id=user_id,
        tenant_id=owner_tenant_id(user) if user is not None else None,
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
    user=None,
) -> Document:
    """
    Persist the uploaded file and create a Document record.

    OCR is NOT triggered here. When the upload is part of the §6.4 country-template
    flow, the caller (documents.upload_document endpoint) follows up with
    `bind_to_api_and_extract` which runs OCR using the active OcrPromptVersion's
    composed_prompt. Otherwise the document stays in `queued`.
    """
    from app.core.deps import owner_tenant_id

    file_type = _validate_file(filename, len(file_data), content_type)
    storage_path = _save_upload(file_data, filename)

    doc = Document(
        user_id=user_id,
        tenant_id=owner_tenant_id(user) if user is not None else None,
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
    # OcrPromptVersion when body.prompt is None.
    #
    # Errors here (most often Gemini SSL/connectivity failures) must NOT
    # bubble up as a 500 — the file was successfully uploaded and bound to
    # the API. We mark doc.status=failed via _run_extraction's own except
    # clause; the customer can re-run OCR later (e.g. when the proxy is back).
    body = ReprocessRequest(prompt=None)
    try:
        reprocess_document(db, doc.id, body)
    except Exception as exc:
        logger.exception(
            "OCR failed for newly-uploaded sample doc=%s api=%s — keeping upload, marking doc failed",
            doc.id, api_definition_id,
        )
        # _run_extraction has already set doc.status=failed; refresh state
        db.rollback()
        # Reload doc + reapply the config change we may have committed above
        db.refresh(api_def)
        cfg2 = dict(api_def.config or {})
        ids2 = list(cfg2.get("sample_document_ids") or [])
        if str(doc.id) not in ids2:
            ids2.append(str(doc.id))
            cfg2["sample_document_ids"] = ids2
            api_def.config = cfg2
            flag_modified(api_def, "config")
        # Mark the doc record itself as failed so the UI reflects status
        db.refresh(doc)
        from app.models.document import DocumentStatus
        doc.status = DocumentStatus.failed
        doc.error_message = (f"OCR failed: {exc}")[:1024]
        db.commit()

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
        # When the doc is bound to an API definition, reconcile the LLM output
        # against that API's contract field set:
        #   1. PROJECT — drop fields the model free-formed that are NOT in the
        #      contract. The VLM isn't hard-bound to the schema, so a pruned
        #      API (e.g. JP 8-field) otherwise still surfaces the model's full
        #      canonical invoice (billFrom/lineItems/bankAccount/… → 97 leaves).
        #      Projecting makes "标准识别" honor the defined schema.
        #   2. PAD — N4: add null for any contract field the model omitted, so
        #      every sample exposes the SAME top-level key set (cross-sample
        #      parity) and downstream consumers don't crash on missing keys.
        # `required` = modules + user-added + confirmed-observed − deleted, so
        # projection only removes brand-new free-formed junk and never shrinks
        # the legitimately-confirmed field set.
        if doc.api_definition_id:
            try:
                from app.services import pending_edits_service
                required = pending_edits_service.compute_required_field_set(
                    db, doc.api_definition_id,
                )
                structured_data = _project_to_field_set(structured_data, required)
                structured_data = _pad_with_required_keys(structured_data, required)
            except Exception as _exc:  # noqa: BLE001
                logger.warning("Failed to reconcile structured_data with required keys: %s", _exc)
            # Customer per-field overrides (type / strip-chars): apply the
            # stripping + type coercion deterministically to the extracted
            # values, so the override "takes effect" on the result regardless
            # of what the VLM emitted (mirrors the prompt-side enforcement).
            try:
                from app.ocr_optimizer.service import field_constraints
                _constraints = field_constraints.load(db, doc.api_definition_id)
                if _constraints:
                    structured_data = _apply_field_constraints(structured_data, _constraints)
            except Exception as _exc:  # noqa: BLE001
                logger.warning("Failed to apply field constraints to structured_data: %s", _exc)
        inferred_schema = _infer_schema(raw_structured)

        # 字段焦点定位（第一步）：从原生 PDF 文字层拿词坐标，把抽取出的值
        # 匹配回票面位置，给无 bbox 的字段补真实坐标（匹配不到留空，前端降级）。
        # 纯本地、确定性、不调 LLM、不改 prompt；扫描件/图片词表为空即跳过。
        try:
            from app.services import word_locator
            words = word_locator.extract_words(doc.storage_path)
            if words:
                located, total = word_locator.locate_fields(structured_data, words)
                logger.info(
                    "word-locator: doc=%s 定位 %d/%d 字段", doc.id, located, total
                )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("word-locator failed for doc=%s: %s", doc.id, _exc)

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
    # processors 消费的键是 "response_schema"（gemini 借此硬约束输出形状）。
    # 历史键名 "schema" 没有任何 processor 读取，被 extra-ignore 静默吞掉——
    # 上传识别一直没有 schema 硬约束（结构审查 F12 死参数）。
    runtime_config = {"response_schema": schema} if schema else None

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


# A2：OCR 输出后处理纯函数下沉 app/domain/extraction_pipeline.py（可独立单测、
# 消除引擎侧 doc_sync 对本模块的部分反向依赖）。这里按下划线私有名 re-import
# 别名——内部调用与既有测试（从 document_service 导入这些函数）全不变。
from app.domain.extraction_pipeline import (  # noqa: E402
    field_top_level as _field_top_level,
    flatten_hierarchical as _flatten_hierarchical,
    is_leaf_field as _is_leaf_field,
    normalize_bbox as _normalize_bbox,
    normalize_structured_data as _normalize_structured_data,
    pad_with_required_keys as _pad_with_required_keys,
    project_to_field_set as _project_to_field_set,
    rewrite_structured_data_keys as _rewrite_structured_data_keys,
)


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


def _apply_field_constraints(
    structured_data: list[dict] | dict, constraints: dict[str, dict],
) -> list[dict] | dict:
    """Apply customer per-field type/strip overrides to extracted values.

    Operates on both shapes:
      - leaf-list [{keyName, value, …}] → coerce records whose top-level token
        is a constrained field;
      - record-dict {field: value} → coerce constrained keys.
    """
    if not constraints:
        return structured_data
    from app.ocr_optimizer.service import field_constraints as _fc

    def _fix_leaf(rec: dict) -> dict:
        c = constraints.get(_field_top_level(rec.get("keyName", "")))
        if c is None:
            return rec
        out = dict(rec)
        out["value"] = _fc.normalize_value(rec.get("value"), c)
        return out

    if isinstance(structured_data, list):
        if structured_data and isinstance(structured_data[0], dict) and "keyName" in structured_data[0]:
            return [_fix_leaf(r) if isinstance(r, dict) else r for r in structured_data]
        return [
            _fc.normalize_record_fields(r, constraints) if isinstance(r, dict) else r
            for r in structured_data
        ]
    if isinstance(structured_data, dict):
        return _fc.normalize_record_fields(structured_data, constraints)
    return structured_data


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

def get_document(db: Session, document_id: uuid.UUID, user=None) -> Document:
    doc = db.get(Document, document_id)
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")
    if user is not None:
        from app.core.deps import assert_can_access
        assert_can_access(doc, user)
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
    user=None,
) -> PaginatedResponse[DocumentResponse]:
    from app.core.deps import scope_filter

    q = db.query(Document)
    q = scope_filter(q, Document, user)
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


def get_document_detail(db: Session, document_id: uuid.UUID, user=None) -> DocumentDetail:
    doc = get_document(db, document_id, user)
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

    Design v8: if the ApiDef has a non-empty pending_edits overlay
    (added_fields / renames), augment the prompt with a "PENDING EDITS"
    appendix so newly-uploaded samples are OCR'd with the user's in-flight
    field set + rename mapping. The overlay is NOT written back to DB —
    each call recomputes it from the current overlay state.
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
    if not v:
        return None
    base = v.composed_prompt or ""
    return _augment_with_overlay(db, api_def_id, base)


def _augment_with_overlay(db: Session, api_def_id: uuid.UUID, base_prompt: str) -> str:
    """Append a 'PENDING EDITS' appendix to base_prompt describing the
    user's in-flight added fields + rename mappings, so the LLM extracts
    the same union field set across all samples of this ApiDef.

    See backend/app/services/pending_edits_service.py for overlay shape and
    docs/prompt-system.md §11 for the cross-sample workspace invariants.
    """
    try:
        from app.services import pending_edits_service
        overlay = pending_edits_service.get_overlay(db, api_def_id)
    except Exception:
        return base_prompt

    added = overlay.get("added_fields") or []
    renames = overlay.get("renames") or {}

    # N4 — include the FULL required-field set so the LLM tries to extract
    # every field the customer cares about, not just the overlay additions.
    # The base prompt's schema reference already lists the canonical fields,
    # but pending renames/adds can shift the expected output keys; spelling
    # them out explicitly closes the loop on cross-doc parity.
    try:
        required = pending_edits_service.compute_required_field_set(db, api_def_id)
    except Exception:  # noqa: BLE001
        required = []

    if not added and not renames and not required:
        return base_prompt

    blocks: list[str] = ["", "# 跨样本 Pending Edits 补充（design v8 overlay）",
                         "用户在其他样本上已进行以下编辑。本次识别请同样适用，找不到的字段输出 null。"]

    if renames:
        blocks.append("")
        blocks.append("## 字段重命名映射（{旧字段命名} → {新字段命名}）")
        blocks.append("识别到下列字段时，请在输出 JSON 中**使用新字段命名**作为 key（不要保留旧 key，也不要同时输出两者）：")
        for old, new in renames.items():
            blocks.append(f"- 在票面看到通常对应 `{old}` 的内容 → 输出到 JSON key `{new}`")

    if added:
        blocks.append("")
        blocks.append("## 用户在其他样本中新增的字段")
        blocks.append("请尝试从本样本中识别下列字段；找不到则该字段输出 null（不要省略 key）：")
        for f in added:
            name = f.get("field_name") or ""
            ftype = f.get("type") or "string"
            desc = (f.get("description") or "").strip()
            if desc:
                blocks.append(f"- `{name}` (type: {ftype}) — {desc}")
            else:
                blocks.append(f"- `{name}` (type: {ftype})")

    if required:
        blocks.append("")
        blocks.append("## 本 ApiDef 必填字段集（跨样本一致性约束）")
        blocks.append(
            "下列字段构成所有样本必须保持一致的字段集合。**每一个 key 都必须出现在输出 JSON 中**——"
            "在票面找到对应内容就填实际值，找不到则填 null。不允许省略 key，也不允许新增本列表之外的 key："
        )
        # Wrap long lists 8-per-line for prompt readability
        for i in range(0, len(required), 8):
            blocks.append("- " + " · ".join(f"`{f}`" for f in required[i:i+8]))

    return base_prompt.rstrip() + "\n" + "\n".join(blocks) + "\n"


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
