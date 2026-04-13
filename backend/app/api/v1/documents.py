"""
Document management endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.document import (
    DocumentDetail,
    DocumentResponse,
    DocumentUploadResponse,
    HighlightsResponse,
    ProcessingResultResponse,
    RegionOcrRequest,
    RegionOcrResponse,
    ReprocessRequest,
)
from app.services import document_service as svc

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传文档",
)
async def upload_document(
    file: UploadFile = File(...),
    template_id: uuid.UUID | None = Form(default=None),
    processor_type: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> DocumentUploadResponse:
    file_data = await file.read()
    doc = svc.upload_document(
        db,
        filename=file.filename or "upload",
        file_data=file_data,
        content_type=file.content_type,
        processor_type=processor_type,
        template_id=template_id,
    )
    return DocumentUploadResponse.model_validate(doc)


@router.get(
    "",
    response_model=PaginatedResponse[DocumentResponse],
    summary="文档列表",
)
def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    file_type: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[DocumentResponse]:
    return svc.list_documents(
        db,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        file_type=file_type,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    summary="文档详情（含最新处理结果）",
)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DocumentDetail:
    return svc.get_document_detail(db, document_id)


@router.get(
    "/{document_id}/preview",
    summary="获取文档预览 URL",
)
def get_preview_url(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    url = svc.get_preview_url(db, document_id)
    return {"preview_url": url}


@router.get(
    "/{document_id}/results",
    response_model=list[ProcessingResultResponse],
    summary="处理结果列表（多版本）",
)
def get_processing_results(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[ProcessingResultResponse]:
    return svc.get_processing_results(db, document_id)


@router.post(
    "/{document_id}/reprocess",
    response_model=ProcessingResultResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新处理文档",
)
def reprocess_document(
    document_id: uuid.UUID,
    body: ReprocessRequest,
    db: Session = Depends(get_db),
) -> ProcessingResultResponse:
    return svc.reprocess_document(db, document_id, body)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除文档",
)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    svc.delete_document(db, document_id)


@router.post(
    "/{document_id}/region-ocr",
    response_model=RegionOcrResponse,
    summary="框选区域专项 OCR",
)
def region_ocr(
    document_id: uuid.UUID,
    body: RegionOcrRequest,
    db: Session = Depends(get_db),
) -> RegionOcrResponse:
    # TODO: implement in engine layer (T4 phase)
    # Returns stub response so the endpoint is callable
    svc.get_document(db, document_id)  # 404 guard
    return RegionOcrResponse(ocr_text="", auto_research_rounds=0)


@router.get(
    "/{document_id}/highlights",
    response_model=HighlightsResponse,
    summary="字段→文档区域高亮映射",
)
def get_highlights(
    document_id: uuid.UUID,
    result_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HighlightsResponse:
    return svc.get_highlights(db, document_id, result_id)
