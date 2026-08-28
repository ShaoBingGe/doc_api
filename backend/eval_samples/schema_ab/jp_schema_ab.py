"""JP 模板 · schema 传 / 不传 A/B。

为什么 JP 是关键验证对象：`JP_invoice_receipt.yaml` 的设计就是
**「正文精简 + schema description 承载细节」**（yaml remark 明写「删除正文与
schema 的重复表述」）。正文只有 ~1.8k 字符，26 个字段的识别规则几乎全在
description 里。若 schema 从不下发，JP 的绝大部分规则就是死字 —— 这里量化它。

数据用 japan/ 现成基建：原件 `japan/docs/`、GT `japan/result/`、
`japan/split.json` 的 dev/test/val 三分；打分复用 `japan/_bench/score_jp.py`
（字段级准确率，口径已确认：只考 GT 有值字段，漏检实体全字段计 0）。

用法：cd backend && .venv/bin/python eval_samples/schema_ab/jp_schema_ab.py \
        [--n 8] [--split dev] [--rounds 1]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, ".")
JAPAN = pathlib.Path("../japan").resolve()
sys.path.insert(0, str(JAPAN / "_bench"))

import score_jp                                        # noqa: E402
from app.processors.base import extract_json           # noqa: E402
from app.processors.gemini_processor import GeminiProcessor  # noqa: E402

TPL = JAPAN / "JP_invoice_receipt.yaml"


def load_template() -> tuple[str, dict]:
    import yaml
    d = yaml.safe_load(TPL.read_text())
    pt = d["prompt_template"]
    return pt["prompt_format"], pt["json_schema"]


def parse(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        blocks = extract_json(raw or "")
        if blocks:
            try:
                return json.loads(blocks[0])
            except (json.JSONDecodeError, TypeError):
                pass
    return None


def entities(out) -> list:
    if isinstance(out, list):
        return [e for e in out if isinstance(e, dict)]
    if isinstance(out, dict):
        inner = out.get("entities") or out.get("data")
        if isinstance(inner, list):
            return [e for e in inner if isinstance(e, dict)]
        return [out]
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="取该 split 的前 N 份")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()

    prompt, schema = load_template()
    print(f"JP 模板 {TPL.name} · 正文 {len(prompt):,} 字符 · "
          f"schema description 合计 "
          f"{sum(len(v.get('description', '')) for v in schema['items']['anyOf'][0]['properties'].values()):,} 字符\n")

    split = json.loads((JAPAN / "split.json").read_text())
    names = split[args.split][: args.n]
    excl_path = JAPAN / "gt_exclusions.json"
    exclusions = json.loads(excl_path.read_text()) if excl_path.exists() else {}

    proc = GeminiProcessor()
    agg: dict[str, dict] = {}
    rows = []

    for name in names:
        doc = JAPAN / "docs" / name
        gt = json.loads((JAPAN / "result" / f"{name}.json").read_text())
        for arm, rt in (("不传schema", None), ("传schema", {"response_schema": schema})):
            for r in range(args.rounds):
                t0 = time.time()
                try:
                    raw = proc.process_document(str(doc), prompt, rt)
                    err = None
                except Exception as exc:                      # noqa: BLE001
                    raw, err = "", str(exc)[:110]
                dt = time.time() - t0
                u = getattr(proc, "last_usage", None) or {}
                violation = None
                if err:
                    got, total = 0.0, 0
                    s = {"got": 0.0, "total": 0, "missed": 0, "spurious": 0}
                else:
                    ents = entities(parse(raw))
                    try:
                        s = score_jp.score_file(ents, gt, exclusions.get(name))
                    except TypeError as exc:
                        # 打分器假定 schema 声明的类型成立（如 page 是数组）。
                        # 模型违反类型时它会崩——这本身就是「无 schema 硬约束」
                        # 的代价：下游解析器直接炸。规范化后重打，并记违规。
                        violation = f"type-violation: {str(exc)[:60]}"
                        for e in ents:
                            for k in ("page",):
                                if k in e and not isinstance(e[k], list):
                                    e[k] = [e[k]]
                        s = score_jp.score_file(ents, gt, exclusions.get(name))
                    got, total = s["got"], s["total"]
                a = agg.setdefault(arm, {"got": 0.0, "total": 0, "sec": 0.0,
                                         "in": 0, "out": 0, "n": 0,
                                         "missed": 0, "spurious": 0})
                a["got"] += got; a["total"] += total; a["sec"] += dt
                a["in"] += u.get("prompt_tokens") or 0
                a["out"] += u.get("output_tokens") or 0
                a["n"] += 1
                a["missed"] += s.get("missed", 0)
                a["spurious"] += s.get("spurious", 0)
                a["viol"] = a.get("viol", 0) + (1 if violation else 0)
                acc = got / total if total else 0.0
                rows.append({"doc": name, "arm": arm, "round": r, "acc": round(acc, 4),
                             "got": got, "total": total, "sec": round(dt, 1),
                             "in_tok": u.get("prompt_tokens"),
                             "out_tok": u.get("output_tokens"),
                             "missed": s.get("missed"), "spurious": s.get("spurious"),
                             "err": err, "violation": violation})
                print(f"  {name[:30]:32} {arm:10} r{r} "
                      f"acc={acc:.3f} ({got:.1f}/{total}) {dt:5.1f}s "
                      f"in={u.get('prompt_tokens')}" + (f"  ERR {err}" if err else ""))

    print("\n═══ 汇总 ═══")
    print(f"{'arm':12} {'字段准确率':>12} {'耗时/份':>9} {'输入tok':>9} {'输出tok':>9} {'漏检':>5} {'误检':>5}")
    for arm, a in agg.items():
        acc = a["got"] / a["total"] if a["total"] else 0.0
        print(f"{arm:12} {acc:11.4f}  {a['sec']/a['n']:8.1f}s "
              f"{a['in']//a['n']:9d} {a['out']//a['n']:9d} "
              f"{a['missed']:5d} {a['spurious']:5d}")
    if len(agg) == 2:
        off, on = agg["不传schema"], agg["传schema"]
        a_off = off["got"] / off["total"] if off["total"] else 0
        a_on = on["got"] / on["total"] if on["total"] else 0
        print(f"\n传 schema 相对不传：准确率 {a_on - a_off:+.4f}"
              f"（{(a_on/a_off - 1)*100:+.1f}%）· "
              f"输入 token {on['in']//on['n'] - off['in']//off['n']:+d} · "
              f"耗时 {on['sec']/on['n'] - off['sec']/off['n']:+.1f}s")
    pathlib.Path(__file__).with_name("jp_ab_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
