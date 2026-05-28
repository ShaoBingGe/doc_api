"""
API Definition management endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.ocr_optimizer.service import customer_iteration
from app.schemas.api_definition import (
    ApiDefinitionResponse,
    ApiDocsResponse,
    ApiStatsResponse,
    CreateApiDefinitionRequest,
    UpdateApiDefinitionRequest,
    UpdateApiStatusRequest,
)
from app.schemas.common import PaginatedResponse
from app.schemas.document import DocumentResponse, DocumentUploadResponse
from app.services import api_definition_service as svc

router = APIRouter(prefix="/api-definitions", tags=["API Definitions"])


@router.post(
    "",
    response_model=ApiDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建 API 定义",
)
def create_api_definition(
    body: CreateApiDefinitionRequest,
    db: Session = Depends(get_db),
) -> ApiDefinitionResponse:
    return svc.create_api_definition(db, body)


@router.get(
    "",
    response_model=PaginatedResponse[ApiDefinitionResponse],
    summary="API 定义列表",
)
def list_api_definitions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    include_pending: bool = Query(
        default=False,
        description="是否包含 pending_first_doc 占位 API（默认 false；§6.4）",
    ),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ApiDefinitionResponse]:
    return svc.list_api_definitions(
        db,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
        include_pending=include_pending,
    )


@router.get(
    "/{api_def_id}",
    response_model=ApiDefinitionResponse,
    summary="API 定义详情",
)
def get_api_definition(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ApiDefinitionResponse:
    return svc.get_api_definition(db, api_def_id)


@router.put(
    "/{api_def_id}",
    response_model=ApiDefinitionResponse,
    summary="更新 API 定义",
)
def update_api_definition(
    api_def_id: uuid.UUID,
    body: UpdateApiDefinitionRequest,
    db: Session = Depends(get_db),
) -> ApiDefinitionResponse:
    return svc.update_api_definition(db, api_def_id, body)


@router.patch(
    "/{api_def_id}/status",
    response_model=ApiDefinitionResponse,
    summary="更改 API 状态（activate / deprecate）",
)
def update_api_status(
    api_def_id: uuid.UUID,
    body: UpdateApiStatusRequest,
    db: Session = Depends(get_db),
) -> ApiDefinitionResponse:
    return svc.update_api_status(db, api_def_id, body)


@router.get(
    "/{api_def_id}/versions",
    summary="Prompt 版本历史",
    response_model=list[dict],
)
def get_versions(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[dict]:
    # TODO: implement when PromptVersion model is added
    svc.get_api_definition(db, api_def_id)  # 404 guard
    return []


@router.get(
    "/{api_def_id}/docs",
    response_model=ApiDocsResponse,
    summary="自动生成的调用文档",
)
def get_api_docs(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ApiDocsResponse:
    return svc.get_api_docs(db, api_def_id)


@router.get(
    "/{api_def_id}/stats",
    response_model=ApiStatsResponse,
    summary="API 调用统计",
)
def get_stats(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ApiStatsResponse:
    return svc.get_stats(db, api_def_id)


@router.delete(
    "/{api_def_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除 API 定义",
)
def delete_api_definition(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    svc.delete_api_definition(db, api_def_id)


# ── Sample documents (batch optimization sample set) ─────────────────────────

@router.get(
    "/{api_def_id}/documents",
    response_model=list[DocumentResponse],
    summary="列出该 API 的样本文档（优化器 GT 来源）",
)
def list_sample_documents(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[DocumentResponse]:
    docs = svc.list_sample_documents(db, api_def_id)
    return [DocumentResponse.model_validate(d) for d in docs]


@router.post(
    "/{api_def_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传一个样本文档并追加到该 API 的样本集",
)
async def add_sample_document(
    api_def_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DocumentUploadResponse:
    file_data = await file.read()
    doc = svc.add_sample_document(
        db,
        api_def_id=api_def_id,
        filename=file.filename or "upload",
        file_data=file_data,
        content_type=file.content_type,
    )
    return DocumentUploadResponse.model_validate(doc)


@router.delete(
    "/{api_def_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="从样本集移除文档（不删除 Document 本体）",
)
def remove_sample_document(
    api_def_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    svc.remove_sample_document(
        db, api_def_id=api_def_id, document_id=document_id
    )


# ── Customer-driven customization (reflection + 3-round + fork) ──────────────


@router.post(
    "/{api_def_id}/customize",
    summary="保存客户字段修改 → 反思 + 3 轮迭代 + fork 出新 api_code（异步）",
)
def customize_api_definition(
    api_def_id: uuid.UUID,
    background: BackgroundTasks,
    body: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """Request body: {"diffs": [<FieldDiff>, ...]}

    FieldDiff shape:
      {
        "kind": "edit" | "add",
        "module_key": str | null,                # null for "add"
        "original_name": str | null,
        "corrected_name": str,
        "original_value": Any,
        "corrected_value": Any,
        "original_format": str | null,           # "string" / "number" / ...
        "corrected_format": str | null
      }
    """
    diffs = body.get("diffs") or []
    if not isinstance(diffs, list) or not diffs:
        raise HTTPException(status_code=400, detail="diffs must be a non-empty list")
    # Validate quickly
    for i, d in enumerate(diffs):
        if not isinstance(d, dict) or d.get("kind") not in ("edit", "add"):
            raise HTTPException(status_code=400, detail=f"diff[{i}] has invalid 'kind'")

    job = customer_iteration.submit_customize_job(
        source_api_definition_id=api_def_id,
        diffs=diffs,
    )
    background.add_task(customer_iteration.run_customize_job, job.id)
    return {
        "job_id": job.id,
        "status": job.status,
        "source_api_definition_id": str(api_def_id),
    }


@router.get(
    "/customize-jobs/{job_id}",
    summary="查询客户定制 job 进度",
)
def get_customize_job(job_id: str) -> dict:
    job = customer_iteration.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "phase_detail": job.phase_detail,
        "rounds_done": job.rounds_done,
        "rounds_total": job.rounds_total,
        "overall_accuracy": job.overall_accuracy,
        "new_api_definition_id": job.new_api_definition_id,
        "new_api_code": job.new_api_code,
        "error_message": job.error_message,
        "reflection_summary": job.reflection_summary,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
