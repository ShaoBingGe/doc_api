"""
Conversation and Message ORM models — STUB (not yet developed).

对话矫正功能暂未开发，模型定义保留以维持数据库结构完整性，
API 端点和业务逻辑将在后续阶段实现。
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class ConversationStatus(str, Enum):
    active = "active"
    completed = "completed"
    archived = "archived"


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


# ── STUB: 表结构保留，API 端点暂不实现 ────────────────────────────────────────

class Conversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConversationStatus.active
    )
    current_schema: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    document: Mapped["Document"] = relationship(  # type: ignore[name-defined]
        "Document", back_populates="conversations"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(UUIDMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    schema_diff: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )
