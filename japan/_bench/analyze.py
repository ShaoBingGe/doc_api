"""失分聚类分析 — 把 score_jp 的 per_file diffs 归纳成可行动的错因清单。

用法:
    python japan/_bench/analyze.py <tag> --split dev [--field billFromName] [--top 15]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

JAPAN = pathlib.Path(__file__).resolve().parents[1]
OUT = JAPAN / "_bench" / "results"


def classify(field: str, gt, pred) -> str:
    """把一次失分归到一个错因类型（纯规则，便于聚合）。"""
    if pred in (None, "", [], {}):
        return "漏提取(pred为空)"
    gs, ps = str(gt), str(pred)
    if gs.replace(" ", "") == ps.replace(" ", ""):
        return "仅空白差异"
    if gs in ps or ps in gs:
        return "包含关系(截断/多余前后缀)"
    try:
        if abs(float(str(gt).replace(",", "")) - float(str(pred).replace(",", ""))) > 0:
            return "数值不同"
    except (ValueError, TypeError):
        pass
    return "内容不同"


def main(tag: str, split: str, only_field: str | None, top: int) -> None:
    data = json.loads((OUT / f"{tag}_{split}_score.json").read_text())
    per_file = data["per_file"]
    summary = data["summary"]

    by_field = collections.Counter()
    by_reason = collections.defaultdict(collections.Counter)
    examples = collections.defaultdict(list)
    missed_files = []
    detail_miss = collections.Counter()

    for name, r in per_file.items():
        if r["missed"]:
            missed_files.append((name, r["missed"], r["n_gt"], r["n_pred"]))
        for d in r["diffs"]:
            if d.get("MISSED"):
                continue
            for field, info in d["diff"].items():
                if isinstance(info, list):  # 明细数组
                    for row in info:
                        for sf in row.get("missed_subfields", []):
                            detail_miss[f"{field}.{sf}"] += 1
                        if row.get("unmatched"):
                            detail_miss[f"{field}.<整行未匹配>"] += 1
                    by_field[field] += len(info)
                    continue
                by_field[field] += 1
                reason = classify(field, info.get("gt"), info.get("pred"))
                by_reason[field][reason] += 1
                if len(examples[(field, reason)]) < 3:
                    examples[(field, reason)].append(
                        (name, info.get("gt"), info.get("pred")))

    print(f"== {tag}/{split} 失分聚类 ==")
    print(f"准确率 {summary['accuracy']:.2%} ({summary['got']}/{summary['total']})  "
          f"漏检 {summary['missed']} 误检 {summary['spurious']}\n")

    print("── 按字段失分数（Top）──")
    pf = summary["per_field"]
    for field, cnt in by_field.most_common(top):
        acc = pf.get(field, {}).get("acc", 0)
        n = pf.get(field, {}).get("n", 0)
        reasons = ", ".join(f"{r}×{c}" for r, c in by_reason[field].most_common(3))
        print(f"{field:36s} 失分{cnt:4d}  acc={acc:5.1%} n={n:4d}  {reasons}")

    if detail_miss:
        print("\n── 明细子字段失分 ──")
        for k, c in detail_miss.most_common(12):
            print(f"  {k:44s} {c}")

    if missed_files:
        print(f"\n── 漏检实体的文件（{len(missed_files)}）──")
        for name, m, ngt, npred in sorted(missed_files, key=lambda x: -x[1])[:12]:
            print(f"  {name[:50]:52s} 漏{m:3d}  GT={ngt} 预测={npred}")

    print("\n── 样例 ──")
    shown = 0
    for field, cnt in by_field.most_common(top):
        for reason, _ in by_reason[field].most_common(2):
            for name, gt, pred in examples[(field, reason)][:2]:
                print(f"  [{field}/{reason}] {name[:36]}")
                print(f"      GT  : {str(gt)[:110]}")
                print(f"      pred: {str(pred)[:110]}")
                shown += 1
        if shown > 34:
            break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--field")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    main(a.tag, a.split, a.field, a.top)
