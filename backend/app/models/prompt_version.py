"""
PromptVersion — tracks iterative prompt optimization for each API definition.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class PromptVersion(UUIDMixin, Base):
    __tablename__ = "prompt_versions"

    api_definition_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, index=True, comment="FK → api_definitions"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="Auto-incremented version number"
    )
    prompt_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Full prompt content"
    )
    accuracy_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="Accuracy on evaluation set (0-1)"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Currently active version"
    )
    parent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="Parent version for iteration chain tracking"
    )
    optimization_metadata: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="Optimization process metadata (diff, eval details)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
