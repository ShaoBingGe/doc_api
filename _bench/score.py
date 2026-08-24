"""GT 锚定打分器 — 把黄金集的杂乱 GT 归一到 MY.yaml 的字段词汇再逐字段比对。

打分原则（A/B 两版共用，保证公平）:
  1. 只考 GT 里真实有值的字段；GT 缺失 → 该 (文档,字段) 不计分。
  2. 税号类 GT 标签不可信（salerTIN 里装 BRN、billFromTaxIdentificationNumber
     里混 SST）→ 按值的**格式**路由到 BRN / TIN，不按标签。
  3. 归一化比较：数字容差 0.01；日期统一 YYYY-MM-DD；单号去常见前缀与空格；
     名称去公司后缀噪声后比对。
  4. 明细行按「集合匹配」计分：每条 GT 行找最佳预测行，分数 = 命中子字段占比。
"""
from __future__ import annotations

import json
import pathlib
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLD = ROOT / "backend/app/ocr_optimizer/eval/golden_set/MY"
OUT = ROOT / "_bench/results"

# GT 别名 → MY.yaml 字段名（仅合并语义确定的同义命名）
ALIAS = {
    "invoiceNumber": ["invoiceNumber"],
    "invoiceDate": ["invoiceDate"],
    "totalAmount": ["totalAmount"],
    "totalNetAmount": ["totalNetAmount"],
    "totalTaxAmount": ["totalTaxAmount"],
    "currency": ["currency"],
    "invoiceType": ["invoiceType"],
    "docType": ["docType"],
    "billToName": ["billToName", "buyerName", "customerName", "buyerCompany",
                   "billToCompanyName", "billTo"],
    "billFromName": ["billFromName", "salerName", "vendorName", "salerCompany",
                     "billFromCompanyName", "billFrom"],
    "purchaseOrderNumber": ["purchaseOrderNumber", "PO"],
    "deliveryOrderNumber": ["deliveryOrderNumber", "DO"],
}
# 税号：候选 GT 键（值按格式路由）
TAXID_KEYS = [
    "salerTAXNO", "salerTaxNO", "salerTIN", "vendorTaxId", "vendorRegistrationId",
    "billFromBusinessRegistrationNumber", "billFromTaxIdentificationNumber",
]
BRN_RE = re.compile(r"^\d{6,12}\s*(\(.*\))?$|^\d{4,7}-[A-Z]$", re.I)
TIN_RE = re.compile(r"^C\d{10,11}$", re.I)

LINE_SUBFIELDS = ["quantity", "unitPrice", "netAmount", "description"]


# ── 归一化 ───────────────────────────────────────────────────────────────────

def norm_num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d.\-]", "", v.replace(",", ""))
        try:
            return float(s)
        except ValueError:
            return None
    return None


def norm_date(v):
    if not isinstance(v, str):
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return v.strip()


def norm_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    s = str(v).upper()
    s = re.sub(r"[^A-Z0-9一-鿿]+", "", s)
    return s


def norm_id(v):
    """单号：去 PO-/DO-/NO. 等前缀与全部分隔符。"""
    if isinstance(v, list):
        v = ";".join(str(x) for x in v)
    s = str(v or "").upper()
    s = re.sub(r"\b(P/?O|D/?O|NO|NUMBER|REF)\b[.:\-\s]*", "", s)
    return re.sub(r"[^A-Z0-9]+", "", s)


def norm_name(v):
    """公司名：去后缀与标点。"""
    s = norm_text(v)
    for suf in ("SDNBHD", "SDN BHD", "BHD", "PTELTD", "LTD", "LLC", "INC", "CO"):
        s = s.replace(re.sub(r"[^A-Z0-9]", "", suf), "")
    return s


# ── 比较 ─────────────────────────────────────────────────────────────────────

def cmp_field(field: str, gt, pred) -> float:
    if field in ("totalAmount", "totalNetAmount", "totalTaxAmount"):
        a, b = norm_num(gt), norm_num(pred)
        if a is None or b is None:
            return 0.0
        return 1.0 if abs(a - b) <= max(0.01, abs(a) * 1e-4) else 0.0
    if field == "invoiceDate":
        return 1.0 if norm_date(gt) == norm_date(pred) else 0.0
    # 精确匹配组：截断/拆分是明确违规（BRN 要求整串含括号），不能给子串放水。
    # 此前的子串规则会把 '589598-H' 判成 '200201021935 (589598-H)' 的满分，
    # 从而系统性地偏袒「拆字段」这种错误答案。
    if field in ("invoiceNumber", "billFromBusinessRegistrationNumber",
                 "billFromTaxIdentificationNumber"):
        a, b = norm_id(gt), norm_id(pred)
        return 1.0 if (a and a == b) else 0.0
    # DO/PO 可合法多值或带前缀噪声 → 保留包含式判定
    if field in ("purchaseOrderNumber", "deliveryOrderNumber"):
        a, b = norm_id(gt), norm_id(pred)
        if not a:
            return 0.0
        return 1.0 if (a == b or (len(a) >= 4 and (a in b or b in a) and b)) else 0.0
    if field in ("billToName", "billFromName"):
        a, b = norm_name(gt), norm_name(pred)
        if not a:
            return 0.0
        return 1.0 if (a == b or (len(a) >= 5 and (a in b or b in a) and b)) else 0.0
    return 1.0 if norm_text(gt) == norm_text(pred) and norm_text(gt) else 0.0


def cmp_lines(gt_rows, pred_rows) -> float | None:
    """明细行：每条 GT 行找最佳匹配预测行，分数=子字段命中占比的均值。"""
    gt_rows = [r for r in (gt_rows or []) if isinstance(r, dict)]
    if not gt_rows:
        return None
    pred_rows = [r for r in (pred_rows or []) if isinstance(r, dict)]
    if not pred_rows:
        return 0.0
    scores = []
    for g in gt_rows:
        tested = [f for f in LINE_SUBFIELDS if g.get(f) not in (None, "", [], {})]
        if not tested:
            continue
        best = 0.0
        for p in pred_rows:
            hit = 0
            for f in tested:
                if f == "description":
                    gv = g.get(f) or g.get("articleName")
                    pv = p.get(f) or p.get("articleName")
                    a, b = norm_text(gv), norm_text(pv)
                    ok = bool(a) and (a == b or (len(a) >= 6 and (a in b or b in a) and b))
                else:
                    a, b = norm_num(g.get(f)), norm_num(p.get(f))
                    ok = a is not None and b is not None and abs(a - b) <= max(0.01, abs(a) * 1e-4)
                hit += 1 if ok else 0
            best = max(best, hit / len(tested))
        scores.append(best)
    # 行数惩罚：预测行数偏离 GT 行数按比例扣
    if not scores:
        return None
    ratio = min(len(pred_rows), len(gt_rows)) / max(len(pred_rows), len(gt_rows))
    return statistics.fmean(scores) * ratio


# ── GT 归一 ──────────────────────────────────────────────────────────────────

def flatten_gt(gt_json) -> dict:
    recs = gt_json if isinstance(gt_json, list) else [gt_json]
    rec = next((r for r in recs if isinstance(r, dict)), {})
    out: dict = {}
    for field, keys in ALIAS.items():
        for k in keys:
            v = rec.get(k)
            if v not in (None, "", [], {}):
                out[field] = v
                break
    # 税号按格式路由
    for k in TAXID_KEYS:
        v = rec.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        s = v.strip()
        if TIN_RE.match(s):
            out.setdefault("billFromTaxIdentificationNumber", s)
        elif BRN_RE.match(s):
            out.setdefault("billFromBusinessRegistrationNumber", s)
    lines = rec.get("detailOfGoodsOrServices") or rec.get("details") or rec.get("GoodsOrServices")
    if isinstance(lines, list) and lines:
        out["detailOfGoodsOrServices"] = lines
    return out


def flatten_pred(parsed) -> dict:
    recs = parsed if isinstance(parsed, list) else [parsed]
    rec = next((r for r in recs if isinstance(r, dict) and r.get("docType") != "other"), None)
    if rec is None:
        rec = next((r for r in recs if isinstance(r, dict)), {})
    return rec or {}


# ── 主流程 ───────────────────────────────────────────────────────────────────

def score_tag(tag: str) -> dict:
    """tag 可用 `a+b` 池化同一 prompt 的多次运行——单次 VLM 采样在若干字段上
    有 ±1~2 文档的抖动，池化后比较的才是稳定均值。"""
    # `tag:holdout` = 只算调优期间从未看过的文档（seed 42 抽的 18 份之外），
    # 用来证明改动不是对着失败样本过拟合出来的。
    holdout = tag.endswith(":holdout")
    tag = tag[: -len(":holdout")] if holdout else tag
    tags = tag.split("+")
    datas = [json.loads((OUT / f"{t}.json").read_text()) for t in tags]
    results = [r for d in datas for r in d["results"]]
    if holdout:
        import random as _r
        manifest = json.loads((GOLD / "manifest.json").read_text())["items"]
        seen = {i["doc"] for i in _r.Random(42).sample(manifest, 18)}
        results = [r for r in results if r["doc"] not in seen]
    data = {"model": datas[0]["model"], "variant": datas[0]["variant"],
            "results": results}
    per_field: dict[str, list[float]] = {}
    parse_ok = 0
    required_ok = 0
    in_toks, out_toks = [], []
    detail = []

    for r in data["results"]:
        if r.get("in_tokens"):
            in_toks.append(r["in_tokens"])
        if r.get("out_tokens"):
            out_toks.append(r["out_tokens"])
        if not r["parse_ok"]:
            detail.append({"doc": r["doc"], "note": "PARSE_FAIL"})
            continue
        parse_ok += 1
        gt = flatten_gt(json.loads((GOLD / r["gt"]).read_text()))
        pred = flatten_pred(r["parsed"])
        # 结构完整性：MY.yaml 的 required 7 字段
        req = ["docType", "invoiceType", "page", "currency", "invoiceNumber",
               "invoiceDate", "totalAmount"]
        if all(pred.get(k) not in (None, "", [], {}) for k in req):
            required_ok += 1
        row = {"doc": pathlib.Path(r["doc"]).name[:46]}
        for field, gval in gt.items():
            if field == "detailOfGoodsOrServices":
                s = cmp_lines(gval, pred.get(field))
            else:
                s = cmp_field(field, gval, pred.get(field))
            if s is None:
                continue
            per_field.setdefault(field, []).append(s)
            row[field] = round(s, 2)
        detail.append(row)

    n = len(data["results"])
    field_acc = {f: statistics.fmean(v) for f, v in per_field.items()}
    return {
        "tag": tag, "model": data["model"], "variant": data["variant"],
        "n": n, "parse_ok": parse_ok, "required_ok": required_ok,
        "avg_in_tokens": round(statistics.fmean(in_toks)) if in_toks else None,
        "avg_out_tokens": round(statistics.fmean(out_toks)) if out_toks else None,
        "field_acc": field_acc,
        "field_n": {f: len(v) for f, v in per_field.items()},
        "overall": statistics.fmean(field_acc.values()) if field_acc else 0.0,
        "detail": detail,
    }


def main(tags: list[str]) -> None:
    reports = [score_tag(t) for t in tags]
    fields = sorted({f for r in reports for f in r["field_acc"]})
    w = max(len(f) for f in fields) + 2

    print(f"\n{'字段':<{w}} {'考题数':>6}", end="")
    for r in reports:
        print(f" {r['tag']:>12}", end="")
    if len(reports) == 2:
        print(f" {'Δ':>8}", end="")
    print()
    print("-" * (w + 8 + 13 * len(reports) + 9))

    for f in fields:
        n = max(r["field_n"].get(f, 0) for r in reports)
        print(f"{f:<{w}} {n:>6}", end="")
        for r in reports:
            print(f" {r['field_acc'].get(f, float('nan')):>11.1%}", end="")
        if len(reports) == 2:
            d = reports[1]["field_acc"].get(f, 0) - reports[0]["field_acc"].get(f, 0)
            mark = "  " if abs(d) < 0.005 else ("↑" if d > 0 else "↓")
            print(f" {d:>+7.1%}{mark}", end="")
        print()

    print("-" * (w + 8 + 13 * len(reports) + 9))
    for label, key, fmt in [("整体准确率", "overall", "{:.1%}"),
                            ("JSON 解析成功", "parse_ok", "{}"),
                            ("必填 7 字段齐全", "required_ok", "{}"),
                            ("平均输入 token", "avg_in_tokens", "{}"),
                            ("平均输出 token", "avg_out_tokens", "{}")]:
        print(f"{label:<{w}} {'':>6}", end="")
        for r in reports:
            v = r[key]
            print(f" {fmt.format(v) if v is not None else '-':>12}", end="")
        print()
    print()
    for r in reports:
        print(f"  {r['tag']}: {r['variant']}  ({r['model']}, n={r['n']})")


if __name__ == "__main__":
    main(sys.argv[1:])
