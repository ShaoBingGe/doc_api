"""
Common / shared Pydantic schemas used across multiple modules.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class BoundingBox(BaseModel):
    """归一化坐标系（0-1），不绑定具体像素分辨率。"""

    page: int
    x: float
    y: float
    w: float
    h: float

    model_config = ConfigDict(from_attributes=True)
