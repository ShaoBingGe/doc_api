"""
AnnotationService — 标注 CRUD、批量操作、修正率统计。

标注写入规则：
  - AI 识别后批量创建：source=ai_detected，is_corrected=False
  - 用户编辑：自动记录 original_value / original_bbox，is_corrected=True
  - 用户手动添加：source=manual
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.annotation import Annotation, AnnotationSource
from app.models.document import Document
from app.schemas.annotation import (
    AnnotationListResponse,
    AnnotationResponse,
    BatchAnnotationRequest,
    BatchUpdateRequest,
    CreateAnnotationRequest,
    UpdateAnnotationRequest,
)


def _get_or_404(db: Session, annotation_id: uuid.UUID, document_id: uuid.UUID) -> Annotation:
    ann = (
        db.query(Annotation)
        .filter(Annotation.id == annotation_id, Annotation.document_id == document_id)
        .first()
    )
    if not ann:
        raise NotFoundError(f"Annotation {annotation_id} not found on document {document_id}")
    return ann


def _doc_exists(db: Session, document_id: uuid.UUID) -> None:
    if not db.get(Document, document_id):
        raise NotFoundError(f"Document {document_id} not found")


def _to_response(ann: Annotation) -> AnnotationResponse:
    return AnnotationResponse.model_validate(ann)


def _coerce_value(raw, field_type: str | None):
    """Best-effort coerce a string field_value into its declared type so the
    JSON output panel renders numbers/bools natively, not as quoted strings."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    if field_type == "number":
        try:
            return int(raw) if "." not in raw else float(raw)
        except ValueError:
            return raw
    if field_type == "boolean":
        return raw.strip().lower() in ("true", "1", "yes", "y")
    return raw


def _upsert_field_in_latest_structured_data(
    db: Session,
    document_id: uuid.UUID,
    field_name: str,
    field_value,
    field_type: str | None = None,
) -> None:
    """Write a TOP-LEVEL field into the document's latest ProcessingResult
    structured_data so the workspace field view + JSON output panel reflect
    a manually-added/answered field immediately.

    Why this matters: the frontend rebuilds the field list from
    `latest_result.structured_data` (NOT the Annotation table). Creating an
    Annotation row alone is invisible to that view — the field would keep
    showing as "missing / 待补充" with an empty input after a successful save.
    Upserting the key here keeps the Annotation row (GT) and the displayed
    structured_data in agreement.

    Top-level only, mirroring the parity/pad contract. Flattened labels
    ("foo[0].bar") are skipped — array-cell answers go through a different
    path. Idempotent on the (name, value) pair.
    """
    from sqlalchemy import desc
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.document import ProcessingResult

    if not field_name or "[" in field_name or "." in field_name:
        return

    pr = (
        db.query(ProcessingResult)
        .filter(ProcessingResult.document_id == document_id)
        .order_by(desc(ProcessingResult.version))
        .first()
    )
    if pr is None or pr.structured_data is None:
        return

    value = _coerce_value(field_value, field_type)
    sd = pr.structured_data

    if isinstance(sd, list):
        new_sd = []
        touched = False
        for rec in sd:
            if isinstance(rec, dict):
                rec = dict(rec)
                rec[field_name] = value
                touched = True
            new_sd.append(rec)
        if not touched:
            # structured_data was an empty list → seed one record
            new_sd = [{field_name: value}]
        pr.structured_data = new_sd
    elif isinstance(sd, dict):
        new_sd = dict(sd)
        new_sd[field_name] = value
        pr.structured_data = new_sd
    else:
        return

    flag_modified(pr, "structured_data")
    db.commit()


# ── Create ────────────────────────────────────────────────────────────────────

def create_annotation(
    db: Session,
    document_id: uuid.UUID,
    body: CreateAnnotationRequest,
    created_by: uuid.UUID | None = None,
) -> AnnotationResponse:
    _doc_exists(db, document_id)
    ann = Annotation(
        document_id=document_id,
        processing_result_id=body.processing_result_id,
        field_name=body.field_name,
        field_value=body.field_value,
        field_type=body.field_type,
        bounding_box=body.bounding_box.model_dump() if body.bounding_box else None,
        source=body.source,
        confidence=body.confidence,
        is_corrected=False,
        created_by=created_by,
    )
    db.add(ann)
    db.commit()
    db.refresh(ann)

    # Mirror manual-added field into ApiDef overlay (design v8) so it
    # appears in every other sample's "添加" section.
    if body.source == AnnotationSource.manual:
        try:
            from app.services import pending_edits_service
            doc = db.get(Document, document_id)
            api_def_id = doc.api_definition_id if doc else None
            if api_def_id:
                pending_edits_service.record_added_field(
                    db,
                    api_def_id,
                    field_name=body.field_name,
                    field_type=body.field_type or "string",
                    description="",
                    added_at_doc_id=document_id,
                    default_value=body.field_value,
                )
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "pending_edits add mirror failed for ann=%s: %s", ann.id, exc,
            )
        # Reflect the answer into this doc's latest structured_data so the
        # field view + JSON panel show it (and it leaves the "待补充"/missing
        # section). Without this the save looks like a no-op: the input
        # clears but the field reappears empty. Best-effort, never blocks.
        try:
            _upsert_field_in_latest_structured_data(
                db, document_id, body.field_name, body.field_value,
                body.field_type,
            )
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "structured_data upsert failed for ann=%s: %s", ann.id, exc,
            )

    return _to_response(ann)


# ── Batch Create ──────────────────────────────────────────────────────────────

def batch_create_annotations(
    db: Session,
    document_id: uuid.UUID,
    body: BatchAnnotationRequest,
    created_by: uuid.UUID | None = None,
) -> list[AnnotationResponse]:
    _doc_exists(db, document_id)
    new_annotations: list[Annotation] = []
    for item in body.annotations:
        ann = Annotation(
            document_id=document_id,
            processing_result_id=body.processing_result_id,
            field_name=item.field_name,
            field_value=item.field_value,
            field_type=item.field_type,
            bounding_box=item.bounding_box.model_dump() if item.bounding_box else None,
            source=item.source,
            confidence=item.confidence,
            is_corrected=False,
            created_by=created_by,
        )
        new_annotations.append(ann)
    db.add_all(new_annotations)
    db.commit()
    for ann in new_annotations:
        db.refresh(ann)
    return [_to_response(a) for a in new_annotations]


# ── List ──────────────────────────────────────────────────────────────────────

def list_annotations(
    db: Session,
    document_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> AnnotationListResponse:
    _doc_exists(db, document_id)
    base_q = db.query(Annotation).filter(Annotation.document_id == document_id)
    total = base_q.count()
    corrected_count = base_q.filter(Annotation.is_corrected.is_(True)).count()
    correction_rate = round(corrected_count / total, 4) if total > 0 else 0.0

    annotations = (
        base_q.order_by(Annotation.created_at)
        .offset(skip)
        .limit(limit)
        .all()
    )

    return AnnotationListResponse(
        annotations=[_to_response(a) for a in annotations],
        document_id=document_id,
        total=total,
        skip=skip,
        limit=limit,
        correction_rate=correction_rate,
    )


# ── Update ────────────────────────────────────────────────────────────────────

def update_annotation(
    db: Session,
    document_id: uuid.UUID,
    annotation_id: uuid.UUID,
    body: UpdateAnnotationRequest,
) -> AnnotationResponse:
    ann = _get_or_404(db, annotation_id, document_id)

    # Track corrections: record originals before overwriting
    changed = False
    old_name = ann.field_name
    rename_to: str | None = None
    value_modified = False

    if body.field_value is not None and body.field_value != ann.field_value:
        if not ann.is_corrected:
            ann.original_value = ann.field_value
        ann.field_value = body.field_value
        ann.is_corrected = True
        changed = True
        value_modified = True

    if body.field_name is not None and body.field_name != ann.field_name:
        rename_to = body.field_name
        ann.field_name = body.field_name
        ann.is_corrected = True
        changed = True

    if body.field_type is not None:
        ann.field_type = body.field_type

    if body.bounding_box is not None:
        new_bbox = body.bounding_box.model_dump()
        if new_bbox != ann.bounding_box:
            if not ann.is_corrected:
                ann.original_bbox = ann.bounding_box
            ann.bounding_box = new_bbox
            ann.is_corrected = True
            changed = True

    if changed and ann.source == AnnotationSource.ai_detected:
        # Mark that this AI annotation was human-corrected
        pass  # is_corrected already set above

    db.commit()
    db.refresh(ann)

    # Mirror this edit into ApiDefinition.pending_edits (design v8 overlay)
    # so other samples of the same ApiDef can render the union of edits.
    # Rename also cascades to every other Annotation row with the same old name.
    if rename_to or value_modified:
        try:
            from app.services import pending_edits_service
            doc = db.get(Document, document_id)
            api_def_id = doc.api_definition_id if doc else None
            if api_def_id:
                if rename_to:
                    pending_edits_service.record_rename(db, api_def_id, old_name, rename_to)
                    pending_edits_service.cascade_rename_annotations(
                        db, api_def_id, old_name, rename_to,
                    )
                if value_modified:
                    pending_edits_service.record_modification(
                        db, api_def_id, document_id, ann.field_name, ann.field_value,
                    )
        except Exception as exc:  # noqa: BLE001
            # Overlay mirroring must never block the primary edit.
            import logging
            logging.getLogger(__name__).warning(
                "pending_edits mirror failed for ann=%s: %s", annotation_id, exc,
            )

    return _to_response(ann)


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_annotation(
    db: Session,
    document_id: uuid.UUID,
    annotation_id: uuid.UUID,
) -> None:
    ann = _get_or_404(db, annotation_id, document_id)
    db.delete(ann)
    db.commit()


# ── Batch Update ──────────────────────────────────────────────────────────────

def batch_update(
    db: Session,
    document_id: uuid.UUID,
    body: BatchUpdateRequest,
) -> list[AnnotationResponse]:
    _doc_exists(db, document_id)
    results: list[AnnotationResponse] = []
    overlay_ops: list[tuple[str, tuple]] = []  # (kind, args) collected pre-commit
    for item in body.updates:
        ann = _get_or_404(db, item.annotation_id, document_id)
        old_name = ann.field_name

        if item.field_value is not None and item.field_value != ann.field_value:
            if not ann.is_corrected:
                ann.original_value = ann.field_value
            ann.field_value = item.field_value
            ann.is_corrected = True
            overlay_ops.append(("modify", (item.field_name or old_name, item.field_value)))

        if item.field_name is not None and item.field_name != ann.field_name:
            ann.field_name = item.field_name
            ann.is_corrected = True
            overlay_ops.append(("rename", (old_name, item.field_name)))

        if item.field_type is not None:
            ann.field_type = item.field_type

        if item.bounding_box is not None:
            new_bbox = item.bounding_box.model_dump()
            if new_bbox != ann.bounding_box:
                if not ann.is_corrected:
                    ann.original_bbox = ann.bounding_box
                ann.bounding_box = new_bbox
                ann.is_corrected = True

        results.append(ann)

    db.commit()
    for ann in results:
        db.refresh(ann)

    # Mirror to pending_edits overlay (best-effort; never blocks the primary write)
    if overlay_ops:
        try:
            from app.services import pending_edits_service
            doc = db.get(Document, document_id)
            api_def_id = doc.api_definition_id if doc else None
            if api_def_id:
                for kind, args in overlay_ops:
                    if kind == "rename":
                        old_n, new_n = args
                        pending_edits_service.record_rename(db, api_def_id, old_n, new_n)
                        pending_edits_service.cascade_rename_annotations(db, api_def_id, old_n, new_n)
                    elif kind == "modify":
                        fname, fval = args
                        pending_edits_service.record_modification(db, api_def_id, document_id, fname, fval)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "pending_edits batch mirror failed for doc=%s: %s", document_id, exc,
            )

    return [_to_response(a) for a in results]
