"""
UsageRecord — tracks each public extract API call for traffic monitoring.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class UsageRecord(UUIDMixin, Base):
    __tablename__ = "usage_records"

    api_definition_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, index=True, comment="FK → api_definitions"
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, index=True, comment="FK → api_keys"
    )
    api_code: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True, comment="API code snapshot"
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, comment="Unique request ID"
    )
    status_code: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200, comment="HTTP status code"
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Processing time in milliseconds"
    )
    tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="AI tokens consumed"
    )
    request_ip: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="Client IP"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
