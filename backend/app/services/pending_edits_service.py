"""PendingEditsService — service-layer facade over `app.domain.overlay`.

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

结构第二轮 A1：纯数据读写移到 `app/domain/overlay.py`（中立叶子层，
ocr_optimizer 引擎可直接 import 它读 overlay，不再反向依赖本模块）。本模块
留作 **service-layer facade**，承担两件 domain 不该碰的引擎相关职责：
  1. 解析国别锁定集（`_locked_set` → ocr_optimizer.field_constraints）并注入
     domain 的锁定守卫函数（record_rename / add / delete / field_constraint）；
  2. 带引擎副作用的操作（record_field_constraint 的 apply_to_active_version
     「立即生效到活跃版本」）+ 依赖 ocr_optimizer.models / ground_truth 的
     `compute_required_field_set`。
签名与调用方（api/v1、services、tests）完全不变。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domain import overlay as _overlay
# Re-export the engine-free overlay ops verbatim — callers keep using
# `pending_edits_service.X`. Locked-guarded ops are wrapped below.
from app.domain.overlay import (  # noqa: F401
    _empty,
    _normalize,
    _save_overlay,
    cascade_rename_annotations,
    clear_overlay,
    get_overlay,
    get_overlay_by_doc,
    record_field_feedback,
    record_modification,
)

logger = logging.getLogger(__name__)


# ── Country-lock guard (engine knowledge — stays in the service layer) ────────


def _locked_set(db: Session, api_def_id: uuid.UUID) -> set[str]:
    """Country-locked (regulatory, non-modifiable) field names for this ApiDef.

    Customer edits — add / rename / delete / type-format override — are REFUSED
    on these fields (precedence: country-lock > customer-override). Best-effort:
    returns empty set on any resolution error so normal fields never break.
    """
    try:
        from app.ocr_optimizer.service import field_constraints as _fc
        return _fc.locked_fields_for_api(db, api_def_id)
    except Exception:  # noqa: BLE001
        return set()


# ── Locked-guarded writes: resolve the lock set, then delegate to domain ──────


def record_rename(
    db: Session,
    api_def_id: uuid.UUID,
    old_name: str,
    new_name: str,
) -> dict[str, Any]:
    return _overlay.record_rename(
        db, api_def_id, old_name, new_name,
        country_locked=_locked_set(db, api_def_id),
    )


def record_added_field(
    db: Session,
    api_def_id: uuid.UUID,
    field_name: str,
    field_type: str = "string",
    description: str | None = None,
    added_at_doc_id: uuid.UUID | None = None,
    default_value: Any = None,
    *,
    columns: Any = None,
) -> dict[str, Any]:
    return _overlay.record_added_field(
        db, api_def_id, field_name, field_type, description,
        added_at_doc_id, default_value,
        columns=columns,
        country_locked=_locked_set(db, api_def_id),
    )


def record_deleted_field(
    db: Session,
    api_def_id: uuid.UUID,
    field_name: str,
) -> tuple[dict[str, Any], int]:
    return _overlay.record_deleted_field(
        db, api_def_id, field_name,
        country_locked=_locked_set(db, api_def_id),
    )


def record_array_column(
    db: Session,
    api_def_id: uuid.UUID,
    array_field: str,
    *,
    op: str,
    name: str,
    new_name: str | None = None,
    col_type: str | None = None,
) -> dict[str, Any]:
    """数组列级结构编辑（多行明细 P2）：登记 overlay 后立即执行标注级联——
    rename 改整列（跨样本全部行）、delete 删整列标注（均按**当前**列名匹配，
    此前的改名级联已把标注改到新名）。加列无既有标注，不需级联；加后删的
    抵消场景级联是无害 no-op。列随表锁（按数组字段本身判定，预检拒绝）。"""
    locked = _locked_set(db, api_def_id)
    if array_field in locked:
        logger.warning("Refused array-column %s on country-locked array: %r", op, array_field)
        return get_overlay(db, api_def_id)
    overlay = _overlay.record_array_column(
        db, api_def_id, array_field,
        op=op, name=name, new_name=new_name, col_type=col_type,
        country_locked=locked,
    )
    if op == "rename" and new_name:
        _overlay.cascade_rename_array_column(db, api_def_id, array_field, name, new_name)
    elif op == "delete":
        _overlay.delete_array_column_annotations(db, api_def_id, array_field, name)
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
) -> dict[str, Any]:
    """Delegate the overlay write to domain, then run the engine side-effect
    (make the override live on the active version immediately, don't wait for
    the next round). Best-effort — never block the persist."""
    overlay = _overlay.record_field_constraint(
        db, api_def_id, field_name,
        field_type=field_type, strip_chars=strip_chars,
        strip_non_numeric=strip_non_numeric, locked=locked, note=note,
        country_locked=_locked_set(db, api_def_id),
    )
    try:
        from app.ocr_optimizer.service import field_constraints as _fc
        _fc.apply_to_active_version(db, api_def_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_to_active_version failed for %s: %s", api_def_id, exc)
    return overlay


# ── Engine-dependent read: required field set (stays in service layer) ────────


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


# Reserved keys of the annotation/normalized format ([{id,keyName,value,…}]).
# They are NOT extraction fields and must never enter the required-field set —
# a leaked "value" key in particular collapses the whole OCR record in
# document_service._is_leaf_field. (Regression guard for the Phase-26 bug.)
_ANNOTATION_WRAPPER_KEYS = {"id", "keyName", "value", "confidence", "bbox", "bounding_box"}


def apply_draft(
    db: Session,
    api_def_id: uuid.UUID,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Persist a workspace FieldEditorPanel draft into the overlay (design v8
    Phase 10). Dispatches the body's present operations to the record_* helpers
    (which inject country-lock + run engine side-effects), then returns the
    resulting overlay.

    结构第二轮 A3：从 api/v1/api_defs.commit_draft_to_overlay 下沉的 6-case
    分发——它编排的是 facade 级操作（record_rename 注入 locked、
    record_field_constraint 的 apply_to_active_version 副作用），故住在
    service 层而非 domain（domain 保持纯数据、不知 locked / 副作用）。
    路由只剩 access guard + 一行调用。

    Body shape (all keys optional, only present operations apply):
        {
            "old_name": "billFromName",         # rename / value mod
            "new_name": "supplierName",          # rename / add
            "field_type": "string", "description": "...",
            "added_value": "...",                # add intent
            "modification": {"document_id", "field_name", "value"},
            "deleted": true, "field_name": "...",            # delete
            "field_constraint": {"field_name", "type", "strip_chars",
                                 "strip_non_numeric", "locked", "note"},
            "field_feedback": {"field_name", "text"},
        }
    """
    old_name = (body.get("old_name") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    field_type = body.get("field_type") or "string"
    description = body.get("description") or ""

    # Case 1: pure rename (old != new, both present)
    if old_name and new_name and old_name != new_name:
        record_rename(db, api_def_id, old_name, new_name)
        cascade_rename_annotations(db, api_def_id, old_name, new_name)

    # Case 2: add new field (no old_name; new_name + value/desc).
    # columns（多行明细，plan-line-items P0）：field_type='array' 时定义每行列。
    elif new_name and not old_name:
        record_added_field(
            db, api_def_id,
            field_name=new_name,
            field_type=field_type,
            description=description,
            added_at_doc_id=None,
            default_value=body.get("added_value"),
            columns=body.get("columns"),
        )

    # Case 3: value modification (modification block present)
    mod = body.get("modification") or {}
    if mod.get("document_id") and mod.get("field_name") is not None:
        record_modification(
            db, api_def_id,
            document_id=uuid.UUID(str(mod["document_id"])),
            field_name=str(mod["field_name"]),
            new_value=mod.get("value"),
        )

    # Case 4 (Phase 11a): field deletion — cascades across all docs
    if body.get("deleted") and body.get("field_name"):
        record_deleted_field(db, api_def_id, str(body["field_name"]))

    # Case 5: explicit per-field type/format override (customer override).
    # Persisted sticky and enforced through every optimization round +
    # overriding the country template's Part 1.
    fc = body.get("field_constraint") or {}
    if fc.get("field_name"):
        record_field_constraint(
            db, api_def_id,
            field_name=str(fc["field_name"]),
            field_type=fc.get("type"),
            strip_chars=fc.get("strip_chars"),
            strip_non_numeric=fc.get("strip_non_numeric"),
            locked=bool(fc.get("locked", True)),
            note=fc.get("note"),
        )

    # Case 6: per-field free-text USER FEEDBACK (reflection hint, NOT final prompt).
    ff = body.get("field_feedback") or {}
    if ff.get("field_name"):
        record_field_feedback(db, api_def_id, str(ff["field_name"]), ff.get("text"))

    # Case 7（多行明细 P2）: 数组列级结构编辑。
    #   {"array_column": {"array": "detailOfGoodsOrServices", "op": "rename",
    #     "name": "quantity", "new_name": "qty", "type": "number"}}
    ac = body.get("array_column") or {}
    if ac.get("array") and ac.get("op") and ac.get("name"):
        record_array_column(
            db, api_def_id, str(ac["array"]),
            op=str(ac["op"]),
            name=str(ac["name"]),
            new_name=(str(ac["new_name"]) if ac.get("new_name") else None),
            col_type=(str(ac["type"]) if ac.get("type") else None),
        )

    return get_overlay(db, api_def_id)


def _observed_top_level_keys_from_confirmed(
    db: Session,
    api_def_id: uuid.UUID,
    renames: dict[str, str],
) -> list[str]:
    """Top-level FIELD names observed in every CONFIRMED sample's ground truth
    (with renames applied). Confirmed-field parity: a field the customer
    confirmed must never vanish from the required set.

    Source = ground_truth.build() (a nested record `{invoiceNumber: …, …}` whose
    top-level keys ARE the real field names) — NOT ProcessingResult.
    structured_data, which is stored in annotation/normalized format
    `[{id,keyName,value,confidence,bbox}, …]`. Reading structured_data harvested
    those WRAPPER keys (id/keyName/value/…) as if they were fields, polluting the
    required set; the injected `value` field then collapsed new docs' OCR. We
    read GT instead, and also filter the wrapper keys defensively.

    Array/flattened keys ("foo[0]", "a.b") are skipped — top-level only.
    """
    from app.models.document import Document
    from app.ocr_optimizer.service import ground_truth as _gt

    out: list[str] = []
    seen: set[str] = set()
    docs = (
        db.query(Document)
        .filter(Document.api_definition_id == api_def_id)
        .all()
    )
    for d in docs:
        gt = _gt.build(db, d.id)   # {} when the sample has no GT
        if not isinstance(gt, dict) or not gt:
            continue
        for k in gt.keys():
            if not k or "[" in k or "." in k:
                continue
            if k in _ANNOTATION_WRAPPER_KEYS:
                continue
            nk = renames.get(k, k)
            if nk and nk not in seen:
                seen.add(nk)
                out.append(nk)
    return out
