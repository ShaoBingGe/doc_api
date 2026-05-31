"""add ApiDefinition.pending_edits overlay column (design v8)

Revision ID: f2c8b3a9d104
Revises: e7d4b91c8a02
Create Date: 2026-06-02

Background: The multi-sample customize workspace needs to surface user
edits made on one sample document when the user is viewing a different
sample of the same ApiDef. Previously edits lived only in:
  - per-document Annotation rows (visible only on that doc)
  - frontend Zustand fieldEditDrafts (lost on selectDocument)
  - a CustomizeJob.diffs payload (only assembled at fork submission)

This migration adds a single nullable JSON column to api_definitions
that holds the live, mid-flight overlay. Composer's _resolve_active_
composed_prompt + the frontend field viewer both consume it.

Shape (initial value: NULL):
    {
        "added_fields": [
            {"field_name": "...", "type": "string",
             "description": "...", "added_at_doc_id": "..."}
        ],
        "renames": {"<old_field_name>": "<new_field_name>"},
        "modifications": {
            "<doc_uuid>": {"<field_name>": "<corrected_value>"},
            ...
        }
    }

Cleared on successful customize-job fork (Phase 5).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers
revision: str = "f2c8b3a9d104"
down_revision: Union[str, None] = "e7d4b91c8a02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_definitions") as batch:
        batch.add_column(sa.Column("pending_edits", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_definitions") as batch:
        batch.drop_column("pending_edits")
