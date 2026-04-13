"""
Annotation CRUD endpoints (nested under /documents/{document_id}/annotations).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.annotation import (
    AnnotationListResponse,
    AnnotationResponse,
    BatchAnnotationRequest,
    BatchUpdateRequest,
    CreateAnnotationRequest,
    UpdateAnnotationRequest,
)
from app.services import annotation_service as svc

router = APIRouter(
    prefix="/documents/{document_id}/annotations",
    tags=["Annotations"],
)


@router.post(
    "",
    response_model=AnnotationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新增标注",
)
def create_annotation(
    document_id: uuid.UUID,
    body: CreateAnnotationRequest,
    db: Session = Depends(get_db),
) -> AnnotationResponse:
    return svc.create_annotation(db, document_id, body)


@router.get(
    "",
    response_model=AnnotationListResponse,
    summary="获取文档所有标注",
)
def list_annotations(
    document_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> AnnotationListResponse:
    return svc.list_annotations(db, document_id, skip=skip, limit=limit)


@router.post(
    "/batch",
    response_model=list[AnnotationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="批量创建标注",
)
def batch_create_annotations(
    document_id: uuid.UUID,
    body: BatchAnnotationRequest,
    db: Session = Depends(get_db),
) -> list[AnnotationResponse]:
    return svc.batch_create_annotations(db, document_id, body)


@router.patch(
    "/batch",
    response_model=list[AnnotationResponse],
    summary="批量更新标注（自动记录修正历史）",
)
def batch_update(
    document_id: uuid.UUID,
    body: BatchUpdateRequest,
    db: Session = Depends(get_db),
) -> list[AnnotationResponse]:
    return svc.batch_update(db, document_id, body)


@router.patch(
    "/{annotation_id}",
    response_model=AnnotationResponse,
    summary="更新标注（自动记录修正历史）",
)
def update_annotation(
    document_id: uuid.UUID,
    annotation_id: uuid.UUID,
    body: UpdateAnnotationRequest,
    db: Session = Depends(get_db),
) -> AnnotationResponse:
    return svc.update_annotation(db, document_id, annotation_id, body)


@router.delete(
    "/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除标注",
)
def delete_annotation(
    document_id: uuid.UUID,
    annotation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    svc.delete_annotation(db, document_id, annotation_id)
