"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-04

创建所有核心表：
  documents, processing_results,
  annotations,
  conversations, messages,
  api_definitions, api_keys
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── documents ─────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("file_type", sa.String(16), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="uploading"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── processing_results ────────────────────────────────────────────────
    op.create_table(
        "processing_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_version_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("processor_type", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("raw_output", sa.JSON(), nullable=True),
        sa.Column("structured_data", sa.JSON(), nullable=True),
        sa.Column("inferred_schema", sa.JSON(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_processing_results_document_id", "processing_results", ["document_id"])

    # ── annotations ───────────────────────────────────────────────────────
    op.create_table(
        "annotations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_result_id", sa.Uuid(), sa.ForeignKey("processing_results.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("field_name", sa.String(256), nullable=False),
        sa.Column("field_value", sa.Text(), nullable=True),
        sa.Column("field_type", sa.String(16), nullable=False, server_default="string"),
        sa.Column("bounding_box", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="ai_detected"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_corrected", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("original_bbox", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_annotations_document_id", "annotations", ["document_id"])
    op.create_index("ix_annotations_processing_result_id", "annotations", ["processing_result_id"])

    # ── conversations (stub) ──────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("current_schema", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversations_document_id", "conversations", ["document_id"])

    # ── messages (stub) ───────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_version_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("schema_diff", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    # ── api_definitions ───────────────────────────────────────────────────
    op.create_table(
        "api_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("api_code", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("response_schema", sa.JSON(), nullable=True),
        sa.Column("prompt_version_id", sa.Uuid(), nullable=True),
        sa.Column("processor_type", sa.String(32), nullable=False, server_default="gemini"),
        sa.Column("model_name", sa.String(128), nullable=False, server_default="gemini-2.5-flash"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("source_conversation_id", sa.Uuid(), nullable=True),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("uq_api_definitions_api_code", "api_definitions", ["api_code"], unique=True)

    # ── api_keys ──────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("rate_limit", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("api_definitions")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("annotations")
    op.drop_table("processing_results")
    op.drop_table("documents")
