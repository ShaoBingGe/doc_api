"""ocr_optimizer v2: paused_for_review state machine + skills (TODO) + manual_edit versions

Revision ID: a1b2c3d4e5f6
Revises: f8a4c2e91d10
Create Date: 2026-05-25

Adds:
  - ocr_prompt_versions.version: INT → STRING(16) (for "2.1" style manual_edit suffixes)
  - ocr_prompt_versions.origin (init|round|manual_edit)
  - ocr_modules.skill_ids JSON default '[]'
  - ocr_optimization_runs.current_round_num INT default 0
  - ocr_module_iterations.skill_feedback TEXT NULL
  - NEW table ocr_skills (TODO placeholder, no service uses it yet)
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f8a4c2e91d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ocr_prompt_versions: add origin, convert version to String ───────
    with op.batch_alter_table("ocr_prompt_versions") as batch:
        batch.add_column(
            sa.Column(
                "origin",
                sa.String(16),
                nullable=False,
                server_default="init",
            )
        )
        # Cast existing INT version to String. SQLite alter_column type change
        # is handled by batch_alter_table recreating the table.
        batch.alter_column(
            "version",
            existing_type=sa.Integer(),
            type_=sa.String(16),
            existing_nullable=False,
        )

    # Existing rows: round products → origin='round' if produced_by_run_id is not null;
    # else origin='init' (the default already covers).
    op.execute(
        "UPDATE ocr_prompt_versions SET origin = 'round' "
        "WHERE produced_by_run_id IS NOT NULL"
    )

    # ── ocr_modules: add skill_ids JSON ──────────────────────────────────
    with op.batch_alter_table("ocr_modules") as batch:
        batch.add_column(
            sa.Column(
                "skill_ids",
                sqlite.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    # ── ocr_optimization_runs: add current_round_num ─────────────────────
    with op.batch_alter_table("ocr_optimization_runs") as batch:
        batch.add_column(
            sa.Column(
                "current_round_num",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    # Backfill current_round_num from rounds_completed for existing runs
    op.execute(
        "UPDATE ocr_optimization_runs SET current_round_num = rounds_completed"
    )

    # ── ocr_module_iterations: add skill_feedback ────────────────────────
    with op.batch_alter_table("ocr_module_iterations") as batch:
        batch.add_column(sa.Column("skill_feedback", sa.Text(), nullable=True))

    # ── NEW table ocr_skills (TODO placeholder) ──────────────────────────
    op.create_table(
        "ocr_skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("api_definition_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "api_definition_id", "name", name="uq_ocr_skills_api_name"
        ),
    )
    op.create_index(
        "ix_ocr_skills_api_definition_id",
        "ocr_skills",
        ["api_definition_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ocr_skills_api_definition_id", table_name="ocr_skills")
    op.drop_table("ocr_skills")

    with op.batch_alter_table("ocr_module_iterations") as batch:
        batch.drop_column("skill_feedback")

    with op.batch_alter_table("ocr_optimization_runs") as batch:
        batch.drop_column("current_round_num")

    with op.batch_alter_table("ocr_modules") as batch:
        batch.drop_column("skill_ids")

    with op.batch_alter_table("ocr_prompt_versions") as batch:
        batch.drop_column("origin")
        batch.alter_column(
            "version",
            existing_type=sa.String(16),
            type_=sa.Integer(),
            existing_nullable=False,
        )
