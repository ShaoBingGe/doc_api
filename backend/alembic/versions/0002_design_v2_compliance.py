"""design-v2 compliance fixes

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-04

Changes:
  - ProcessingResult: add prompt_used (Text), source (String)
  - ProcessingResult: structured_data comment update (list format)
  - Document: add processor_key (String)
  - Annotation: add result_version (Integer)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── processing_results: prompt_used, source ───────────────────────────
    op.add_column(
        "processing_results",
        sa.Column("prompt_used", sa.Text(), nullable=True),
    )
    op.add_column(
        "processing_results",
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="initial",
        ),
    )

    # ── documents: processor_key ──────────────────────────────────────────
    op.add_column(
        "documents",
        sa.Column("processor_key", sa.String(128), nullable=True),
    )

    # ── annotations: result_version ───────────────────────────────────────
    op.add_column(
        "annotations",
        sa.Column("result_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("annotations", "result_version")
    op.drop_column("documents", "processor_key")
    op.drop_column("processing_results", "source")
    op.drop_column("processing_results", "prompt_used")
