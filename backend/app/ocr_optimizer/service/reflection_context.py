"""反思语料收集（customer_iteration 拆分第二刀）.

反思层（reflection/reflector）需要的两类证据语料，全部为**纯读 DB**：

  - `_build_cross_doc_context_for_diffs` —— Phase 14b 跨样本对照：每个被
    编辑字段在全部已审视样本中的实际值 + bbox + GT 标记（Phase 15 去重）；
  - `_build_sample_outputs` —— 每个样本最新一次 OCR 的完整 structured_data，
    供 RETARGET 全文检索（客户期望值实际藏在哪个字段下）。

函数名保持原样（含下划线），customer_iteration 作 facade 重导出。
"""

from __future__ import annotations

import logging
import re as _re
import uuid

from sqlalchemy.orm import Session

from app.models.api_definition import ApiDefinition

logger = logging.getLogger(__name__)


def _build_cross_doc_context_for_diffs(
    db: Session,
    api_def_id: uuid.UUID,
    diffs: list[dict],
) -> dict[str, list[dict]]:
    """Phase 14b — collect each diff's field-name across every confirmed
    sample of the ApiDef.

    For each diff (edit/add/rename), produce a list of {doc_id, doc_filename,
    value, is_corrected, bbox} for every annotation matching either the
    original_name or corrected_name. The reflection agent uses this to
    compare values/formatting across the 3 invoices.
    """
    from app.models.annotation import Annotation as _Annotation
    from app.models.document import Document as _Document

    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        return {}
    sample_ids = (api_def.config or {}).get("sample_document_ids") or []
    if not sample_ids:
        return {}

    # Collect the field names we care about (both old and new forms).
    # 多行明细 P3：数组单元格名（arr[N].col）不再跳过——解析出 (arr, col)，
    # 为该**列**收集全部样本、全部行的值作跨样本对照（历史缺陷：含 [/. 的
    # 名字直接 skip，数组列的修正拿不到任何跨样本语料，反思凭单 cell 猜）。
    field_names: set[str] = set()
    array_cols: dict[str, tuple[str, str]] = {}   # diff 原名 → (arr, col)
    _cell_re = _re.compile(r"^([A-Za-z0-9_]+)\[\d+\]\.([A-Za-z0-9_]+)$")
    for d in diffs:
        for k in ("original_name", "corrected_name"):
            v = (d.get(k) or "").strip()
            if not v:
                continue
            if "[" not in v and "." not in v:
                field_names.add(v)
            else:
                m = _cell_re.match(v)
                if m:
                    array_cols[v] = (m.group(1), m.group(2))
    if not field_names and not array_cols:
        return {}

    # Resolve docs once
    doc_uuids: list[uuid.UUID] = []
    for s in sample_ids:
        try:
            doc_uuids.append(uuid.UUID(str(s)))
        except Exception:  # noqa: BLE001
            continue
    if not doc_uuids:
        return {}

    docs = db.query(_Document).filter(_Document.id.in_(doc_uuids)).all()
    doc_by_id = {d.id: d for d in docs}

    out: dict[str, list[dict]] = {f: [] for f in field_names}
    rows = (
        db.query(_Annotation)
        .filter(
            _Annotation.document_id.in_(doc_uuids),
            _Annotation.field_name.in_(field_names),
        )
        .all()
    )
    for ann in rows:
        doc = doc_by_id.get(ann.document_id)
        out[ann.field_name].append({
            "doc_id": str(ann.document_id),
            "doc_filename": (doc.filename if doc else None) or str(ann.document_id),
            "value": ann.field_value,
            "is_corrected": bool(ann.is_corrected),
            "bbox": ann.bounding_box,
        })

    # 多行明细 P3 — 数组列的跨样本收集：对每个 (arr, col)，取全部样本中
    # `arr[N].col` 的所有行值（LIKE 初筛 + 正则精确复核防同前缀误伤），
    # 语料按 diff 的原始 cell 名（arr[0].qty）挂载——反思器按 diff 名查找。
    # 走同一 dedup（同值行跨行/跨样本折叠为 × N），避免长表撑爆 prompt。
    for diff_name, (arr, col) in array_cols.items():
        col_rows = (
            db.query(_Annotation)
            .filter(
                _Annotation.document_id.in_(doc_uuids),
                _Annotation.field_name.like(f"{arr}[%"),
            )
            .all()
        )
        pat = _re.compile(rf"^{_re.escape(arr)}\[\d+\]\.{_re.escape(col)}$")
        bucket = out.setdefault(diff_name, [])
        for ann in col_rows:
            if not pat.match(ann.field_name or ""):
                continue
            doc = doc_by_id.get(ann.document_id)
            bucket.append({
                "doc_id": str(ann.document_id),
                "doc_filename": (doc.filename if doc else None) or str(ann.document_id),
                "value": ann.field_value,
                "is_corrected": bool(ann.is_corrected),
                "bbox": ann.bounding_box,
            })

    # Phase 15 — dedup duplicate values within each field's sample list.
    # If two docs show the same (value, is_corrected) tuple, we collapse
    # them and annotate "× N docs" so the LLM doesn't see the same data
    # point repeated. Preserves the first occurrence's doc_filename + bbox.
    deduped: dict[str, list[dict]] = {}
    for field, samples in out.items():
        if not samples:
            continue
        # Key by (value, is_corrected) — bbox usually varies across docs
        # even when value is identical, so we keep the first one seen.
        by_key: dict[tuple, dict] = {}
        for s in samples:
            key = (
                # repr handles None / strings / numbers consistently
                repr(s.get("value")),
                bool(s.get("is_corrected")),
            )
            if key in by_key:
                by_key[key]["dup_count"] = by_key[key].get("dup_count", 1) + 1
                # accumulate doc filenames for transparency
                other_files = by_key[key].setdefault("dup_doc_filenames", [])
                other_files.append(s.get("doc_filename"))
            else:
                by_key[key] = dict(s)
        deduped[field] = list(by_key.values())
    return deduped


def _build_sample_outputs(db: Session, api_def_id: uuid.UUID) -> dict[str, dict]:
    """收集 ApiDef 每个样本最新一次 OCR 的完整 structured_data。

    供反思层做「全文检索」（edit_intent.search_value_in_outputs）：客户填写
    的正确值若出现在输出 JSON 的其他字段下，说明当前规则抓错了来源——检索
    命中路径直接揭示真实锚点。键用文件名（比 UUID 对 LLM 可读）。
    """
    from app.models.document import Document as _Document, ProcessingResult as _PR

    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        return {}
    sample_ids: list[uuid.UUID] = []
    for s in (api_def.config or {}).get("sample_document_ids") or []:
        try:
            sample_ids.append(uuid.UUID(str(s)))
        except Exception:  # noqa: BLE001
            continue
    if not sample_ids:
        return {}

    docs = db.query(_Document).filter(_Document.id.in_(sample_ids)).all()
    out: dict[str, dict] = {}
    for doc in docs:
        pr = (
            db.query(_PR)
            .filter(_PR.document_id == doc.id)
            .order_by(_PR.version.desc())
            .first()
        )
        data = pr.structured_data if pr else None
        if data is None:
            continue
        label = doc.filename or str(doc.id)
        # 同名文件去重：后缀编号，保证每个样本都进语料
        if label in out:
            label = f"{label}#{str(doc.id)[:6]}"
        out[label] = data
    return out
