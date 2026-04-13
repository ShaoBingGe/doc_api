"""
Pydantic schemas for the public extraction API (/api/v1/extract/:api_code).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ExtractJsonRequest(BaseModel):
    """JSON body alternative to multipart upload."""
    file_url: str | None = Field(default=None, description="可公开访问的文件 URL")
    file_base64: str | None = Field(
        default=None,
        description="Base64 编码文件，格式：data:<mime>;base64,<data>",
    )


class ExtractMetadata(BaseModel):
    processor: str
    model: str
    tokens_used: int
    processing_time_ms: int
    confidence: float | None = None


class ExtractResponse(BaseModel):
    request_id: uuid.UUID
    api_code: str
    api_version: int
    data: dict
    metadata: ExtractMetadata


class ExtractErrorResponse(BaseModel):
    request_id: uuid.UUID
    error: "ErrorDetail"


# avoid circular import — inline here
class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None
