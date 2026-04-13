"""
API Definition management endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.api_definition import (
    ApiDefinitionResponse,
    ApiDocsResponse,
    ApiStatsResponse,
    CreateApiDefinitionRequest,
    UpdateApiDefinitionRequest,
    UpdateApiStatusRequest,
)
from app.schemas.common import PaginatedResponse
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
    db: Session = Depends(get_db),
) -> PaginatedResponse[ApiDefinitionResponse]:
    return svc.list_api_definitions(
        db, page=page, page_size=page_size, status_filter=status_filter, search=search
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
