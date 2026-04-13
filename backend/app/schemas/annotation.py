"""
Pydantic schemas for Annotation CRUD.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .common import BoundingBox


# ── Requests ──────────────────────────────────────────────────────────────────

class CreateAnnotationRequest(BaseModel):
    field_name: str = Field(..., min_length=1, max_length=256)
    field_value: str | None = None
    field_type: str = Field(default="string", description="string|number|date|array|boolean")
    bounding_box: BoundingBox | None = None
    source: str = Field(default="manual", description="ai_detected|manual")
    confidence: float | None = Field(default=None, ge=0, le=1)
    processing_result_id: uuid.UUID | None = None


class UpdateAnnotationRequest(BaseModel):
    field_name: str | None = Field(default=None, min_length=1, max_length=256)
    field_value: str | None = None
    field_type: str | None = None
    bounding_box: BoundingBox | None = None


class BatchAnnotationRequest(BaseModel):
    annotations: list[CreateAnnotationRequest]
    processing_result_id: uuid.UUID


class BatchUpdateItem(BaseModel):
    annotation_id: uuid.UUID
    field_name: str | None = Field(default=None, min_length=1, max_length=256)
    field_value: str | None = None
    field_type: str | None = None
    bounding_box: BoundingBox | None = None


class BatchUpdateRequest(BaseModel):
    updates: list[BatchUpdateItem]


# ── Responses ─────────────────────────────────────────────────────────────────

class AnnotationResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    processing_result_id: uuid.UUID | None = None
    field_name: str
    field_value: str | None = None
    field_type: str
    bounding_box: BoundingBox | None = None
    source: str
    confidence: float | None = None
    is_corrected: bool
    original_value: str | None = None
    original_bbox: BoundingBox | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnnotationListResponse(BaseModel):
    annotations: list[AnnotationResponse]
    document_id: uuid.UUID
    total: int
    skip: int = 0
    limit: int = 100
    correction_rate: float = Field(
        description="被修正字段数 / 总字段数，用于训练优先级排序"
    )
