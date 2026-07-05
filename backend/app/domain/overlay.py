"""Overlay 域模块 —— `ApiDefinition.pending_edits` 的纯数据读写（结构第二轮 A1）.

overlay 保存客户在工作区提交定制前的字段编辑（rename / add / delete /
modification / field_constraint / field_feedback），使这些编辑跨同一 ApiDef
的全部样本可见。

**这是依赖图的中立叶子层**：只依赖 `app.models` + `app.core`，
**不依赖 app.services，也不依赖 ocr_optimizer**——因此 ocr_optimizer 引擎侧
可以直接 import 它读 overlay，而不再反向依赖 `app.services.pending_edits_service`
（消除双向依赖，见 repository-structure.md §六）。

国别锁定集（country-locked：法规不可改字段）不在这里解析——它由
`ocr_optimizer.field_constraints` 提供，属引擎知识。凡需要拒绝对锁定字段的
编辑的函数，都把锁定集作为 `country_locked` 参数注入（默认空集 = 不拦），
使本模块保持对引擎零依赖。带副作用的「立即生效到活跃版本」
（apply_to_active_version）也留在 service 层。

Shape / 不变量见 `app/services/pending_edits_service.py`（本模块的 facade）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection
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
        # field_constraints: explicit, sticky per-field type/format overrides
        # ({field: {type, strip_chars, strip_non_numeric, locked, note}}).
        # Enforced by ocr_optimizer.service.field_constraints — survives every
        # optimization round and overrides the country template's Part 1.
        "field_constraints": {},
        # field_feedback: per-field free-text USER FEEDBACK ({field: text}).
        # NOT the final prompt — injected as reflection CONTEXT during the next
        # optimization so the optimizer knows what the customer expects.
        "field_feedback": {},
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
    out["field_constraints"] = {
        str(k): dict(v) for k, v in (overlay.get("field_constraints") or {}).items()
        if isinstance(v, dict)
    }
    out["field_feedback"] = {
        str(k): str(v) for k, v in (overlay.get("field_feedback") or {}).items()
        if v is not None
    }
    return out


def _save_overlay(db: Session, api_def: ApiDefinition, overlay: dict) -> None:
    """Detach a fresh dict so SQLAlchemy's JSON dirty-detection fires.
    SQLAlchemy's JSON type compares by identity for some operations;
    assigning a new dict is the safe path."""
    api_def.pending_edits = _normalize(overlay)


# ── Reads ─────────────────────────────────────────────────────────────────────


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


def clear_overlay(db: Session, api_def_id: uuid.UUID) -> None:
    """Phase 5: reset overlay after successful fork."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        return
    api_def.pending_edits = None
    db.commit()
    logger.info("Cleared pending_edits overlay on ApiDef %s", api_def_id)


# ── Writes (country_locked injected by the service facade) ────────────────────


def record_field_feedback(
    db: Session,
    api_def_id: uuid.UUID,
    field_name: str,
    text: str | None,
) -> dict[str, Any]:
    """Register / update a field's free-text USER FEEDBACK. This is NOT the final
    prompt — it is injected as reflection CONTEXT in the next optimization so the
    optimizer learns what the customer expects. Empty text clears it."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")
    if not field_name:
        return _normalize(api_def.pending_edits)
    overlay = _normalize(api_def.pending_edits)
    t = (text or "").strip()
    if t:
        overlay["field_feedback"][field_name] = t
    else:
        overlay["field_feedback"].pop(field_name, None)
    _save_overlay(db, api_def, overlay)
    db.commit()
    return overlay


def record_rename(
    db: Session,
    api_def_id: uuid.UUID,
    old_name: str,
    new_name: str,
    *,
    country_locked: Collection[str] = frozenset(),
) -> dict[str, Any]:
    """Idempotently register a rename. Chained renames collapse to old → newest.

    `country_locked`: names the customer may NOT rename (precedence: lock >
    override). Injected by the facade so this module stays engine-free."""
    if not old_name or not new_name or old_name == new_name:
        return get_overlay(db, api_def_id)

    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    if old_name in country_locked or new_name in country_locked:
        logger.warning("Refused rename touching country-locked field: %r→%r", old_name, new_name)
        return _normalize(api_def.pending_edits)

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

    # A field constraint follows its field's name so the override keeps
    # targeting the right leaf after the rename cascades into modules.
    fc = overlay["field_constraints"]
    if old_name in fc and old_name != new_name:
        fc[new_name] = fc.pop(old_name)

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
    *,
    country_locked: Collection[str] = frozenset(),
) -> dict[str, Any]:
    """Register a new field. No-op if already present."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    if field_name in country_locked:
        logger.warning("Refused add of country-locked field name: %r", field_name)
        return _normalize(api_def.pending_edits)

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


def record_field_constraint(
    db: Session,
    api_def_id: uuid.UUID,
    field_name: str,
    *,
    field_type: str | None = None,
    strip_chars: list[str] | None = None,
    strip_non_numeric: bool | None = None,
    locked: bool = True,
    note: str | None = None,
    country_locked: Collection[str] = frozenset(),
) -> dict[str, Any]:
    """Register / update an explicit per-field type+format override.

    This is the customer-override the platform must NEVER overrule with its
    general knowledge (country Part 1, field description, reflection). It is
    enforced deterministically by ocr_optimizer.service.field_constraints at
    every version-composition point + on the extracted value.

    `locked` is the per-constraint flag stored in the entry; `country_locked`
    is the set of regulatory fields the customer may not override at all.
    Passing field_type=None AND no strip behaviour REMOVES the constraint.

    NOTE: this does NOT call apply_to_active_version (an engine side-effect) —
    the facade does that after, keeping this module engine-free."""
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    if field_name in country_locked:
        logger.warning("Refused field-constraint override on country-locked field: %r", field_name)
        # Also drop any stale override left over from before the field was
        # locked, so it can't silently reactivate if the field is ever unlocked.
        ov = _normalize(api_def.pending_edits)
        if field_name in ov["field_constraints"]:
            ov["field_constraints"].pop(field_name, None)
            _save_overlay(db, api_def, ov)
            db.commit()
        return ov

    overlay = _normalize(api_def.pending_edits)
    fc = overlay["field_constraints"]

    has_strip = bool(strip_chars) or bool(strip_non_numeric)
    if not field_type and not has_strip:
        fc.pop(field_name, None)  # unset
    else:
        entry: dict[str, Any] = {"locked": bool(locked)}
        if field_type:
            entry["type"] = field_type
        if strip_chars is not None:
            entry["strip_chars"] = list(strip_chars)
        if strip_non_numeric is not None:
            entry["strip_non_numeric"] = bool(strip_non_numeric)
        if note:
            entry["note"] = note
        fc[field_name] = entry

    _save_overlay(db, api_def, overlay)
    db.commit()
    logger.info(
        "Recorded field constraint on ApiDef %s: %r -> %s",
        api_def_id, field_name, fc.get(field_name, "(removed)"),
    )
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
    *,
    country_locked: Collection[str] = frozenset(),
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

    if field_name in country_locked:
        logger.warning("Refused delete of country-locked field: %r", field_name)
        return _normalize(api_def.pending_edits), 0

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

    # Strip any field constraint (a deleted field has no slot to enforce on)
    overlay["field_constraints"].pop(field_name, None)

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
