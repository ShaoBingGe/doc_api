"""
Pydantic schemas for ApiKey management.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Requests ──────────────────────────────────────────────────────────────────

class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    scopes: list[str] = Field(default=["extract"])
    rate_limit: int = Field(default=180, ge=1, le=10000, description="每分钟最大调用次数（默认 180 ≈ 3 req/sec）")
    expires_in_days: int | None = Field(default=None, ge=1, description="None 表示永不过期")


class UpdateApiKeyRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    scopes: list[str] | None = None
    rate_limit: int | None = Field(default=None, ge=1, le=10000)


# ── Responses ─────────────────────────────────────────────────────────────────

class CreateApiKeyResponse(BaseModel):
    """创建密钥时的响应 — 包含完整明文 key，仅此一次可见。"""
    id: uuid.UUID
    name: str
    key: str = Field(description="⚠️ 完整 API Key，仅在创建时返回一次，请立即保存")
    key_prefix: str
    scopes: list[str]
    rate_limit: int
    expires_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiKeyResponse(BaseModel):
    """列表 / 详情响应 — 不含明文 key。"""
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str] | None = None
    rate_limit: int
    is_active: bool
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
