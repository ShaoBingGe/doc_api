"""
Module-level accuracy evaluator.

Compares an OCR slice against the ground-truth slice for the same json_path.
Returns (matched, field_accuracy, diff_detail).

Tolerance rules（批次4 修正版）：
  - strings: strip + case-insensitive
  - numbers: strip thousands separators / currency, 解析归一后精确相等
    （仅浮点表示级 epsilon；历史绝对容差 0.01 会放过税率/汇率/单价级差异，
    也会放过大金额的尾数识别错）
  - dates: **全串**归一为 YYYY-MM-DD，支持 YMD 与 DMY/MDY 格式族
    （历史 `search` 子串匹配会把「抓了整行」判为正确——最典型的提取错误
    被打分系统漂白，反思回路永远收不到信号）
  - bools: 语义比较（true/yes/1 ↔ True；历史 truthiness 让 GT=True vs
    OCR="no" 判对，布尔字段全盲）
  - dict: recurse on key union
  - list: 内容贪心对齐（历史按 index 硬对齐：一行多提/漏提 → 后续所有行
    整体错位记 0，optimizer 收到一批假 diff）
"""

from __future__ import annotations

import re
from typing import Any


# 全串日期形状（^…$）：YYYY-MM-DD / YYYY/M/D / YYYY.M.D / 2025年5月2日
_DATE_YMD_RE = re.compile(r"^\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*$")
# DD/MM/YYYY 或 MM/DD/YYYY（歧义时两种解释都作为候选）
_DATE_DMY_RE = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$")
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

    # list vs list — 内容贪心对齐
    if isinstance(gt, list) and isinstance(ocr, list):
        return _compare_lists(ocr, gt, path, diffs)

    # scalar comparison
    if _values_match(ocr, gt):
        return 1.0
    diffs.append(f"{path or 'root'}: OCR={_short(ocr)} ≠ GT={_short(gt)}")
    return 0.0


def _compare_lists(ocr: list, gt: list, path: str, diffs: list[str]) -> float:
    """按内容贪心对齐两个数组后打分。

    历史 bug：按 index 硬对齐——OCR 把第 1 行拆成两行（或漏提一行）后，
    后续所有行整体错位记 0，模块分从 ~0.9 崩到接近 0，optimizer 收到一批
    「内容全错」的假 diff 并据此改坏本来正确的 prompt。

    做法：算全部 (gt_i, ocr_j) 相似度 → 按 (相似度降序, 序距 |i-j| 升序)
    贪心配对 → 配对分求和 / max(len)。未配对的 GT 行记「漏提取」、OCR 行
    记「多提取」（各自计 0 分于分母中）。纯内容对齐 —— 行序不同不扣分
    （提取质量以内容为准；顺序由 Part 3 输出契约约束）。
    """
    if not gt and not ocr:
        return 1.0
    denom = max(len(gt), len(ocr))

    # 相似度矩阵（scratch diffs 丢弃）
    candidates: list[tuple[float, int, int, int]] = []
    for i, g in enumerate(gt):
        for j, o in enumerate(ocr):
            scratch: list[str] = []
            s = _compare_recursive(o, g, f"{path}[{i}]", scratch)
            candidates.append((s, abs(i - j), i, j))
    # 高相似优先；同分优先保持原序（|i-j| 小者先）；再按 i 稳定
    candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3]))

    used_g: set[int] = set()
    used_o: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for s, _, i, j in candidates:
        if i in used_g or j in used_o:
            continue
        used_g.add(i)
        used_o.add(j)
        pairs.append((i, j))

    total = 0.0
    for i, j in sorted(pairs):
        total += _compare_recursive(ocr[j], gt[i], f"{path}[{i}]", diffs)
    for i in range(len(gt)):
        if i not in used_g:
            diffs.append(f"{path}[{i}]: 漏提取（GT 有该记录，OCR 无匹配）")
    for j in range(len(ocr)):
        if j not in used_o:
            diffs.append(f"{path}[+{j}]: 多提取（OCR 记录无 GT 对应）")
    return total / denom


_TRUE_STRS = {"true", "yes", "y", "1", "是"}
_FALSE_STRS = {"false", "no", "n", "0", "否"}


def _as_bool(v: Any) -> bool | None:
    """把值解释为布尔语义；不可解释返回 None。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE_STRS:
            return True
        if s in _FALSE_STRS:
            return False
    return None


def _values_match(a: Any, b: Any) -> bool:
    """Tolerant scalar comparison."""
    # Direct equality first
    if a == b:
        return True
    # Bool：语义比较。历史 truthiness（bool("no")=True）让布尔字段全盲。
    if isinstance(a, bool) or isinstance(b, bool):
        ba, bb = _as_bool(a), _as_bool(b)
        return ba is not None and bb is not None and ba == bb
    # Date normalization（全串，多格式族；候选集相交即匹配）
    if isinstance(a, str) and isinstance(b, str):
        da, db = _date_candidates(a), _date_candidates(b)
        if da and db and (da & db):
            return True
        if a.strip().lower() == b.strip().lower():
            return True
    # Numeric：解析归一后精确相等（仅浮点表示级 epsilon）。
    # 绝对容差 0.01 是双向失真：放过 0.06 vs 0.065（税率/汇率/单价），
    # 也放过 123456.78 vs 123456.784（大金额尾数识别错）。
    try:
        fa = _to_float(a)
        fb = _to_float(b)
        if fa is not None and fb is not None:
            tol = max(1e-9, 1e-9 * max(abs(fa), abs(fb)))
            return abs(fa - fb) <= tol
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


def _date_candidates(s: str) -> set[str]:
    """把**整个字符串**解析为日期候选集（ISO 格式）。

    历史 bug：`_DATE_RE.search` 在字符串里搜日期形状子串——OCR 抓了整行
    （"Date: 2025-05-02 Due: 2025-06-01"）也会与 GT "2025-05-02" 判相等，
    「抓多了」这类最典型错误被漂白。现在必须全串是一个日期才参与匹配。

    DMY/MDY 歧义（02/05/2025）时两种解释都进候选集，两侧候选集相交即
    匹配——GT 标 ISO、票面是 DD/MM/YYYY 的国家（如 MY）不再恒判错。
    """
    out: set[str] = set()
    m = _DATE_YMD_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.add(f"{y}-{mo:02d}-{d:02d}")
        return out
    m = _DATE_DMY_RE.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= b <= 12 and 1 <= a <= 31:   # DMY 解释
            out.add(f"{y}-{b:02d}-{a:02d}")
        if 1 <= a <= 12 and 1 <= b <= 31:   # MDY 解释
            out.add(f"{y}-{a:02d}-{b:02d}")
    return out


def _short(v: Any, max_len: int = 60) -> str:
    s = repr(v)
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s
