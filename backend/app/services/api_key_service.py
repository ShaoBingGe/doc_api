"""
ApiKeyService — API Key 生成、验证、轮换、吊销。

安全规则：
  - 明文密钥仅在 create/rotate 时返回，之后无法恢复
  - 数据库只存 SHA-256 哈希 (key_hash) 和显示前缀 (key_prefix)
  - 认证校验用 secrets.compare_digest 防止时序攻击
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.schemas.api_key import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    UpdateApiKeyRequest,
)


def _get_or_404(db: Session, key_id: uuid.UUID, user=None) -> ApiKey:
    key = db.get(ApiKey, key_id)
    if not key:
        raise NotFoundError(f"ApiKey {key_id} not found")
    if user is not None:
        from app.core.deps import assert_can_access
        assert_can_access(key, user)
    return key


def _to_response(key: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse.model_validate(key)


# ── Create ────────────────────────────────────────────────────────────────────

def create_api_key(
    db: Session,
    body: CreateApiKeyRequest,
    organization_id: uuid.UUID | None = None,
    user=None,
) -> CreateApiKeyResponse:
    from app.core.deps import owner_tenant_id

    raw_key, key_hash, key_prefix = generate_api_key()

    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    key = ApiKey(
        organization_id=organization_id,
        tenant_id=owner_tenant_id(user) if user is not None else None,
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=body.scopes,
        rate_limit=body.rate_limit,
        is_active=True,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    return CreateApiKeyResponse(
        id=key.id,
        name=key.name,
        key=raw_key,          # ← returned ONCE only
        key_prefix=key_prefix,
        scopes=key.scopes or [],
        rate_limit=key.rate_limit,
        expires_at=key.expires_at,
        created_at=key.created_at,
    )


# ── List ──────────────────────────────────────────────────────────────────────

def list_api_keys(db: Session, user=None) -> list[ApiKeyResponse]:
    from app.core.deps import scope_filter

    q = scope_filter(db.query(ApiKey), ApiKey, user)
    keys = q.order_by(ApiKey.created_at.desc()).all()
    return [_to_response(k) for k in keys]


# ── Update ────────────────────────────────────────────────────────────────────

def update_api_key(
    db: Session,
    key_id: uuid.UUID,
    body: UpdateApiKeyRequest,
    user=None,
) -> ApiKeyResponse:
    key = _get_or_404(db, key_id, user)
    if body.name is not None:
        key.name = body.name
    if body.scopes is not None:
        key.scopes = body.scopes
    if body.rate_limit is not None:
        key.rate_limit = body.rate_limit
    db.commit()
    db.refresh(key)
    return _to_response(key)


# ── Revoke ────────────────────────────────────────────────────────────────────

def revoke_api_key(db: Session, key_id: uuid.UUID, user=None) -> None:
    key = _get_or_404(db, key_id, user)
    key.is_active = False
    db.commit()


# ── Rotate ────────────────────────────────────────────────────────────────────

def rotate_api_key(db: Session, key_id: uuid.UUID, user=None) -> CreateApiKeyResponse:
    """
    Revoke the existing key and issue a new one with the same configuration.
    The new key's plaintext is returned once.
    """
    old_key = _get_or_404(db, key_id, user)
    old_key.is_active = False

    raw_key, key_hash, key_prefix = generate_api_key()
    new_key = ApiKey(
        organization_id=old_key.organization_id,
        tenant_id=old_key.tenant_id,
        name=old_key.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=old_key.scopes,
        rate_limit=old_key.rate_limit,
        is_active=True,
        expires_at=old_key.expires_at,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    return CreateApiKeyResponse(
        id=new_key.id,
        name=new_key.name,
        key=raw_key,
        key_prefix=key_prefix,
        scopes=new_key.scopes or [],
        rate_limit=new_key.rate_limit,
        expires_at=new_key.expires_at,
        created_at=new_key.created_at,
    )
