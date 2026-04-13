"""
API Key management endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.api_key import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    UpdateApiKeyRequest,
)
from app.services import api_key_service as svc

router = APIRouter(prefix="/api-keys", tags=["API Keys"])


@router.post(
    "",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建 API Key（明文仅此一次）",
)
def create_api_key(
    body: CreateApiKeyRequest,
    db: Session = Depends(get_db),
) -> CreateApiKeyResponse:
    return svc.create_api_key(db, body)


@router.get(
    "",
    response_model=list[ApiKeyResponse],
    summary="密钥列表（不含明文）",
)
def list_api_keys(
    db: Session = Depends(get_db),
) -> list[ApiKeyResponse]:
    return svc.list_api_keys(db)


@router.put(
    "/{key_id}",
    response_model=ApiKeyResponse,
    summary="更新密钥",
)
def update_api_key(
    key_id: uuid.UUID,
    body: UpdateApiKeyRequest,
    db: Session = Depends(get_db),
) -> ApiKeyResponse:
    return svc.update_api_key(db, key_id, body)


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="吊销密钥",
)
def revoke_api_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    svc.revoke_api_key(db, key_id)


@router.post(
    "/{key_id}/rotate",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="轮换密钥（旧 Key 立即失效，新明文仅此一次）",
)
def rotate_api_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> CreateApiKeyResponse:
    return svc.rotate_api_key(db, key_id)
