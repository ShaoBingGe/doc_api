"""
Ground-truth quality validation for a customer's confirmed samples.

The customer iteration trusts human-reviewed GT as the optimization target
(CLAUDE.md §2.3 客户回路). Garbage GT silently caps accuracy — a null GT field
the model also returns null scores a false pass; an intra-doc contradiction
makes a field unlearnable. This module surfaces such issues as warnings BEFORE
they poison the iteration, without ever auto-editing the customer's data.

Pure read-only. Country-agnostic: it inspects structure (empty / duplicate /
type / missing-anchor), not country-specific field semantics.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.annotation import Annotation, AnnotationSource

# Fields that, when absent from an invoice/receipt GT, are worth flagging — a
# minimal universal set (not country-specific). docType anchors the whole row;
# currency is required by the global output contract for invoice/receipt.
_EXPECTED_CORE = ("docType",)
# Fields a single vendor usually keeps constant across their invoices — a
# divergence is allowed (multi-currency vendors exist) but worth an info note.
_CROSS_SAMPLE_CONSISTENT = ("currency",)


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    return False


def _is_number(v: str) -> bool:
    try:
        float(str(v).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def _base_field(field_name: str) -> str:
    """'detailOfGoodsOrServices[0].netAmount' → 'netAmount'; 'page[0]' → 'page'."""
    leaf = field_name.split(".")[-1]
    return leaf.split("[")[0]


def validate_gt_quality(db: Session, api_definition_id: uuid.UUID) -> dict:
    """Validate the GT of an ApiDef's confirmed samples.

    Returns:
        {
          "ok": bool,                         # no error-level warnings
          "checked_samples": int,
          "counts": {"error": n, "warning": n, "info": n},
          "warnings": [ {level, doc_id?, field?, message}, ... ],
        }
    """
    from app.models.api_definition import ApiDefinition
    from app.models.document import Document

    warnings: list[dict] = []

    api = db.get(ApiDefinition, api_definition_id)
    if not api:
        return {"ok": True, "checked_samples": 0,
                "counts": {"error": 0, "warning": 0, "info": 0}, "warnings": []}

    sample_ids = (api.config or {}).get("sample_document_ids") or []
    doc_uuids: list[uuid.UUID] = []
    for s in sample_ids:
        try:
            doc_uuids.append(uuid.UUID(str(s)))
        except Exception:  # noqa: BLE001
            continue

    filenames: dict[uuid.UUID, str] = {}
    if doc_uuids:
        for d in db.query(Document).filter(Document.id.in_(doc_uuids)).all():
            filenames[d.id] = d.filename or str(d.id)

    # currency-like fields observed across samples → cross-sample consistency
    cross: dict[str, set[str]] = defaultdict(set)
    checked = 0

    for did in doc_uuids:
        anns = (
            db.query(Annotation)
            .filter(Annotation.document_id == did)
            .filter(or_(Annotation.is_corrected.is_(True),
                        Annotation.source == AnnotationSource.manual.value))
            .all()
        )
        if not anns:
            warnings.append({
                "level": "warning", "doc_id": str(did),
                "field": None,
                "message": f"样本「{filenames.get(did, did)}」无任何已审视 GT 字段。",
            })
            continue
        checked += 1

        seen: dict[str, list[str]] = defaultdict(list)
        present_base: set[str] = set()
        for a in anns:
            base = _base_field(a.field_name or "")
            present_base.add(base)
            val = a.field_value
            # 1) empty GT leaf
            if _is_empty(val):
                warnings.append({
                    "level": "warning", "doc_id": str(did), "field": a.field_name,
                    "message": f"字段「{a.field_name}」被标为 GT 但值为空——空值 GT 会造成假通过，请补值或删除。",
                })
                continue
            # 2) declared number but non-numeric value
            if (a.field_type or "") == "number" and not _is_number(val):
                warnings.append({
                    "level": "error", "doc_id": str(did), "field": a.field_name,
                    "message": f"字段「{a.field_name}」声明为数值类型，但 GT 值 {val!r} 非数字。",
                })
            seen[a.field_name].append(str(val))
            if base in _CROSS_SAMPLE_CONSISTENT:
                cross[base].add(str(val).strip())

        # 3) intra-doc contradiction: same field path, conflicting values
        for fname, vals in seen.items():
            distinct = set(vals)
            if len(distinct) > 1:
                warnings.append({
                    "level": "error", "doc_id": str(did), "field": fname,
                    "message": f"字段「{fname}」在同一样本内出现矛盾的多个 GT 值：{sorted(distinct)}。",
                })

        # 4) missing universal core field
        for core in _EXPECTED_CORE:
            if core not in present_base:
                warnings.append({
                    "level": "warning", "doc_id": str(did), "field": core,
                    "message": f"样本「{filenames.get(did, did)}」缺少核心字段「{core}」的 GT。",
                })

    # 5) cross-sample consistency hint (info level — divergence may be legit)
    for base, values in cross.items():
        if len(values) > 1:
            warnings.append({
                "level": "info", "doc_id": None, "field": base,
                "message": f"字段「{base}」在多个样本间取值不一致：{sorted(values)}——"
                           f"若非多币种业务，请核对是否有样本标错。",
            })

    counts = {"error": 0, "warning": 0, "info": 0}
    for w in warnings:
        counts[w["level"]] = counts.get(w["level"], 0) + 1

    return {
        "ok": counts["error"] == 0,
        "checked_samples": checked,
        "counts": counts,
        "warnings": warnings,
    }
