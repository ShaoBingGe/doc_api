"""
Pydantic schemas for ApiDefinition management.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Requests ──────────────────────────────────────────────────────────────────

class CreateApiDefinitionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str = ""
    api_code: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$",
        description="URL-safe 唯一编码，如 inv-cn-vat-v1",
    )
    conversation_id: uuid.UUID | None = Field(
        default=None, description="从哪个对话创建（可选）"
    )
    response_schema: dict | None = Field(
        default=None, description="JSON Schema；不传则从 conversation 继承"
    )
    processor_type: str = "gemini"
    model_name: str = "gemini-2.5-flash"
    config: dict | None = None
    sample_document_id: uuid.UUID | None = Field(
        default=None, description="关联的样本文档 ID（存入 config JSON）"
    )


class UpdateApiDefinitionRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    response_schema: dict | None = None
    processor_type: str | None = None
    model_name: str | None = None
    config: dict | None = None


class UpdateApiStatusRequest(BaseModel):
    action: str = Field(..., description="activate | deprecate")


# ── Responses ─────────────────────────────────────────────────────────────────

class ApiStatsResponse(BaseModel):
    total_calls: int = 0
    calls_today: int = 0
    calls_this_month: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    error_count: int = 0


class ApiDefinitionResponse(BaseModel):
    id: uuid.UUID
    name: str
    api_code: str
    description: str
    status: str
    version: int
    response_schema: dict | None = None
    processor_type: str
    model_name: str
    config: dict | None = None
    endpoint_url: str = Field(default="", description="完整调用 URL，由服务器拼接")
    sample_document_id: str | None = Field(default=None, description="关联样本文档 ID")
    source_type: str = Field(default="custom", description="custom | template")
    stats: ApiStatsResponse | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiDocsResponse(BaseModel):
    """Auto-generated usage docs for an API definition."""
    api_code: str
    name: str
    description: str
    version: int
    endpoint: str
    method: str = "POST"
    authentication: str = "X-API-Key header"
    request_formats: list[str] = ["multipart/form-data", "application/json (file_url / file_base64)"]
    response_schema: dict | None = None
    error_codes: list[dict] = []
