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
import re
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
        # array_columns（多行明细 P2）：既有数组字段的列级结构编辑。
        # {"<arrayFieldName>": {"added": [{name,type}], "deleted": [name],
        #   "renamed": {old: new}}}
        # 独立于顶层 renames/added/deleted 三映射——那些读取方全按「顶层名」
        # 语义工作，塞 arr[*].col 点路径进去每个读取方都要数组感知，漏一处
        # 即静默 bug；独立键让读取方（customize_fork 数组模块应用点）显式
        # opt-in。fork 应用顺序：renamed → added → deleted。
        "array_columns": {},
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
    for arr, spec in (overlay.get("array_columns") or {}).items():
        if not isinstance(spec, dict):
            continue
        out["array_columns"][str(arr)] = {
            "added": _sanitize_columns(spec.get("added")),
            "deleted": [str(x) for x in (spec.get("deleted") or []) if str(x).strip()],
            "renamed": {str(k): str(v) for k, v in (spec.get("renamed") or {}).items()
                        if str(k).strip() and str(v).strip()},
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


def _sanitize_columns(columns: Any) -> list[dict[str, str]]:
    """规整客户新增数组字段的列定义为 [{name, type}]。

    只接受非空 name；type 缺省 string。非法输入静默丢弃（永不阻塞新增）。
    列表为空时返回 [] —— 表示「裸值数组」（items 为标量），而非无约束数组。
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(columns, list):
        return out
    for c in columns:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "type": str(c.get("type") or "string").strip() or "string"})
    return out


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
    country_locked: Collection[str] = frozenset(),
) -> dict[str, Any]:
    """Register a new field. No-op if already present.

    `columns`（多行明细支持，plan-line-items P0）：仅当 field_type='array' 时
    有意义——`[{name, type}, …]` 定义数组每行对象的列。存进 added_fields 条目，
    由 customize_fork._module_from_add_diff 生成 items schema 与列感知 prompt。
    非 array 字段忽略该参数。"""
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

    entry: dict[str, Any] = {
        "field_name": field_name,
        "type": field_type or "string",
        "description": description or "",
        "added_at_doc_id": str(added_at_doc_id) if added_at_doc_id else None,
        "default_value": default_value,
    }
    if (field_type or "").lower() == "array":
        entry["columns"] = _sanitize_columns(columns)
    overlay["added_fields"].append(entry)
    _save_overlay(db, api_def, overlay)
    db.commit()
    logger.info("Recorded added field on ApiDef %s: %r (columns=%s)",
                api_def_id, field_name, entry.get("columns"))
    return overlay


# ── 数组列级结构编辑（多行明细 P2）───────────────────────────────────────────


def record_array_column(
    db: Session,
    api_def_id: uuid.UUID,
    array_field: str,
    *,
    op: str,
    name: str,
    new_name: str | None = None,
    col_type: str | None = None,
    country_locked: Collection[str] = frozenset(),
) -> dict[str, Any]:
    """对既有数组字段登记一次列级结构编辑（op: add / delete / rename）。

    语义（fork 应用顺序 renamed → added → deleted，见 _empty 注释）：
      - add：加入 added（同名幂等）；若列在 deleted 里 → 撤销删除（加回）。
      - delete：加入 deleted；若列是本次会话新增的（在 added 里）→ 双向抵消
        （从 added 移除、不进 deleted）；清理指向它的 rename 记录。
      - rename：链坍缩同顶层 renames（A→B 再 B→C 存 A→C，回环删条目）；
        若被改名的列是本次新增的 → 直接改 added 条目名，不留 rename 记录。

    `country_locked` 按**数组字段本身**判定（列随表锁）。级联标注由调用方
    （service facade）在登记成功后执行——本函数只写 overlay。
    """
    arr = (array_field or "").strip()
    col = (name or "").strip()
    if not arr or not col or op not in ("add", "delete", "rename"):
        return get_overlay(db, api_def_id)

    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")

    if arr in country_locked:
        logger.warning("Refused array-column %s on country-locked array: %r", op, arr)
        return _normalize(api_def.pending_edits)

    overlay = _normalize(api_def.pending_edits)
    spec = overlay["array_columns"].setdefault(
        arr, {"added": [], "deleted": [], "renamed": {}},
    )

    if op == "add":
        if col in spec["deleted"]:
            spec["deleted"].remove(col)  # 撤销删除
        elif all(c["name"] != col for c in spec["added"]):
            spec["added"].append({
                "name": col,
                "type": (col_type or "string").strip() or "string",
            })

    elif op == "delete":
        added_names = {c["name"] for c in spec["added"]}
        if col in added_names:
            spec["added"] = [c for c in spec["added"] if c["name"] != col]  # 加后删=抵消
        else:
            # 删除的可能是「改名后的新名」——deleted 必须携带 schema 里的
            # 真实原列名（fork 按 renamed→added→deleted 应用，rename 条目
            # 即将被清理，留新名会让原列在 schema 里删不掉）。链坍缩回原名。
            orig = col
            for src, dst in list(spec["renamed"].items()):
                if dst == col:
                    orig = src
                    del spec["renamed"][src]
                    break
            if orig not in spec["deleted"]:
                spec["deleted"].append(orig)

    elif op == "rename":
        new = (new_name or "").strip()
        if not new or new == col:
            return overlay
        added_names = {c["name"] for c in spec["added"]}
        if col in added_names:
            # 本次新增的列改名：直接改条目，不留 rename 记录
            for c in spec["added"]:
                if c["name"] == col:
                    c["name"] = new
        else:
            # 链坍缩：A→B 再 B→C 存 A→C；改回原名删条目
            collapsed_old = col
            for src, dst in list(spec["renamed"].items()):
                if dst == col:
                    collapsed_old = src
                    del spec["renamed"][src]
                    break
            if collapsed_old != new:
                spec["renamed"][collapsed_old] = new

    # 空 spec 清理（全部操作抵消后不留空壳）
    if not spec["added"] and not spec["deleted"] and not spec["renamed"]:
        overlay["array_columns"].pop(arr, None)

    _save_overlay(db, api_def, overlay)
    db.commit()
    logger.info(
        "Recorded array-column %s on ApiDef %s: %s.%s%s",
        op, api_def_id, arr, col, f" → {new_name}" if op == "rename" else "",
    )
    return overlay


def _array_cell_annotations(
    db: Session, api_def_id: uuid.UUID, array_field: str, col: str,
) -> list[Annotation]:
    """该 ApiDef 全部文档中，字段名形如 `{arr}[N].{col}` 的标注行。

    LIKE 初筛 + Python 正则精确复核——防止同前缀字段误伤
    （`items` vs `itemsTotal`、列名 `qty` vs `qtyUnit`）。
    """
    doc_ids = [d.id for d in db.query(Document.id).filter(
        Document.api_definition_id == api_def_id
    ).all()]
    if not doc_ids:
        return []
    rows = (
        db.query(Annotation)
        .filter(
            Annotation.document_id.in_(doc_ids),
            Annotation.field_name.like(f"{array_field}[%"),
        )
        .all()
    )
    pat = re.compile(rf"^{re.escape(array_field)}\[\d+\]\.{re.escape(col)}$")
    return [a for a in rows if pat.match(a.field_name or "")]


def cascade_rename_array_column(
    db: Session, api_def_id: uuid.UUID, array_field: str,
    old: str, new: str,
) -> int:
    """列改名级联：把 `{arr}[N].{old}` 全部改写为 `{arr}[N].{new}`（整列、
    跨全部样本）。修正「列改名半失效」缺陷——此前单元格改名只动一行标注、
    schema 纹丝不动。返回改写行数。"""
    if not old or not new or old == new:
        return 0
    n = 0
    for ann in _array_cell_annotations(db, api_def_id, array_field, old):
        prefix = ann.field_name.rsplit(".", 1)[0]  # "{arr}[N]"
        ann.field_name = f"{prefix}.{new}"
        n += 1
    db.commit()
    logger.info(
        "Cascaded array-column rename on ApiDef %s: %s.[*].%s → %s touched %d annotations",
        api_def_id, array_field, old, new, n,
    )
    return n


def delete_array_column_annotations(
    db: Session, api_def_id: uuid.UUID, array_field: str, col: str,
) -> int:
    """列删除级联：删除 `{arr}[N].{col}` 全部标注行（跨样本）。返回删除行数。"""
    rows = _array_cell_annotations(db, api_def_id, array_field, col)
    for ann in rows:
        db.delete(ann)
    db.commit()
    if rows:
        logger.info(
            "Deleted array-column annotations on ApiDef %s: %s.[*].%s → %d rows",
            api_def_id, array_field, col, len(rows),
        )
    return len(rows)


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
