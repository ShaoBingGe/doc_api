"""ocr_optimizer: drop prompt_versions, add 5 new tables

Revision ID: f8a4c2e91d10
Revises: e2601813cfca
Create Date: 2026-05-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

revision: str = "f8a4c2e91d10"
down_revision: Union[str, None] = "e2601813cfca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Drop legacy single-string optimizer table ─────────────────────────
    op.drop_index(op.f("ix_prompt_versions_api_definition_id"), table_name="prompt_versions")
    op.drop_table("prompt_versions")

    # ── 1. ocr_prompt_versions ────────────────────────────────────────────
    op.create_table(
        "ocr_prompt_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("api_definition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("composed_prompt", sa.Text(), nullable=False),
        sa.Column("composed_schema", sqlite.JSON(), nullable=True),
        sa.Column("overall_accuracy", sa.Float(), nullable=True),
        sa.Column("produced_by_run_id", sa.Uuid(), nullable=True),
        sa.Column("produced_in_round", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("api_definition_id", "version", name="uq_ocr_prompt_versions_api_version"),
    )
    op.create_index(
        "ix_ocr_prompt_versions_api_definition_id",
        "ocr_prompt_versions",
        ["api_definition_id"],
    )

    # ── 2. ocr_modules ────────────────────────────────────────────────────
    op.create_table(
        "ocr_modules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "prompt_version_id",
            sa.Uuid(),
            sa.ForeignKey("ocr_prompt_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_key", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("json_path", sa.String(256), nullable=False),
        sa.Column("schema_fragment", sqlite.JSON(), nullable=False),
        sa.Column("ocr_suggestions", sqlite.JSON(), nullable=False, server_default="{}"),
        sa.Column("ocr_prompt", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("module_accuracy", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("prompt_version_id", "module_key", name="uq_ocr_modules_version_key"),
    )
    op.create_index("ix_ocr_modules_prompt_version_id", "ocr_modules", ["prompt_version_id"])
    op.create_index("ix_ocr_modules_module_key", "ocr_modules", ["module_key"])

    # ── 3. ocr_optimization_runs ──────────────────────────────────────────
    op.create_table(
        "ocr_optimization_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("api_definition_id", sa.Uuid(), nullable=False),
        sa.Column("starting_version_id", sa.Uuid(), nullable=False),
        sa.Column("resulting_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("target_accuracy", sa.Float(), nullable=False, server_default="0.95"),
        sa.Column("rounds_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_document_ids", sqlite.JSON(), nullable=False),
        sa.Column("llm_provider", sa.String(64), nullable=False),
        sa.Column("triggered_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metrics", sqlite.JSON(), nullable=True),
    )
    op.create_index(
        "ix_ocr_optimization_runs_api_definition_id",
        "ocr_optimization_runs",
        ["api_definition_id"],
    )

    # ── 4. ocr_optimization_rounds ────────────────────────────────────────
    op.create_table(
        "ocr_optimization_rounds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("ocr_optimization_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round_num", sa.Integer(), nullable=False),
        sa.Column("prompt_version_id", sa.Uuid(), nullable=False),
        sa.Column("next_version_id", sa.Uuid(), nullable=True),
        sa.Column("overall_accuracy", sa.Float(), nullable=True),
        sa.Column("per_sample_accuracy", sqlite.JSON(), nullable=True),
        sa.Column("ocr_raw_outputs", sqlite.JSON(), nullable=True),
        sa.Column("meta_decision", sqlite.JSON(), nullable=True),
        sa.Column("phase", sa.String(24), nullable=False, server_default="ocr_running"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "round_num", name="uq_ocr_optimization_rounds_run_num"),
    )
    op.create_index("ix_ocr_optimization_rounds_run_id", "ocr_optimization_rounds", ["run_id"])

    # ── 5. ocr_module_iterations ──────────────────────────────────────────
    op.create_table(
        "ocr_module_iterations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "round_id",
            sa.Uuid(),
            sa.ForeignKey("ocr_optimization_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_id", sa.Uuid(), nullable=False),
        sa.Column("module_key", sa.String(64), nullable=False),
        sa.Column("per_sample_results", sqlite.JSON(), nullable=False),
        sa.Column("aggregate_accuracy", sa.Float(), nullable=False, server_default="0"),
        sa.Column("aggregate_diff", sqlite.JSON(), nullable=True),
        sa.Column("optimization_suggestion", sa.Text(), nullable=True),
        sa.Column("new_description", sa.Text(), nullable=True),
        sa.Column("new_ocr_suggestions", sqlite.JSON(), nullable=True),
        sa.Column("new_ocr_prompt", sa.Text(), nullable=True),
        sa.Column("llm_call_metadata", sqlite.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("round_id", "module_key", name="uq_ocr_module_iterations_round_module"),
    )
    op.create_index("ix_ocr_module_iterations_round_id", "ocr_module_iterations", ["round_id"])
    op.create_index("ix_ocr_module_iterations_module_key", "ocr_module_iterations", ["module_key"])


def downgrade() -> None:
    op.drop_index("ix_ocr_module_iterations_module_key", table_name="ocr_module_iterations")
    op.drop_index("ix_ocr_module_iterations_round_id", table_name="ocr_module_iterations")
    op.drop_table("ocr_module_iterations")

    op.drop_index("ix_ocr_optimization_rounds_run_id", table_name="ocr_optimization_rounds")
    op.drop_table("ocr_optimization_rounds")

    op.drop_index("ix_ocr_optimization_runs_api_definition_id", table_name="ocr_optimization_runs")
    op.drop_table("ocr_optimization_runs")

    op.drop_index("ix_ocr_modules_module_key", table_name="ocr_modules")
    op.drop_index("ix_ocr_modules_prompt_version_id", table_name="ocr_modules")
    op.drop_table("ocr_modules")

    op.drop_index("ix_ocr_prompt_versions_api_definition_id", table_name="ocr_prompt_versions")
    op.drop_table("ocr_prompt_versions")

    # Recreate legacy table for full reversibility
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("api_definition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("accuracy_score", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("optimization_metadata", sqlite.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_prompt_versions_api_definition_id", "prompt_versions", ["api_definition_id"])
