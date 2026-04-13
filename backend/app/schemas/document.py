"""
Pydantic schemas for Document and ProcessingResult.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── ProcessingResult ──────────────────────────────────────────────────────────

class ProcessingResultResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    processor_type: str
    model_name: str
    prompt_used: str | None = None
    source: str = "initial"
    structured_data: list | None = None
    inferred_schema: dict | None = None
    tokens_used: int | None = None
    processing_time_ms: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Document ──────────────────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    file_type: str
    file_size: int
    status: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentDetail(DocumentResponse):
    """Document detail including the latest processing result."""
    latest_result: ProcessingResultResponse | None = None
    processing_results: list[ProcessingResultResponse] = []


class DocumentUploadResponse(BaseModel):
    """Returned immediately after file upload (processing may be async)."""
    id: uuid.UUID
    filename: str
    file_type: str
    file_size: int
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReprocessRequest(BaseModel):
    processor_type: str | None = Field(
        default=None, description="Override processor; defaults to original"
    )
    model_name: str | None = Field(default=None)


# ── Region OCR ────────────────────────────────────────────────────────────────

class RegionOcrRequest(BaseModel):
    page: int = Field(..., ge=1, description="文档页码，从 1 开始")
    x: float = Field(..., ge=0, le=1)
    y: float = Field(..., ge=0, le=1)
    w: float = Field(..., gt=0, le=1)
    h: float = Field(..., gt=0, le=1)
    action: str = Field(
        ..., description="new_field | correct_field | context"
    )
    target_field_path: str | None = Field(
        default=None, description="action=correct_field 时目标字段路径"
    )


class RegionOcrResponse(BaseModel):
    ocr_text: str
    suggested_field: dict | None = None
    correction_result: dict | None = None
    prompt_version_id: uuid.UUID | None = None
    auto_research_rounds: int = 0


# ── Field Highlights ──────────────────────────────────────────────────────────

class BoundingBoxSchema(BaseModel):
    page: int
    x: float
    y: float
    w: float
    h: float

    model_config = ConfigDict(from_attributes=True)


class FieldHighlight(BaseModel):
    field_path: str
    field_group: str = ""
    group_color: str = "#3B82F6"
    bounding_box: BoundingBoxSchema | None = None
    is_derived: bool = False


class HighlightsResponse(BaseModel):
    highlights: list[FieldHighlight]
    ocr_full_text: str | None = None
