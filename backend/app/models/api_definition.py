"""
ApiDefinition ORM model.

表示一个可对外调用的文档提取 API，由对话矫正确认后生成，
包含 JSON Schema、默认处理器配置和版本信息。
"""

import uuid
from enum import Enum
from typing import Optional

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class ApiDefinitionStatus(str, Enum):
    draft = "draft"
    active = "active"
    deprecated = "deprecated"
    pending_first_doc = "pending_first_doc"  # §6.4 placeholder API awaiting first doc / save


class ApiDefinition(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "api_definitions"
    __table_args__ = (
        UniqueConstraint("api_code", name="uq_api_definitions_api_code"),
    )

    # ── ownership ──────────────────────────────────────────────────────────
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="FK → organizations（原型阶段可为 None）"
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="创建者 FK → users"
    )

    # ── identity ───────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(256), nullable=False, comment="API 显示名称")
    api_code: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        comment="唯一编码，如 inv-cn-vat-v1，用于提取端点路径",
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── status & versioning ────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ApiDefinitionStatus.draft,
        comment="draft|active|deprecated",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="API 版本号，每次 Schema 变更递增"
    )

    # ── schema & prompt ────────────────────────────────────────────────────
    response_schema: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="JSON Schema 定义，提取结果必须符合此 Schema"
    )
    prompt_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="FK → prompt_versions（当前使用的 Prompt 版本）"
    )

    # ── processor config ───────────────────────────────────────────────────
    processor_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="gemini", comment="默认处理器：gemini|openai|mock"
    )
    model_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gemini-2.5-flash", comment="默认模型名称"
    )
    config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="额外处理器配置，如 temperature、max_tokens"
    )

    # ── pending edits overlay (design v8 — multi-sample workspace) ─────────
    # JSON shape:
    #   {
    #     "added_fields":  [{"field_name": "supplierTier", "type": "string",
    #                        "description": "...", "added_at_doc_id": "..."}],
    #     "renames":       {"billFromName": "supplierName"},   # old → new
    #     "modifications": {                                    # per-doc value edits
    #         "<doc_uuid>": {"<field_name>": "<corrected_value>"},
    #         ...
    #     },
    #     "deleted_fields": ["<field_name>", ...]               # Phase 11a
    #   }
    # See docs/prompt-system.md §11 for the cross-sample workspace invariants.
    # Cleared only on explicit user action via DELETE /pending-edits
    # (Phase 19 + Phase 20-P2: overlay survives customize so badges
    # stay visible on the source workspace after iteration completes).
    pending_edits: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="跨样本编辑 overlay：added_fields / renames / modifications"
    )

    # ── source ─────────────────────────────────────────────────────────────
    source_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="从哪个对话创建（原型阶段直接关联）"
    )
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="FK → templates（基于哪个模板，可为 None）"
    )
