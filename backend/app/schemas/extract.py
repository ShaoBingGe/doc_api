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
    data: dict = Field(
        default_factory=dict,
        description="首条票据（保持既有契约不变；多票据文档请改用 entities）",
    )
    entities: list[dict] = Field(
        default_factory=list,
        description=(
            "文档中提取到的**全部**票据。一份文档可能含多张独立票据"
            "（如整月发票扫成一个 PDF），data 只暴露第一张，此处为全量。"
        ),
    )
    metadata: ExtractMetadata


class ExtractErrorResponse(BaseModel):
    request_id: uuid.UUID
    error: "ErrorDetail"


# avoid circular import — inline here
class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None
