"""customize_jobs table — persistent state for the customer customize pipeline

Revision ID: d6e9a2b4f1c8
Revises: c5d8a9b1f2e4
Create Date: 2026-05-28

Adds the `customize_jobs` table that persists the lifecycle of a customer
field-iteration job (status: queued / waiting_for_samples / reflecting /
forking / optimizing / completed / failed). Replaces the in-memory job dict
in customer_iteration.py so jobs survive a process restart and so the
"upload more samples → resume" gate has somewhere to park state.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6e9a2b4f1c8"
down_revision: Union[str, None] = "c5d8a9b1f2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customize_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("source_api_definition_id", sa.Uuid(), nullable=False),
        sa.Column("new_api_definition_id", sa.Uuid(), nullable=True),
        sa.Column("new_api_code", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("phase_detail", sa.Text(), nullable=True),
        sa.Column("diffs", sa.JSON(), nullable=False),
        sa.Column("reflection_summary", sa.JSON(), nullable=True),
        sa.Column("rounds_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rounds_total", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("overall_accuracy", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_customize_jobs_source_api_definition_id",
        "customize_jobs",
        ["source_api_definition_id"],
        unique=False,
    )
    op.create_index(
        "ix_customize_jobs_new_api_definition_id",
        "customize_jobs",
        ["new_api_definition_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_customize_jobs_new_api_definition_id", table_name="customize_jobs")
    op.drop_index("ix_customize_jobs_source_api_definition_id", table_name="customize_jobs")
    op.drop_table("customize_jobs")
