"""OCR 输出后处理纯函数（结构第二轮 A2）.

把 LLM 的层级化 OCR 输出规整为平台标准的 structured_data 形状
`[{id, keyName, value, confidence, bbox}, …]`，并做投影 / 补齐 / 改键。

**依赖图的中立叶子层**：只依赖标准库（re / uuid），零 DB、零 I/O、
零 app.services、零 ocr_optimizer——因此可被两侧安全依赖，也让这批
load-bearing 的数据变换首次可独立单测（此前埋在 document_service 1079 行里）。

带引擎/DB 依赖的后处理留在 `document_service`：
  - `_apply_field_constraints`（调 ocr_optimizer.field_constraints）
  - `_create_annotations`（写 Annotation 行）
  - `reprocess_document`（编排 OCR——引擎侧 re-OCR 经它，属合法的
    engine→document 原语，见 test_dependency_direction 白名单）。
"""

from __future__ import annotations

import re
import uuid


def normalize_bbox(bbox: dict | None) -> dict | None:
    """Coerce LLM bbox output to {x, y, width, height, page} in 0-100 range.

    Some Gemini outputs use 0-1000 coords; if any value > 100, divide by 10.
    """
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x", 0))
        y = float(bbox.get("y", 0))
        w = float(bbox.get("width", bbox.get("w", 0)))
        h = float(bbox.get("height", bbox.get("h", 0)))
    except (TypeError, ValueError):
        return None
    if max(x, y, w, h) > 100:
        x, y, w, h = x / 10, y / 10, w / 10, h / 10
    page_val = bbox.get("page", 1)
    try:
        page = int(page_val) if page_val is not None else 1
    except (TypeError, ValueError):
        page = 1
    return {
        "x": max(0.0, min(100.0, x)),
        "y": max(0.0, min(100.0, y)),
        "width": max(0.0, min(100.0, w)),
        "height": max(0.0, min(100.0, h)),
        "page": page,
    }


_LEAF_SHAPE_KEYS = {"value", "confidence", "bbox", "bounding_box"}


def is_leaf_field(v) -> bool:
    """Hierarchical leaf shape: {value, confidence?, bbox?}.

    Must contain `value` AND have ONLY leaf-shape keys. The extra-key check is a
    hard guard: without it, a real extraction RECORD that merely contains a
    field literally named `value` (e.g. a customer-added field) was misread as a
    single leaf, collapsing the ENTIRE record to one null entry and wiping the
    whole field set. A genuine record carries other keys (invoiceNumber, …), so
    it now correctly recurses instead of collapsing.
    """
    if not (isinstance(v, dict) and "value" in v and not isinstance(v.get("value"), dict)):
        return False
    return set(v.keys()) <= _LEAF_SHAPE_KEYS


def flatten_hierarchical(node, path: str, out: list[dict]) -> None:
    """Recursively walk the hierarchical Gemini output and emit flat entries."""
    if node is None:
        return

    # Leaf: { value, confidence, bbox }
    if is_leaf_field(node):
        out.append({
            "id": str(uuid.uuid4()),
            "keyName": path or "field",
            "value": node.get("value"),
            "confidence": node.get("confidence"),
            "bbox": normalize_bbox(node.get("bbox") or node.get("bounding_box")),
        })
        return

    # Dict container: recurse into each key (skip _meta, capture container bbox if present).
    if isinstance(node, dict):
        # Table-shaped container { _meta, rows: [...] }
        rows = node.get("rows")
        if isinstance(rows, list):
            for i, row in enumerate(rows):
                flatten_hierarchical(row, f"{path}[{i}]" if path else f"[{i}]", out)
            return

        for key, val in node.items():
            if key == "_meta":
                continue
            # Some hierarchical leaves might have value as a nested dict (rare); recurse.
            if "value" in node and key == "value":
                continue
            child_path = f"{path}.{key}" if path else key
            flatten_hierarchical(val, child_path, out)
        return

    # List of items (table without _meta wrapper, or bare array)
    if isinstance(node, list):
        for i, item in enumerate(node):
            flatten_hierarchical(item, f"{path}[{i}]" if path else f"[{i}]", out)
        return

    # Bare scalar — record as a value-only entry
    out.append({
        "id": str(uuid.uuid4()),
        "keyName": path or "field",
        "value": node,
        "confidence": None,
        "bbox": None,
    })


def normalize_structured_data(raw: dict | list) -> list[dict]:
    """
    Normalize AI processor output to design format:
      [{id, keyName, value, confidence, bbox}, ...]

    Recursively descends into hierarchical Gemini output so every leaf field
    (e.g. "seller.name", "line_items[0].description") gets its own entry with
    its own bbox preserved.
    """
    # Pre-structured list with `keyName` items — keep as-is, just normalize bbox.
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "keyName" in raw[0]:
        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
            entry.setdefault("confidence", None)
            entry["bbox"] = normalize_bbox(entry.get("bbox") or entry.get("bounding_box"))
            result.append(entry)
        return result

    out: list[dict] = []
    flatten_hierarchical(raw, "", out)
    return out


def field_top_level(key_name: str) -> str:
    """Top-level field token of a flattened keyName.

    'docType' → 'docType'; 'page[0]' → 'page'; 'billFrom.name' → 'billFrom';
    'detailOfGoodsOrServices[0].description' → 'detailOfGoodsOrServices'.
    """
    if not key_name:
        return ""
    return re.split(r"[.\[]", key_name, maxsplit=1)[0]


def project_to_field_set(
    structured_data: list[dict] | dict, allowed: list[str],
) -> list[dict] | dict:
    """Drop fields the LLM produced that are NOT part of the API's contract.

    The OCR model is not hard-bound to the API's schema, so a powerful VLM
    (qwen3-vl-plus) free-forms the *full* canonical invoice — e.g. a JP API
    pruned to 8 fields still gets billFrom.* / detailOfGoodsOrServices[*] /
    bankAccount.* / issueDate / dueDate … back (97 leaves). Projection makes
    the stored result + annotations honor the defined field set.

    `allowed` is `compute_required_field_set` (modules + user-added +
    confirmed-observed − deleted), so this only removes brand-new free-formed
    junk; modules, user-added, and previously-confirmed fields are preserved
    (keeps the monotonic cross-sample parity guarantee intact).

    Shapes:
      - normalized leaf-list [{keyName, value, …}] → keep records whose
        top-level token ∈ allowed.
      - record-dict {field: value} → keep keys whose top-level token ∈ allowed.

    No-op when `allowed` is empty (unbound doc / no active version).
    """
    if not allowed:
        return structured_data
    allowed_set = set(allowed)

    def _project_record(rec: dict) -> dict:
        # field→value record (no keyName wrapper): filter its keys.
        out = {}
        for k, v in rec.items():
            if field_top_level(k) in allowed_set:
                out[k] = v
        return out

    if isinstance(structured_data, list):
        # Leaf-list shape: each item has a 'keyName'.
        if structured_data and isinstance(structured_data[0], dict) and "keyName" in structured_data[0]:
            return [
                r for r in structured_data
                if isinstance(r, dict) and field_top_level(r.get("keyName", "")) in allowed_set
            ]
        # Record-list shape: filter keys of each record.
        return [_project_record(r) if isinstance(r, dict) else r for r in structured_data]
    if isinstance(structured_data, dict):
        return _project_record(structured_data)
    return structured_data


def pad_with_required_keys(
    structured_data: list[dict] | dict, required: list[str],
) -> list[dict] | dict:
    """N4 — guarantee every required field appears as a top-level key in
    structured_data, with null value when the LLM omitted it.

    structured_data shape after normalize: usually list[dict] where each
    dict is one extracted record (e.g. one invoice). We pad each record
    independently so cross-sample parity is enforced per-record.

    Idempotent: re-applying with the same required-list is a no-op.
    """
    if not required:
        return structured_data

    required_set = list(required)

    def _pad_one(rec: dict) -> dict:
        if not isinstance(rec, dict):
            return rec
        out = dict(rec)
        for key in required_set:
            if key not in out:
                out[key] = None
        return out

    if isinstance(structured_data, list):
        return [_pad_one(r) for r in structured_data]
    if isinstance(structured_data, dict):
        return _pad_one(structured_data)
    return structured_data


def rewrite_structured_data_keys(
    structured_data: list[dict] | dict, renames: dict[str, str],
) -> list[dict] | dict:
    """Phase 23.3 — rewrite TOP-LEVEL keys of structured_data according
    to renames {old: new}. Used post-customize to bring every doc's
    cached OCR output in line with the new module key names.

    Idempotent: applying twice is a no-op. Top-level only — nested
    objects (e.g. array items in detailOfGoodsOrServices) are NOT
    touched, since overlay renames model top-level fields.
    """
    if not renames:
        return structured_data

    def _rewrite_one(rec: dict) -> dict:
        if not isinstance(rec, dict):
            return rec
        out: dict = {}
        for k, v in rec.items():
            out[renames.get(k, k)] = v
        return out

    if isinstance(structured_data, list):
        return [_rewrite_one(r) for r in structured_data]
    if isinstance(structured_data, dict):
        return _rewrite_one(structured_data)
    return structured_data
