"""v6 vs v6.1 —— schema 路径能否替代 prompt 正文里的「输出字段清单」段。

v6（prompts/tpl7/v6.json）正文含一段约 2.1k 字符的「输出字段清单」，
内容是 schema 的文字复述（键名 + 类型 + enum）。既然 response_schema
现在真的会下发（PASS_SCHEMA_TO_MODEL），这段理论上可由硬约束替代。

  v6    = 原样正文 + 传 schema（清单与 schema 双写）
  v6.1  = 删掉清单段 + 传 schema（只靠硬约束）

两者**规则完全相同**，唯一差异就是那一段 —— 所以耗时/token/准确率的
差异可归因于它。另跑 v6-noschema（原样正文、不传 schema）作为「当前生产
行为」的参照系。

用法：cd backend && .venv/bin/python eval_samples/schema_ab/v61_experiment.py [--rounds 2]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, ".")

from app.processors.base import extract_json          # noqa: E402
from app.processors.gemini_processor import GeminiProcessor  # noqa: E402

HERE = pathlib.Path(__file__).parent
V6 = json.loads((HERE / ".." / ".." / ".." / "prompts" / "tpl7" / "v6.json").read_text())
GT = json.loads((HERE / "ground_truth.json").read_text())
SAMPLES = ["S1_single_invoice.pdf", "S2_six_invoices.pdf", "S3_credit_note.pdf"]

FIELD_LIST_HEADER = "# 输出字段清单（键名必须逐字一致）"


def strip_field_list(prompt: str) -> str:
    """删掉「输出字段清单」段（到下一个一级标题为止）。"""
    start = prompt.find(FIELD_LIST_HEADER)
    if start < 0:
        raise SystemExit("v6 正文里找不到「输出字段清单」段")
    nxt = prompt.find("\n# ", start + len(FIELD_LIST_HEADER))
    end = nxt + 1 if nxt > 0 else len(prompt)
    return prompt[:start] + prompt[end:]


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


def entities(out) -> list[dict]:
    if isinstance(out, list):
        return [e for e in out if isinstance(e, dict)]
    if isinstance(out, dict):
        inner = out.get("entities") or out.get("data")
        if isinstance(inner, list):
            return [e for e in inner if isinstance(e, dict)]
        return [out]
    return []


def num_eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


def score(sample: str, out) -> tuple[int, int, list[str]]:
    """按 ground_truth.json 打分 → (通过, 总数, 失败明细)。"""
    gt = GT[sample]
    ents = entities(out)
    billing = [e for e in ents if e.get("docType") != "other"]
    checks: list[tuple[str, bool, str]] = []

    def add(name, ok, got):
        checks.append((name, ok, str(got)))

    add("条数", len(ents) == gt["n"], len(ents))
    add("docTypes", [e.get("docType") for e in ents] == gt["docTypes"],
        [e.get("docType") for e in ents])
    add("invoiceTypes",
        [e.get("invoiceType") for e in billing] == gt["invoiceTypes"],
        [e.get("invoiceType") for e in billing])
    add("invoiceNumbers",
        [e.get("invoiceNumber") for e in billing] == gt["invoiceNumbers"],
        [e.get("invoiceNumber") for e in billing])
    if "totals" in gt:
        got = [e.get("totalAmount") for e in billing]
        add("totals",
            len(got) == len(gt["totals"]) and all(num_eq(a, b) for a, b in zip(got, gt["totals"])),
            got)
    if "brn" in gt:
        got = [e.get("billFromBusinessRegistrationNumber") for e in billing]
        add("brn", got == gt["brn"], got)
    if "lineRows" in gt:
        got = [len(e.get("detailOfGoodsOrServices") or []) for e in billing]
        add("lineRows", got == gt["lineRows"], got)
    if "origRefs" in gt:
        got = [len(e.get("originalInvoiceReferences") or []) for e in billing]
        add("origRefs≥1", bool(got and got[0] >= gt["origRefs"][0]), got)

    fails = [f"{n}={g}" for n, ok, g in checks if not ok]
    return sum(1 for _, ok, _ in checks if ok), len(checks), fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    p_v6 = V6["composed_prompt"]
    p_v61 = strip_field_list(p_v6)
    schema = V6["composed_schema"]

    print(f"v6   正文 {len(p_v6):,} 字符")
    print(f"v6.1 正文 {len(p_v61):,} 字符  （删掉字段清单段 "
          f"−{len(p_v6) - len(p_v61):,} 字符 = −{(1 - len(p_v61)/len(p_v6)) * 100:.1f}%）\n")

    arms = [
        ("v6-noschema", p_v6, None),      # 当前生产行为的参照系
        ("v6",          p_v6, schema),    # 清单 + schema 双写
        ("v6.1",        p_v61, schema),   # 只靠 schema
    ]

    proc = GeminiProcessor()
    rows: list[dict] = []
    for sample in SAMPLES:
        path = str(HERE / sample)
        for arm, prompt, sc in arms:
            for r in range(args.rounds):
                rt = {"response_schema": sc} if sc else None
                t0 = time.time()
                try:
                    raw = proc.process_document(path, prompt, rt)
                    err = None
                except Exception as exc:                      # noqa: BLE001
                    raw, err = "", str(exc)[:120]
                dt = time.time() - t0
                usage = getattr(proc, "last_usage", None) or {}
                p_, t_, fails = (0, 0, [err]) if err else score(sample, parse(raw))
                rows.append({
                    "sample": sample[:2], "arm": arm, "round": r,
                    "sec": round(dt, 1),
                    "in_tok": usage.get("prompt_tokens"),
                    "out_tok": usage.get("output_tokens"),
                    "pass": p_, "total": t_, "fails": fails,
                })
                mark = "✅" if t_ and p_ == t_ else "❌"
                print(f"  {mark} {sample[:2]} {arm:12} r{r}  {dt:5.1f}s  "
                      f"in={usage.get('prompt_tokens')}  out={usage.get('output_tokens')}  "
                      f"{p_}/{t_}" + (f"  ← {'; '.join(fails)[:90]}" if fails else ""))
        print()

    # ── 汇总 ──
    print("═══ 汇总（每样本各轮均值）═══")
    print(f"{'arm':13} {'耗时':>7} {'输入token':>10} {'输出token':>10} {'准确':>9}")
    summary = {}
    for arm, _, _ in arms:
        sel = [r for r in rows if r["arm"] == arm]
        sec = sum(r["sec"] for r in sel) / len(sel)
        it = [r["in_tok"] for r in sel if r["in_tok"]]
        ot = [r["out_tok"] for r in sel if r["out_tok"]]
        pas = sum(r["pass"] for r in sel)
        tot = sum(r["total"] for r in sel)
        summary[arm] = {"sec": sec, "in": sum(it)/len(it) if it else None,
                        "out": sum(ot)/len(ot) if ot else None,
                        "pass": pas, "total": tot}
        print(f"{arm:13} {sec:6.1f}s {summary[arm]['in'] or 0:10.0f} "
              f"{summary[arm]['out'] or 0:10.0f} {pas:4d}/{tot:<4d}")

    b, n = summary.get("v6"), summary.get("v6.1")
    if b and n and b["in"] and n["in"]:
        print(f"\nv6.1 相对 v6：输入 token {n['in'] - b['in']:+.0f} "
              f"（{(n['in']/b['in'] - 1) * 100:+.1f}%）· "
              f"耗时 {n['sec'] - b['sec']:+.1f}s · "
              f"准确 {n['pass']}/{n['total']} vs {b['pass']}/{b['total']}")
    (HERE / "v61_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
