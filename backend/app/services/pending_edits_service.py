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
        },
        "deleted_fields": ["<field_name>", ...]      # Phase 11a
    }

Invariants:
  - `renames` is global: one field has exactly one name across all docs.
  - `added_fields` is global: new fields appear on every sample's field list.
  - `modifications` is per-doc: each invoice's values are document-specific.
  - `deleted_fields` is global + ABSOLUTE: a deleted field has NO meta,
    NO reflection, NO module slot; the optimizer drops it entirely.
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
    return {
        "added_fields": [],
        "renames": {},
        "modifications": {},
        "deleted_fields": [],
    }


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
    out["deleted_fields"] = list(overlay.get("deleted_fields") or [])
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


def record_deleted_field(
    db: Session,
    api_def_id: uuid.UUID,
    field_name: str,
) -> tuple[dict[str, Any], int]:
    """Phase 11a: register a field deletion + cascade across all docs.

    Returns (overlay, annotations_deleted_count).

    Semantics (per user spec for deleted fields):
      - The field is fully removed from the field set: no meta, no module
        slot, no reflection agent invocation, no schema entry.
      - All existing Annotation rows for this field across every Document
        of this ApiDef are HARD-DELETED (not archived) so they no longer
        contribute to OCR baseline or display.
      - The field name is also dropped from any pending added_fields entry
        and any modifications[*][field_name] entry, since they no longer
        make sense.
      - The rename map keeps its entries (if the deleted field had a rename
        chain, the user can choose to clean it up via separate UI).
    """
    if not field_name:
        return get_overlay(db, api_def_id), 0

    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    overlay = _normalize(api_def.pending_edits)

    # Add to deleted_fields (idempotent)
    if field_name not in overlay["deleted_fields"]:
        overlay["deleted_fields"].append(field_name)

    # Strip from added_fields (delete-after-add cancels both)
    overlay["added_fields"] = [
        f for f in overlay["added_fields"]
        if f.get("field_name") != field_name
    ]

    # Strip from modifications (per-doc value mods no longer apply)
    for doc_key in list(overlay["modifications"].keys()):
        overlay["modifications"][doc_key].pop(field_name, None)
        if not overlay["modifications"][doc_key]:
            del overlay["modifications"][doc_key]

    _save_overlay(db, api_def, overlay)

    # Cascade-DELETE annotations across all docs of this ApiDef
    doc_ids = [d.id for d in db.query(Document.id).filter(
        Document.api_definition_id == api_def_id
    ).all()]
    deleted_count = 0
    if doc_ids:
        deleted_count = (
            db.query(Annotation)
            .filter(
                Annotation.document_id.in_(doc_ids),
                Annotation.field_name == field_name,
            )
            .delete(synchronize_session=False)
        )

    db.commit()
    logger.info(
        "Recorded deletion of field %r on ApiDef %s; hard-deleted %d annotation rows",
        field_name, api_def_id, deleted_count,
    )
    return overlay, deleted_count


def compute_required_field_set(db: Session, api_def_id: uuid.UUID) -> list[str]:
    """N4 — canonical "what every sample should produce" field list.

    Mirrors the GET /api-definitions/{id}/required-fields endpoint so both
    the frontend missing-fields panel AND the backend OCR-prompt
    augmentation / post-OCR null-padding use the same source of truth.

    Computed as:
        union(active modules' field names, overlay.added_fields)
        − overlay.deleted_fields
        + apply overlay.renames (old → new)

    Returns the field name list in module order, with added-fields
    appended at the end.
    """
    from app.ocr_optimizer.models import (
        OcrModule, OcrPromptVersion, PromptVersionStatus,
    )

    version = (
        db.query(OcrPromptVersion)
        .filter(
            OcrPromptVersion.api_definition_id == api_def_id,
            OcrPromptVersion.status == PromptVersionStatus.active.value,
        )
        .first()
    )
    base_fields: list[str] = []
    if version is not None:
        modules = (
            db.query(OcrModule)
            .filter(OcrModule.prompt_version_id == version.id)
            .order_by(OcrModule.order_index)
            .all()
        )
        for m in modules:
            path = m.json_path or ""
            leaf = path.split(".")[-1] if path else ""
            leaf = leaf.replace("[*]", "").replace("[", "").replace("]", "").strip()
            if leaf and leaf not in {"$", ""}:
                base_fields.append(leaf)

    overlay = get_overlay(db, api_def_id)
    renames = overlay.get("renames") or {}
    renamed = [renames.get(f, f) for f in base_fields]

    seen = set(renamed)
    for f in overlay.get("added_fields") or []:
        name = (f or {}).get("field_name") or ""
        if name and name not in seen:
            renamed.append(name)
            seen.add(name)

    # ── Monotonic parity union (issue-1 fix) ──────────────────────────────
    # Fields the LLM produced on a CONFIRMED sample but that aren't modules
    # (e.g. nameOfInvoice, detailOfTaxSummary, billToTaxIdentificationNumber)
    # are legitimate GT-reviewed fields. Without unioning them in, a newly
    # uploaded sample is padded only to the module-derived set and the
    # cross-sample field set silently SHRINKS — the user's "文件 1 确认的
    # 字段集在上传文件 2/3 后丢失" report. A confirmed field must never
    # vanish unless the user explicitly deletes/renames it, so we fold every
    # confirmed sample's observed top-level keys into the required set.
    for name in _observed_top_level_keys_from_confirmed(db, api_def_id, renames):
        if name and name not in seen:
            renamed.append(name)
            seen.add(name)

    deleted = set(overlay.get("deleted_fields") or [])
    return [f for f in renamed if f not in deleted]


def _observed_top_level_keys_from_confirmed(
    db: Session,
    api_def_id: uuid.UUID,
    renames: dict[str, str],
) -> list[str]:
    """Top-level keys observed in the latest structured_data of every
    CONFIRMED sample (one that has at least one GT annotation) bound to this
    ApiDef, with renames applied. Confirmed-only bounds pollution: an
    un-reviewed sample's hallucinated key can't enter the parity set.

    Array/flattened keys ("foo[0]", "a.b") are skipped — the parity set is
    top-level only, matching the module-derived leaf names.
    """
    from app.models.document import Document, ProcessingResult
    from app.ocr_optimizer.service.ground_truth import has_ground_truth

    out: list[str] = []
    seen: set[str] = set()
    docs = (
        db.query(Document)
        .filter(Document.api_definition_id == api_def_id)
        .all()
    )
    for d in docs:
        if not has_ground_truth(db, d.id):
            continue
        pr = (
            db.query(ProcessingResult)
            .filter(ProcessingResult.document_id == d.id)
            .order_by(ProcessingResult.version.desc())
            .first()
        )
        if not pr or not pr.structured_data:
            continue
        sd = pr.structured_data
        records = sd if isinstance(sd, list) else [sd]
        for rec in records:
            if not isinstance(rec, dict):
                continue
            for k in rec.keys():
                if not k or "[" in k or "." in k:
                    continue
                nk = renames.get(k, k)
                if nk not in seen:
                    seen.add(nk)
                    out.append(nk)
    return out


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
