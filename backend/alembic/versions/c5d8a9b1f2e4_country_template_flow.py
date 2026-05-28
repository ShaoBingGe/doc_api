"""country-template flow: documents.api_definition_id + pending_first_doc enum value

Revision ID: c5d8a9b1f2e4
Revises: a1b2c3d4e5f6
Create Date: 2026-05-27

Adds:
  - documents.api_definition_id UUID NULLABLE + INDEX
    (used by §6.4 country-template upload binding so reprocess_document can
     pull the active OcrPromptVersion.composed_prompt for this doc's API)

Note: api_definitions.status is already a VARCHAR(32) column with free-form
string values, so no DDL change is needed to allow 'pending_first_doc' —
the enum is enforced at the Python layer only.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d8a9b1f2e4"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_definition_id", sa.Uuid(), nullable=True))
        batch_op.create_index(
            "ix_documents_api_definition_id",
            ["api_definition_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index("ix_documents_api_definition_id")
        batch_op.drop_column("api_definition_id")
