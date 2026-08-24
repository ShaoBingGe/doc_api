"""多次运行的实体并集合并（self-consistency union）。

动机：同一 prompt 两次运行的漏检实体数差 7 条（见 REPORT.md §3.0）——模型对混贴页的
切分是「抖」的而非「盲」的，同一张票在某次运行里能被看见。把 N 次运行的实体取并集，
理论上能压低漏检；而漏检是最大失分源（一条实体归零 = 该实体 7–10 个字段全归零）。

去重键：(billFromName 归一, invoiceDate 归一, totalAmount) 三元组 —— 与打分器的实体
对齐口径同源。冲突时保留**字段更全**的那条（字段多 = 信息多 = 命中 GT 有值字段的机会大，
且打分器对多输出的字段不扣分）。

用法:
    python japan/_bench/merge_runs.py <out_tag> <tag1> <tag2> [tag3 ...] --split dev
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

JAPAN = pathlib.Path(__file__).resolve().parents[1]
OUT = JAPAN / "_bench" / "results"
sys.path.insert(0, str(JAPAN / "_bench"))


def entity_key(e: dict) -> tuple:
    from score_jp import norm_date, norm_num, norm_text
    return (
        norm_text(e.get("billFromName"))[:24],
        norm_date(e.get("invoiceDate")) or "",
        norm_num(e.get("totalAmount")),
    )


def richness(e: dict) -> int:
    """字段丰富度：标量有值字段数 + 明细行数（作为保留取舍的依据）。"""
    n = sum(1 for k, v in e.items()
            if not k.startswith("_") and v not in (None, "", [], {}))
    for k in ("detailOfGoodsOrServices", "detailOfTaxSummary"):
        rows = e.get(k)
        if isinstance(rows, list):
            n += len(rows)
    return n


def merge_file(preds_list: list[list], min_votes: int = 1) -> list:
    """把同一文件的多次预测合并成一个实体列表。

    min_votes=1 → 并集（召回优先，漏检最低但误检最高）；
    min_votes=2 → 至少两次运行都看到这条票才保留（压误检，可能牺牲部分召回）。
    """
    best: dict[tuple, dict] = {}
    votes: dict[tuple, set[int]] = {}
    order: list[tuple] = []
    others: list[dict] = []
    for run_i, preds in enumerate(preds_list):
        for e in preds or []:
            if not isinstance(e, dict):
                continue
            if str(e.get("docType", "")).lower() == "other":
                others.append(e)
                continue
            k = entity_key(e)
            votes.setdefault(k, set()).add(run_i)
            if k not in best:
                best[k] = e
                order.append(k)
            elif richness(e) > richness(best[k]):
                best[k] = e
    merged = [best[k] for k in order if len(votes[k]) >= min_votes]
    # other 记录不参与打分，保留第一次运行的即可（避免无意义膨胀）
    return merged + others[: len(others) // max(len(preds_list), 1)]


def main(out_tag: str, tags: list[str], split_name: str, min_votes: int = 1) -> None:
    split = json.loads((JAPAN / "split.json").read_text())
    names = split[split_name] if split_name in split else sorted(
        n for s in split.values() for n in s)
    out_dir = OUT / out_tag
    out_dir.mkdir(parents=True, exist_ok=True)
    n_in, n_out = 0, 0
    for name in names:
        preds_list = []
        for t in tags:
            p = OUT / t / f"{name}.json"
            if p.exists():
                ents = json.loads(p.read_text()).get("entities")
                if isinstance(ents, list):
                    preds_list.append(ents)
        if not preds_list:
            continue
        merged = merge_file(preds_list, min_votes)
        n_in += sum(len(p) for p in preds_list) // len(preds_list)
        n_out += len(merged)
        (out_dir / f"{name}.json").write_text(json.dumps(
            {"entities": merged, "merged_from": tags, "doc": name},
            ensure_ascii=False, indent=1))
    print(f"合并 {len(tags)} 次运行(min_votes={min_votes}) → {out_tag}  平均单次实体 {n_in} → {n_out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out_tag")
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--min-votes", type=int, default=1,
                    help="实体至少出现在几次运行中才保留（1=并集，2=多数票）")
    a = ap.parse_args()
    main(a.out_tag, a.tags, a.split, a.min_votes)
