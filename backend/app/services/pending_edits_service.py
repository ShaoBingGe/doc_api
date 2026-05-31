"""
PendingEditsService — read/write helpers for `ApiDefinition.pending_edits`.

The pending_edits overlay holds user edits made in the workspace BEFORE
a customize-job is submitted. It exists to make those edits visible
across all sample documents of the same ApiDef.

Shape:
    {
        "added_fields": [
            {"field_name": "supplierTier",
             "type": "string",
             "description": "...",
             "added_at_doc_id": "<uuid>",
             "default_value": null}
        ],
        "renames": {"<old_field_name>": "<new_field_name>"},
        "modifications": {                          # per-doc value edits
            "<doc_uuid>": {"<field_name>": "<corrected_value>"},
            ...
        }
    }

Invariants (CLAUDE.md §⑥ once added):
  - `renames` is global: one field has exactly one name across all docs.
  - `added_fields` is global: new fields appear on every sample's field list.
  - `modifications` is per-doc: each invoice's values are document-specific.
  - Cleared by Phase 5 on successful customize-job fork.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.annotation import Annotation
from app.models.api_definition import ApiDefinition
from app.models.document import Document

logger = logging.getLogger(__name__)


# ── Shape helpers ─────────────────────────────────────────────────────────────


def _empty() -> dict[str, Any]:
    return {"added_fields": [], "renames": {}, "modifications": {}}


def _normalize(overlay: dict | None) -> dict[str, Any]:
    """Always return a fully-populated dict (no NULL sub-keys)."""
    if not isinstance(overlay, dict):
        return _empty()
    out = _empty()
    out["added_fields"] = list(overlay.get("added_fields") or [])
    out["renames"] = dict(overlay.get("renames") or {})
    out["modifications"] = {
        str(k): dict(v) for k, v in (overlay.get("modifications") or {}).items()
        if isinstance(v, dict)
    }
    return out


# ── Public API ────────────────────────────────────────────────────────────────


def get_overlay(db: Session, api_def_id: uuid.UUID) -> dict[str, Any]:
    """Return the normalized overlay (empty dict if NULL)."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")
    return _normalize(api_def.pending_edits)


def get_overlay_by_doc(db: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Convenience: resolve the doc → ApiDef → overlay."""
    doc = db.get(Document, document_id)
    if not doc or not doc.api_definition_id:
        return _empty()
    return get_overlay(db, doc.api_definition_id)


def _save_overlay(db: Session, api_def: ApiDefinition, overlay: dict) -> None:
    """Detach a fresh dict so SQLAlchemy's JSON dirty-detection fires.
    SQLAlchemy's JSON type compares by identity for some operations;
    assigning a new dict is the safe path."""
    api_def.pending_edits = _normalize(overlay)


def record_rename(
    db: Session,
    api_def_id: uuid.UUID,
    old_name: str,
    new_name: str,
) -> dict[str, Any]:
    """Idempotently register a rename. Chained renames collapse to old → newest."""
    if not old_name or not new_name or old_name == new_name:
        return get_overlay(db, api_def_id)

    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    overlay = _normalize(api_def.pending_edits)
    renames = overlay["renames"]

    # Chain collapse: if user renames A→B then B→C, store A→C
    # If user renames B→A (back to original), drop the entry
    collapsed_old = old_name
    for src, dst in list(renames.items()):
        if dst == old_name:
            collapsed_old = src
            del renames[src]
            break

    if collapsed_old == new_name:
        # Rename canceled (round-trip back to original); ensure no stale entry
        renames.pop(collapsed_old, None)
    else:
        renames[collapsed_old] = new_name

    _save_overlay(db, api_def, overlay)
    db.commit()
    logger.info(
        "Recorded rename on ApiDef %s: %r → %r (collapsed from old=%r)",
        api_def_id, collapsed_old, new_name, old_name,
    )
    return overlay


def record_added_field(
    db: Session,
    api_def_id: uuid.UUID,
    field_name: str,
    field_type: str = "string",
    description: str | None = None,
    added_at_doc_id: uuid.UUID | None = None,
    default_value: Any = None,
) -> dict[str, Any]:
    """Register a new field. No-op if already present."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    overlay = _normalize(api_def.pending_edits)
    existing_names = {f.get("field_name") for f in overlay["added_fields"]}
    if field_name in existing_names:
        return overlay

    overlay["added_fields"].append({
        "field_name": field_name,
        "type": field_type or "string",
        "description": description or "",
        "added_at_doc_id": str(added_at_doc_id) if added_at_doc_id else None,
        "default_value": default_value,
    })
    _save_overlay(db, api_def, overlay)
    db.commit()
    logger.info("Recorded added field on ApiDef %s: %r", api_def_id, field_name)
    return overlay


def record_modification(
    db: Session,
    api_def_id: uuid.UUID,
    document_id: uuid.UUID,
    field_name: str,
    new_value: Any,
) -> dict[str, Any]:
    """Record a per-doc value modification."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    overlay = _normalize(api_def.pending_edits)
    doc_key = str(document_id)
    overlay["modifications"].setdefault(doc_key, {})[field_name] = new_value
    _save_overlay(db, api_def, overlay)
    db.commit()
    return overlay


def clear_overlay(db: Session, api_def_id: uuid.UUID) -> None:
    """Phase 5: reset overlay after successful fork."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        return
    api_def.pending_edits = None
    db.commit()
    logger.info("Cleared pending_edits overlay on ApiDef %s", api_def_id)


# ── Phase 1b: cascade rename to existing Annotation rows ──────────────────────


def cascade_rename_annotations(
    db: Session,
    api_def_id: uuid.UUID,
    old_name: str,
    new_name: str,
) -> int:
    """Option B/1 (user-confirmed): when a field is renamed, update the
    field_name on every existing Annotation row across every Document
    belonging to this ApiDef. Returns the row count touched.

    Why: keeps the per-doc annotation list consistent with the overlay's
    new naming, so the frontend doesn't need to alias at render time.
    """
    if not old_name or not new_name or old_name == new_name:
        return 0
    doc_ids = [d.id for d in db.query(Document.id).filter(
        Document.api_definition_id == api_def_id
    ).all()]
    if not doc_ids:
        return 0

    n = (
        db.query(Annotation)
        .filter(
            Annotation.document_id.in_(doc_ids),
            Annotation.field_name == old_name,
        )
        .update({Annotation.field_name: new_name}, synchronize_session=False)
    )
    db.commit()
    logger.info(
        "Cascaded rename across ApiDef %s docs: %r → %r touched %d annotations",
        api_def_id, old_name, new_name, n,
    )
    return n
