"""
Annotation ORM model.

存储用户对 AI 识别结果的手动标注数据（字段名/值/文档区域坐标），
供后续模型训练和修正率统计使用。
"""

import uuid
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class AnnotationSource(str, Enum):
    ai_detected = "ai_detected"  # AI 自动识别
    manual = "manual"            # 用户手动添加


class FieldType(str, Enum):
    string = "string"
    number = "number"
    date = "date"
    array = "array"
    boolean = "boolean"


class Annotation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "annotations"

    # ── foreign keys ───────────────────────────────────────────────────────
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    processing_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("processing_results.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联到哪个版本的处理结果",
    )
    result_version: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="ProcessingResult.version snapshot — which version this annotation belongs to"
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="FK → users（原型阶段可为 None）"
    )

    # ── field definition ───────────────────────────────────────────────────
    field_name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="字段名，如 invoice_no、buyer_name"
    )
    field_value: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="字段值，如 '04172872'"
    )
    field_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=FieldType.string,
        comment="string|number|date|array|boolean",
    )

    # ── document region ────────────────────────────────────────────────────
    bounding_box: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="{page, x, y, w, h} — 归一化 0-1 坐标，仅用于前端高亮和训练，不写入 Prompt",
    )

    # ── correction tracking ────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=AnnotationSource.ai_detected,
        comment="ai_detected|manual",
    )
    confidence: Mapped[Optional[float]] = mapped_column(
        nullable=True, comment="AI 识别置信度 0-1（manual 时为 None）"
    )
    is_corrected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="用户是否修正过 AI 识别结果"
    )
    original_value: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="修正前的原始值，用于计算修正率"
    )
    original_bbox: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="修正前的原始区域坐标"
    )

    # ── relationships ──────────────────────────────────────────────────────
    document: Mapped["Document"] = relationship(  # type: ignore[name-defined]
        "Document", back_populates="annotations"
    )
    processing_result: Mapped[Optional["ProcessingResult"]] = relationship(  # type: ignore[name-defined]
        "ProcessingResult",
        foreign_keys=[processing_result_id],
    )
