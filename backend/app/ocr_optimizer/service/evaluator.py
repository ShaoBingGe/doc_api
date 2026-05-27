"""
Module-level accuracy evaluator.

Compares an OCR slice against the ground-truth slice for the same json_path.
Returns (matched, field_accuracy, diff_detail).

Tolerance rules (ported from legacy app/optimizers/service.py:_values_match):
  - strings: strip + case-insensitive
  - numbers: strip thousands separators / currency, abs diff < 0.01
  - dates: normalized YYYY-MM-DD
  - dict: recurse on shared keys
  - list: align by index, accuracy = average of element accuracies
"""

from __future__ import annotations

import re
from typing import Any


_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_NUM_CLEAN_RE = re.compile(r"[,，¥$€£￥\s]")


def compare(ocr: Any, gt: Any, schema_fragment: dict | None = None) -> tuple[bool, float, str]:
    """
    Compare ocr vs gt at module granularity.

    Returns:
        matched: True iff field_accuracy >= 0.999
        field_accuracy: 0.0-1.0 (recursive average)
        diff_detail: short human-readable description of mismatch (empty if matched)
    """
    diffs: list[str] = []
    acc = _compare_recursive(ocr, gt, "", diffs)
    matched = acc >= 0.999
    return matched, round(acc, 4), "; ".join(diffs[:5])  # cap diffs to keep short


def _compare_recursive(ocr: Any, gt: Any, path: str, diffs: list[str]) -> float:
    # Both missing → match (1.0)
    if gt is None and ocr is None:
        return 1.0
    # GT missing but OCR has value: extraneous, treat as mismatch
    if gt is None:
        diffs.append(f"{path or 'root'}: GT 缺失但 OCR 有值")
        return 0.0
    # OCR missing but GT has value: hard miss
    if ocr is None:
        diffs.append(f"{path or 'root'}: OCR 输出为空")
        return 0.0

    # dict vs dict
    if isinstance(gt, dict) and isinstance(ocr, dict):
        keys = set(gt.keys()) | set(ocr.keys())
        if not keys:
            return 1.0
        scores = []
        for k in keys:
            sub = _compare_recursive(
                ocr.get(k), gt.get(k), f"{path}.{k}" if path else k, diffs
            )
            scores.append(sub)
        return sum(scores) / len(scores)

    # list vs list
    if isinstance(gt, list) and isinstance(ocr, list):
        if not gt and not ocr:
            return 1.0
        # Align by index up to min length; over-length on either side counts as miss
        max_len = max(len(gt), len(ocr))
        if max_len == 0:
            return 1.0
        scores = []
        for i in range(max_len):
            gt_item = gt[i] if i < len(gt) else None
            ocr_item = ocr[i] if i < len(ocr) else None
            scores.append(
                _compare_recursive(ocr_item, gt_item, f"{path}[{i}]", diffs)
            )
        return sum(scores) / len(scores)

    # scalar comparison
    if _values_match(ocr, gt):
        return 1.0
    diffs.append(f"{path or 'root'}: OCR={_short(ocr)} ≠ GT={_short(gt)}")
    return 0.0


def _values_match(a: Any, b: Any) -> bool:
    """Tolerant scalar comparison."""
    # Direct equality first
    if a == b:
        return True
    # Bool special-case
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    # Date normalization
    if isinstance(a, str) and isinstance(b, str):
        na, nb = _normalize_date(a), _normalize_date(b)
        if na and nb and na == nb:
            return True
        if a.strip().lower() == b.strip().lower():
            return True
    # Numeric tolerance
    try:
        fa = _to_float(a)
        fb = _to_float(b)
        if fa is not None and fb is not None:
            return abs(fa - fb) < 0.01
    except (ValueError, TypeError):
        pass
    return False


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        cleaned = _NUM_CLEAN_RE.sub("", v).strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _normalize_date(s: str) -> str | None:
    m = _DATE_RE.search(s)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _short(v: Any, max_len: int = 60) -> str:
    s = repr(v)
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s
