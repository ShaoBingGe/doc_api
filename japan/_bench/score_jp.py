"""日本票据 GT 锚定打分器 — 字段级准确率（口径已与用户确认）。

口径:
  1. 只考 GT 有值字段（对齐别名归并后）；判对字段数 ÷ GT 有值字段总数。
  2. 明细数组（商品行/税金汇总）全部计入：每条 GT 行在预测行里找最佳匹配，
     逐 GT 有值子字段计分。
  3. 漏检实体其全部 GT 字段计 0；误检实体（预测出 invoice/receipt 但无 GT
     对应）单独统计，不进分母。
  4. 归一化比较：数字容差 0.01；日期归一 YYYY-MM-DD；文本 NFKC 全半角归一+
     去空白；currency 円/¥≡JPY；invoiceType 别名（Commercial Invoice≡Invoice）。
  5. GT 杂键别名归并（receiptNumber→invoiceNumber 等）；page/序号/_* 不计分。

用法:
    python japan/_bench/score_jp.py <tag> --split dev        # 打分
    python japan/_bench/score_jp.py selftest                 # round-trip 自检
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata
from difflib import SequenceMatcher

JAPAN = pathlib.Path(__file__).resolve().parents[1]
RESULT = JAPAN / "result"
OUT = JAPAN / "_bench" / "results"

# GT 杂键 → 规范键（仅语义确定的归并；1-2 次的孤儿键映射到最近语义）
ALIAS = {
    "receiptNumber": "invoiceNumber",
    "receiptDate": "invoiceDate",
    "nameOfReceipt": "nameOfInvoice",
    "receiptType": "invoiceType",
    "seller": "billFromName",
    "amount": "totalAmount",
    "date": "invoiceDate",
    "payer": "billToName",
}
# 不计分键：page（excel_migrated 不可靠）、序号、内部键、无法归并的孤儿键
UNSCORED = {"page", "序号", "purpose", "paymentMethod", "remarks", "docId"}

DETAIL_FIELDS = {
    "detailOfGoodsOrServices": ["articleName", "description", "quantity",
                                "unitOfMeasure", "unitPrice", "netAmount",
                                "taxRate", "tax", "grossAmount", "orderNumber"],
    "detailOfTaxSummary": ["taxCategory", "taxRate", "netTaxableAmount", "tax"],
}

INVOICE_TYPE_ALIAS = {
    "commercialinvoice": "invoice",
    "taxinvoice": "invoice",  # GT 词汇只有 Invoice/Receipt；别名宽容
}


# ── 归一化 ───────────────────────────────────────────────────────────────────

def norm_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d.\-]", "", v.replace(",", ""))
        try:
            return float(s) if s not in ("", "-", ".") else None
        except ValueError:
            return None
    return None


def norm_date(v) -> str | None:
    if v is None:
        return None
    s = str(v)
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s.strip() or None


def norm_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    s = unicodedata.normalize("NFKC", str(v))
    s = re.sub(r"\s+", "", s).upper()
    return s


def norm_currency(v) -> str:
    s = norm_text(v)
    return "JPY" if s in ("円", "¥", "JPY", "YEN") else s


def norm_rate(v) -> str | None:
    """税率: '10%'≡'10.0%'≡'10 %'。"""
    if v is None:
        return None
    m = re.search(r"(-?[\d.]+)\s*%", str(v))
    if m:
        try:
            f = float(m.group(1))
            return f"{f:g}%"
        except ValueError:
            pass
    return norm_text(v) or None


DATE_KEYS = {"invoiceDate", "dueDate"}
NUM_KEYS = {"totalAmount", "totalNetAmount", "totalTaxAmount", "paymentDueInDays",
            "quantity", "unitPrice", "netAmount", "tax", "grossAmount",
            "netTaxableAmount"}
RATE_KEYS = {"taxRate"}


def flatten_value(v):
    """GT 里少数字段是嵌套 dict（如 billToName: {"name": ...}）→ 取其主值做比较。"""
    if isinstance(v, dict):
        for k in ("name", "value", "text"):
            if has_value(v.get(k)):
                return v[k]
        vals = [x for x in v.values() if has_value(x)]
        return vals[0] if len(vals) == 1 else json.dumps(v, ensure_ascii=False,
                                                         sort_keys=True)
    return v


def values_equal(key: str, pred, gt) -> bool:
    """归一化比较（gt 一定有值；pred 可能缺）。"""
    if pred is None or pred == "" or pred == []:
        return False
    pred, gt = flatten_value(pred), flatten_value(gt)
    if key in NUM_KEYS:
        p, g = norm_num(pred), norm_num(gt)
        # 数字键但 GT 是文本值（如 paymentDueInDays="月末締め翌月末払い"）→ 回退文本比较
        if g is None:
            return norm_text(pred) == norm_text(gt)
        return p is not None and abs(p - g) <= 0.01
    if key in DATE_KEYS:
        return norm_date(pred) == norm_date(gt)
    if key in RATE_KEYS:
        return norm_rate(pred) == norm_rate(gt)
    if key == "currency":
        return norm_currency(pred) == norm_currency(gt)
    if key == "invoiceType":
        p, g = norm_text(pred).lower(), norm_text(gt).lower()
        return INVOICE_TYPE_ALIAS.get(p, p) == INVOICE_TYPE_ALIAS.get(g, g)
    if key == "docType":
        return norm_text(pred).lower() == norm_text(gt).lower()
    # 金额类未知键兜底：两边都能解析成数字则按数字比
    pn, gn = norm_num(pred), norm_num(gt)
    if pn is not None and gn is not None and not re.search(r"[A-Za-z一-鿿ぁ-ヿ]", str(gt)):
        return abs(pn - gn) <= 0.01
    return norm_text(pred) == norm_text(gt)


def has_value(v) -> bool:
    return v not in (None, "", [], {})


def canon_entity(e: dict) -> dict:
    """别名归并（GT 与预测两侧用同一套，避免键名错位造成假失分）。
    page 保留（对齐要用），计分时按 UNSCORED 跳过。"""
    out = {}
    for k, v in e.items():
        if k.startswith("_") or not has_value(v):
            continue
        ck = ALIAS.get(k, k)
        if ck not in out:  # 原键优先于别名键，不覆盖
            out[ck] = v
    return out


# ── 明细数组计分（集合最佳匹配） ─────────────────────────────────────────────

def score_detail(key: str, pred_rows, gt_rows) -> tuple[float, int, list]:
    """→ (得分, GT 有值子字段总数, 差异明细)。每条 GT 行匹配一条未用预测行。"""
    subs = DETAIL_FIELDS[key]
    pred_rows = [r for r in (pred_rows or []) if isinstance(r, dict)]
    gt_rows = [r for r in (gt_rows or []) if isinstance(r, dict)]
    total = sum(1 for g in gt_rows for f in subs if has_value(g.get(f)))
    if total == 0:
        return 0.0, 0, []

    def row_hits(p: dict, g: dict) -> int:
        return sum(1 for f in subs
                   if has_value(g.get(f)) and values_equal(f, p.get(f), g.get(f)))

    used: set[int] = set()
    got = 0.0
    diffs = []
    # 贪心：按可得分降序为每条 GT 行挑最佳预测行
    order = sorted(range(len(gt_rows)),
                   key=lambda i: -max((row_hits(p, gt_rows[i]) for p in pred_rows),
                                      default=0))
    for gi in order:
        g = gt_rows[gi]
        best, best_j = -1, None
        for j, p in enumerate(pred_rows):
            if j in used:
                continue
            h = row_hits(p, g)
            if h > best:
                best, best_j = h, j
        gt_n = sum(1 for f in subs if has_value(g.get(f)))
        if best_j is not None and best > 0:
            used.add(best_j)
            got += best
            if best < gt_n:
                p = pred_rows[best_j]
                miss = [f for f in subs if has_value(g.get(f))
                        and not values_equal(f, p.get(f), g.get(f))]
                diffs.append({"gt_row": gi, "missed_subfields": miss,
                              "gt": {f: g.get(f) for f in miss},
                              "pred": {f: p.get(f) for f in miss}})
        else:
            diffs.append({"gt_row": gi, "unmatched": True,
                          "gt": {f: g.get(f) for f in subs if has_value(g.get(f))}})
    return got, total, diffs


# ── 实体对齐 ─────────────────────────────────────────────────────────────────

def fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# 高信息字段：用于实体对齐的判别依据。docType/currency/invoiceType 在日本语料里
# 几乎恒定（receipt/JPY），当作相似度会把不同小票判成同一条。
KEY_FIELDS = ["invoiceNumber", "invoiceDate", "totalAmount", "totalNetAmount",
              "totalTaxAmount", "billFromName", "billToName", "nameOfInvoice",
              "billFromTaxIdentificationNumber", "shipmentNumber",
              "purchaseOrderNumber", "dueDate", "invoiceCode"]


def similarity(pred: dict, gt: dict, use_page: bool) -> tuple[float, int]:
    """→ (高信息字段判对率, 判对数)。GT 自己配自己恒为 (1.0, n)，
    这保证 round-trip 对齐完美，同时对真实预测是「最佳匹配」的标准口径。"""
    keys = [k for k in KEY_FIELDS if has_value(gt.get(k))]
    if not keys:  # GT 只有低信息字段 → 退化到全部有值键
        keys = [k for k in gt if has_value(gt.get(k)) and k not in DETAIL_FIELDS]
    if not keys:
        return 0.0, 0
    hits = sum(1 for k in keys if values_equal(k, pred.get(k), gt.get(k)))
    rate = hits / len(keys)
    if use_page:
        pp, gp = set(pred.get("page") or []), set(gt.get("page") or [])
        if pp and gp and pp & gp:
            rate += 0.05  # 同页轻微加权（仅 manual 源 page 可信）
    return rate, hits


MATCH_MIN_RATE = 0.34  # 至少 1/3 高信息字段判对才算同一条票据


def align(preds: list[dict], gts: list[dict], use_page: bool
          ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """贪心最佳匹配 → (匹配对, 漏检 GT 索引, 误检预测索引)。
    单预测-单 GT 时直接配对；多实体时要求判对率 ≥MATCH_MIN_RATE 且至少 1 个
    高信息字段命中。同分时优先输出顺序相近的配对（模型多按阅读顺序输出）。"""
    if len(preds) == 1 and len(gts) == 1:
        return [(0, 0)], [], []
    cands = []
    for i, p in enumerate(preds):
        for j, g in enumerate(gts):
            rate, hits = similarity(p, g, use_page)
            if rate >= MATCH_MIN_RATE and hits >= 1:
                cands.append((rate, -abs(i - j), i, j))
    cands.sort(reverse=True)
    used_p: set[int] = set()
    used_g: set[int] = set()
    pairs = []
    for _rate, _prox, i, j in cands:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        pairs.append((i, j))
    missed = [j for j in range(len(gts)) if j not in used_g]
    spurious = [i for i in range(len(preds)) if i not in used_p]
    return pairs, missed, spurious


# ── 单文件打分 ───────────────────────────────────────────────────────────────

def load_exclusions() -> dict:
    """同一原件被标注两次且结果不一致的字段 → 不可信，从计分中剔除。
    由 _bench/gt_conflicts.py 客观生成（见该文件文档）。"""
    p = JAPAN / "gt_exclusions.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get("field_level", {})


def score_file(pred_entities: list, gt_data: dict,
               excluded: dict[str, list[str]] | None = None) -> dict:
    """excluded: {"<GT 实体在原 entities 数组中的索引>": [不计分字段, ...]}"""
    gt_all = gt_data.get("entities") or []
    use_page = gt_data.get("source") == "manual"
    excluded = excluded or {}
    # gt_fin 是过滤 other 后的列表 → 需要记住每条在原数组中的索引，才能对上 exclusions
    fin_orig_idx = [i for i, e in enumerate(gt_all)
                    if e.get("docType") in ("invoice", "receipt")]
    gt_fin = [canon_entity(gt_all[i]) for i in fin_orig_idx]

    def skip(gi: int, key: str) -> bool:
        return key in excluded.get(str(fin_orig_idx[gi]), ())
    n_gt_other = sum(1 for e in gt_all if e.get("docType") == "other")

    preds = [canon_entity(e) for e in (pred_entities or []) if isinstance(e, dict)]
    pred_fin = [e for e in preds if norm_text(e.get("docType")).lower()
                in ("invoice", "receipt")]
    n_pred_other = len(preds) - len(pred_fin)

    pairs, missed, spurious = align(pred_fin, gt_fin, use_page)
    # 误检宽容：GT other 数量内的多余预测视为「other 被升格」→ 仍计误检，
    # 但单独标注（错识别 other→invoice/receipt）
    got, total = 0.0, 0
    field_stats: dict[str, list[float]] = {}
    file_diffs = []

    def bump(key: str, ok: float):
        field_stats.setdefault(key, [0.0, 0.0])
        field_stats[key][0] += ok
        field_stats[key][1] += 1

    for pi, gi in pairs:
        p, g = pred_fin[pi], gt_fin[gi]
        ent_diff = {}
        for k, gv in g.items():
            if k in UNSCORED or skip(gi, k):
                continue
            if k in DETAIL_FIELDS:
                dgot, dtotal, ddiffs = score_detail(k, p.get(k), gv)
                got += dgot
                total += dtotal
                field_stats.setdefault(k, [0.0, 0.0])
                field_stats[k][0] += dgot
                field_stats[k][1] += dtotal
                if ddiffs:
                    ent_diff[k] = ddiffs
            else:
                ok = values_equal(k, p.get(k), gv)
                got += 1.0 if ok else 0.0
                total += 1
                bump(k, 1.0 if ok else 0.0)
                if not ok:
                    ent_diff[k] = {"gt": gv, "pred": p.get(k)}
        if ent_diff:
            file_diffs.append({"gt_idx": gi, "diff": ent_diff})

    for gi in missed:
        g = gt_fin[gi]
        for k, gv in g.items():
            if k in UNSCORED or skip(gi, k):
                continue
            if k in DETAIL_FIELDS:
                _, dtotal, _ = score_detail(k, [], gv)
                total += dtotal
                field_stats.setdefault(k, [0.0, 0.0])
                field_stats[k][1] += dtotal
            else:
                total += 1
                bump(k, 0.0)
        file_diffs.append({"gt_idx": gi, "MISSED": True,
                           "gt_head": {k: g.get(k) for k in
                                       ("invoiceNumber", "totalAmount", "invoiceDate",
                                        "billFromName") if g.get(k)}})

    return {
        "got": got, "total": total,
        "n_gt": len(gt_fin), "n_pred": len(pred_fin),
        "n_gt_other": n_gt_other, "n_pred_other": n_pred_other,
        "matched": len(pairs), "missed": len(missed), "spurious": len(spurious),
        "field_stats": field_stats, "diffs": file_diffs,
    }


# ── 汇总 ─────────────────────────────────────────────────────────────────────

def score_tag(tag: str, split_name: str, clean: bool = False) -> dict:
    split = json.loads((JAPAN / "split.json").read_text())
    names = split[split_name] if split_name in split else sorted(
        n for s in split.values() for n in s)
    exclusions = load_exclusions() if clean else {}
    tag_dir = OUT / tag
    got, total = 0.0, 0
    missed = spurious = matched = n_gt = 0
    no_pred = []
    fields: dict[str, list[float]] = {}
    per_file = {}
    for name in names:
        gt = json.loads((RESULT / (name + ".json")).read_text(encoding="utf-8"))
        pf = tag_dir / (name + ".json")
        pred = None
        if pf.exists():
            try:
                pred = json.loads(pf.read_text()).get("entities")
            except json.JSONDecodeError:
                pred = None
        if pred is None:
            no_pred.append(name)
            pred = []
        r = score_file(pred, gt, exclusions.get(name))
        got += r["got"]
        total += r["total"]
        missed += r["missed"]
        spurious += r["spurious"]
        matched += r["matched"]
        n_gt += r["n_gt"]
        for k, (g_, t_) in r["field_stats"].items():
            fields.setdefault(k, [0.0, 0.0])
            fields[k][0] += g_
            fields[k][1] += t_
        per_file[name] = {k: r[k] for k in
                          ("got", "total", "n_gt", "n_pred", "matched",
                           "missed", "spurious", "diffs")}
    acc = got / total if total else 0.0
    summary = {
        "tag": tag, "split": split_name, "files": len(names), "clean": clean,
        "accuracy": round(acc, 4), "got": round(got, 1), "total": total,
        "gt_entities": n_gt, "matched": matched, "missed": missed,
        "spurious": spurious, "files_without_pred": no_pred,
        "per_field": {k: {"acc": round(v[0] / v[1], 3), "n": int(v[1])}
                      for k, v in sorted(fields.items(), key=lambda x: -x[1][1])},
    }
    suffix = "_clean" if clean else ""
    (OUT / f"{tag}_{split_name}{suffix}_score.json").write_text(
        json.dumps({"summary": summary, "per_file": per_file},
                   ensure_ascii=False, indent=1))
    return summary


def print_summary(s: dict) -> None:
    mode = " [剔除冲突标注]" if s.get("clean") else ""
    print(f"\n== {s['tag']} / {s['split']}{mode} ==")
    print(f"字段级准确率: {s['accuracy']:.2%}  ({s['got']}/{s['total']})")
    print(f"实体: GT {s['gt_entities']}  匹配 {s['matched']}  "
          f"漏检 {s['missed']}  误检 {s['spurious']}")
    if s["files_without_pred"]:
        print(f"无预测文件 {len(s['files_without_pred'])}: {s['files_without_pred'][:5]}")
    print(f"{'字段':40s} {'acc':>6s} {'n':>5s}")
    for k, v in s["per_field"].items():
        print(f"{k:40s} {v['acc']:6.1%} {v['n']:5d}")


# ── round-trip 自检 ──────────────────────────────────────────────────────────

def selftest() -> None:
    import copy
    ok = True
    files = sorted(RESULT.glob("*.json"))
    # 1) GT 喂自己 → 100%
    got, total = 0.0, 0
    for f in files:
        gt = json.loads(f.read_text(encoding="utf-8"))
        pred = [{k: v for k, v in e.items() if not k.startswith("_")}
                for e in (gt.get("entities") or [])]
        r = score_file(pred, gt)
        got += r["got"]
        total += r["total"]
        if r["missed"] or r["spurious"]:
            print(f"  自检异常(对齐): {f.name} missed={r['missed']} spurious={r['spurious']}")
            ok = False
    acc = got / total
    print(f"round-trip: {acc:.4%} ({got}/{total})")
    if acc < 1.0:
        # 找出非满分文件
        for f in files:
            gt = json.loads(f.read_text(encoding="utf-8"))
            pred = [dict(e) for e in (gt.get("entities") or [])]
            r = score_file(pred, gt)
            if r["got"] < r["total"]:
                print(f"  {f.name}: {r['got']}/{r['total']} diffs={json.dumps(r['diffs'], ensure_ascii=False)[:300]}")
        ok = False
    # 2) 扰动应扣分：改金额 / 删一条实体
    sample = json.loads((RESULT / "000173.pdf.json").read_text(encoding="utf-8"))
    pred = copy.deepcopy(sample["entities"])
    pred[0]["totalAmount"] = 999
    r = score_file(pred, sample)
    assert r["got"] < r["total"], "改金额未扣分"
    pred2 = copy.deepcopy(sample["entities"])[1:]
    r2 = score_file(pred2, sample)
    assert r2["missed"] == 1, f"删实体未计漏检: {r2['missed']}"
    print("扰动测试通过（改金额扣分、删实体计漏检）")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--clean", action="store_true",
                    help="剔除「同一原件两份 GT 互相冲突」的字段（gt_exclusions.json）")
    a = ap.parse_args()
    if a.tag == "selftest":
        selftest()
    else:
        print_summary(score_tag(a.tag, a.split, a.clean))
