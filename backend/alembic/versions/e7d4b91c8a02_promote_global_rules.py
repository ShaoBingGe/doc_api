"""promote global_rules: OcrPromptVersion.country_global_text column + remove module rows

Revision ID: e7d4b91c8a02
Revises: d6e9a2b4f1c8
Create Date: 2026-06-02

Background: `global_rules` was a per-version row in `ocr_modules` carrying
country-wide rule text via json_path="$" / schema_fragment={}. It contributed
no schema, conceptually wasn't a "field module", and was cloned across every
fork + every round (42 redundant rows in live DB).

This migration:
  1. Adds OcrPromptVersion.country_global_text (nullable TEXT).
  2. Copies each version's global_rules.ocr_prompt → version's new column.
  3. HARD DELETES those global_rules ocr_modules rows.

Dry-run: set env var GLOBAL_RULES_MIGRATION_DRY_RUN=1 to log counts without
mutating the data rows. The DDL add_column still runs (otherwise the column
wouldn't exist for the SELECT). Re-running after a dry-run is safe: the
UPDATE is idempotent (same source → same target) and DELETE just removes
whatever rows remain.

Downgrade: re-inserts a synthetic global_rules OcrModule per version that
has non-NULL country_global_text, then drops the column. The synthetic
rows lose their original UUIDs and timestamps but preserve the prompt text.
"""
from __future__ import annotations

import logging
import os
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

logger = logging.getLogger("alembic.runtime.migration.promote_global_rules")


revision: str = "e7d4b91c8a02"
down_revision: Union[str, None] = "d6e9a2b4f1c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DRY_RUN = os.environ.get("GLOBAL_RULES_MIGRATION_DRY_RUN") == "1"


def upgrade() -> None:
    # ── DDL: add column (always runs) ────────────────────────────────────
    with op.batch_alter_table("ocr_prompt_versions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("country_global_text", sa.Text(), nullable=True)
        )

    conn = op.get_bind()

    # ── Data: count what we'd touch (always logged) ──────────────────────
    versions_with_gr = conn.execute(sa.text(
        "SELECT COUNT(DISTINCT prompt_version_id) FROM ocr_modules "
        "WHERE module_key='global_rules'"
    )).scalar() or 0
    module_rows = conn.execute(sa.text(
        "SELECT COUNT(*) FROM ocr_modules WHERE module_key='global_rules'"
    )).scalar() or 0
    versions_total = conn.execute(sa.text(
        "SELECT COUNT(*) FROM ocr_prompt_versions"
    )).scalar() or 0

    msg = (
        f"global_rules migration scope: "
        f"would set country_global_text on {versions_with_gr} versions "
        f"(of {versions_total} total) and delete {module_rows} ocr_modules rows."
    )

    if _DRY_RUN:
        logger.warning("DRY-RUN: %s (no UPDATE/DELETE executed)", msg)
        print(f"\n[DRY-RUN] {msg}\n")
        return

    logger.warning(msg)

    # ── Data move: UPDATE first, then DELETE ─────────────────────────────
    # SQLite-friendly correlated subquery. Postgres/MySQL also accept this.
    conn.execute(sa.text(
        """
        UPDATE ocr_prompt_versions
        SET country_global_text = (
            SELECT ocr_prompt
            FROM ocr_modules
            WHERE ocr_modules.prompt_version_id = ocr_prompt_versions.id
              AND ocr_modules.module_key = 'global_rules'
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1
            FROM ocr_modules
            WHERE ocr_modules.prompt_version_id = ocr_prompt_versions.id
              AND ocr_modules.module_key = 'global_rules'
        )
        """
    ))

    # ── Now safe to delete: data is in the new column ────────────────────
    res = conn.execute(sa.text(
        "DELETE FROM ocr_modules WHERE module_key='global_rules'"
    ))
    logger.warning(
        "Deleted %d global_rules ocr_modules rows; "
        "country_global_text populated on %d versions.",
        res.rowcount if res.rowcount is not None else module_rows,
        versions_with_gr,
    )


def downgrade() -> None:
    # Re-create synthetic global_rules rows from country_global_text, then
    # drop the column. Loses original UUIDs/timestamps but preserves text.
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, country_global_text FROM ocr_prompt_versions "
        "WHERE country_global_text IS NOT NULL"
    )).fetchall()

    for version_id, text in rows:
        new_id = uuid.uuid4().hex
        conn.execute(
            sa.text(
                """
                INSERT INTO ocr_modules
                  (id, prompt_version_id, module_key, display_name, description,
                   json_path, schema_fragment, ocr_suggestions, ocr_prompt,
                   skill_ids, order_index, status)
                VALUES
                  (:id, :pvid, 'global_rules', '全局规则与约束',
                   '迁移恢复：原 global_rules 模块',
                   '$', '{}', '{}', :prompt,
                   '[]', 0, 'active')
                """
            ),
            {"id": new_id, "pvid": version_id, "prompt": text},
        )

    with op.batch_alter_table("ocr_prompt_versions", schema=None) as batch_op:
        batch_op.drop_column("country_global_text")
