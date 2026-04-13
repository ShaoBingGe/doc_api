"""
Document and ProcessingResult ORM models.

Document — represents an uploaded file (PDF/image/xlsx).
ProcessingResult — one AI-extraction run; multiple versions per document.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class DocumentStatus(str, Enum):
    uploading = "uploading"
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ProcessingResultSource(str, Enum):
    initial = "initial"        # First AI extraction run
    correction = "correction"  # Produced by conversational correction
    manual_edit = "manual_edit"  # Produced by direct manual edit


class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    # ── ownership ──────────────────────────────────────────────────────────
    # 原型阶段不实现多租户，保留字段占位以便后续迁移
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)

    # ── file metadata ──────────────────────────────────────────────────────
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="pdf | png | jpg | xlsx"
    )
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="bytes")
    storage_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="local path or S3 key"
    )

    # ── processor config ───────────────────────────────────────────────────
    processor_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, comment="Default processor configuration key for this document"
    )

    # ── processing state ───────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DocumentStatus.uploading,
        comment="uploading|queued|processing|completed|failed",
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── relationships ──────────────────────────────────────────────────────
    processing_results: Mapped[list["ProcessingResult"]] = relationship(
        "ProcessingResult",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ProcessingResult.version",
    )
    annotations: Mapped[list["Annotation"]] = relationship(  # type: ignore[name-defined]
        "Annotation",
        back_populates="document",
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list["Conversation"]] = relationship(  # type: ignore[name-defined]
        "Conversation",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class ProcessingResult(UUIDMixin, Base):
    __tablename__ = "processing_results"

    # ── foreign keys ───────────────────────────────────────────────────────
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)

    # ── versioning ─────────────────────────────────────────────────────────
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="incremented on each re-run/correction"
    )

    # ── source tracking ────────────────────────────────────────────────────
    prompt_used: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="The prompt text used for this extraction run"
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ProcessingResultSource.initial,
        comment="initial|correction|manual_edit — how this result was produced",
    )

    # ── processor info ─────────────────────────────────────────────────────
    processor_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="gemini | openai | piaozone | mock"
    )
    model_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="e.g. gemini-2.5-flash"
    )

    # ── AI output ──────────────────────────────────────────────────────────
    raw_output: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="verbatim AI response"
    )
    structured_data: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True, comment="normalized key-value list: [{id, keyName, value, confidence, bbox}]"
    )
    inferred_schema: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="auto-inferred JSON Schema"
    )

    # ── performance ────────────────────────────────────────────────────────
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── relationships ──────────────────────────────────────────────────────
    document: Mapped["Document"] = relationship("Document", back_populates="processing_results")
