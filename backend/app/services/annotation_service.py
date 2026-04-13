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

    if body.field_value is not None and body.field_value != ann.field_value:
        if not ann.is_corrected:
            ann.original_value = ann.field_value
        ann.field_value = body.field_value
        ann.is_corrected = True
        changed = True

    if body.field_name is not None and body.field_name != ann.field_name:
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
    for item in body.updates:
        ann = _get_or_404(db, item.annotation_id, document_id)

        if item.field_value is not None and item.field_value != ann.field_value:
            if not ann.is_corrected:
                ann.original_value = ann.field_value
            ann.field_value = item.field_value
            ann.is_corrected = True

        if item.field_name is not None and item.field_name != ann.field_name:
            ann.field_name = item.field_name
            ann.is_corrected = True

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
    return [_to_response(a) for a in results]
