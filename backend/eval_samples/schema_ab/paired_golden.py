"""P2 · 全池逐份配对评测 —— 把「模型抖动」与「模板导致的系统性差异」分开。

R2 的教训：`sample_batch` 每批硬上限 5 份，一份文档翻转即 ±0.20；且本会话
实测 temp=0/seed=42 仍有**跨时间窗漂移**（同配置 seed=7 两次测得 0.4667 /
0.5074）。批次均值在这种噪声下没有判别力。

本工具的口径：
  1. **全池**：黄金池覆盖核心字段集的全部文档（MY=24 份），不抽样——
     消灭「这批抽到谁」的方差；
  2. **配对**：同一份文档在 A/B 两版模板下比，看的是差值不是均值；
  3. **重复**：每个 (文档, 版本) 跑 N 次（默认 2），先量出**该文档自身的
     抖动幅度**，再判 A/B 差异是否超出抖动——
       STABLE  : 各重复完全一致
       JITTER  : 同版本两次重复结果就不同 → 该文档的差异不可归因于模板
       SYSTEMATIC: 版本内稳定、版本间稳定不同 → 才是模板的真实影响。

用法（对比 7_13 与「删 §1.34」变体）：
  cd backend && .venv/bin/python eval_samples/schema_ab/paired_golden.py \
      --cut '## 1.34' --repeats 2
`--cut` 给一个正文小节标题前缀，B 版 = A 版删掉该小节（到下一个同级标题）。
不传 --cut 时 B=A，可用来单纯测量全池抖动基线。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import uuid as uuidlib
from collections import defaultdict

sys.path.insert(0, ".")

from app.core.database import SessionLocal                     # noqa: E402
from app.models.api_definition import ApiDefinition            # noqa: E402
from app.ocr_optimizer.eval import golden_loop                 # noqa: E402
from app.ocr_optimizer.eval.harness import (                   # noqa: E402
    module_specs_from_orm,
    score_outputs,
)
from app.ocr_optimizer.models import (                         # noqa: E402
    OcrModule,
    OcrPromptVersion,
    PromptVersionStatus,
)
from app.ocr_optimizer.service.ocr_runner import run_ocr_on_samples  # noqa: E402

HERE = pathlib.Path(__file__).parent


def cut_section(body: str, marker: str) -> str:
    """删掉以 marker 开头的小节（到下一个同级 `## ` 标题为止）。"""
    lines = body.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ln.lstrip().startswith(marker)]
    if not starts:
        raise SystemExit(f"正文中找不到小节 {marker!r}")
    i = starts[0]
    level = len(lines[i].lstrip()) - len(lines[i].lstrip().lstrip("#"))
    j = i + 1
    while j < len(lines):
        st = lines[j].lstrip()
        if st.startswith("#") and (len(st) - len(st.lstrip("#"))) <= (
                len(lines[i].lstrip()) - len(lines[i].lstrip().lstrip("#"))):
            break
        j += 1
    return "".join(lines[:i] + lines[j:])


def per_doc_scores(report) -> dict[str, float]:
    """EvalReport → {doc_id: 该文档所有被测模块的平均准确率}。"""
    acc: dict[str, list[float]] = defaultdict(list)
    for m in report.module_scores:
        for s in m.per_sample:
            acc[s["doc_id"]].append(s["accuracy"])
    return {d: sum(v) / len(v) for d, v in acc.items() if v}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="MY")
    ap.add_argument("--candidate", default="my-invoice-c99a19")
    ap.add_argument("--cut", default=None,
                    help="B 版删除的正文小节标题前缀，如 '## 1.34'")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--jitter-band", type=float, default=0.02,
                    help="配对差 ≤ 此值视为持平")
    args = ap.parse_args()

    db = SessionLocal()
    api = db.query(ApiDefinition).filter_by(api_code=args.candidate).first()
    v = (db.query(OcrPromptVersion)
         .filter_by(api_definition_id=api.id,
                    status=PromptVersionStatus.active.value).first())
    mods = (db.query(OcrModule).filter_by(prompt_version_id=v.id)
            .order_by(OcrModule.order_index).all())
    specs = module_specs_from_orm(mods)

    golden = golden_loop.load_golden(args.country)
    core = set(golden_loop.compute_core_fields(args.country))
    pool = [d for d, e in golden.items()
            if core <= golden_loop._top_keys(e["gt"])]
    gts = {str(uuidlib.UUID(d)): golden[d]["gt"] for d in pool}
    ids = [uuidlib.UUID(d) for d in pool]

    prompt_a = v.composed_prompt or ""
    prompt_b = cut_section(prompt_a, args.cut) if args.cut else prompt_a
    variants = {"A": prompt_a, "B": prompt_b}
    print(f"全池 {len(pool)} 份 · A={len(prompt_a):,} 字符 · "
          f"B={len(prompt_b):,} 字符"
          + (f"（删 {args.cut!r}，−{len(prompt_a)-len(prompt_b)} 字符）" if args.cut else "（B=A，抖动基线模式）")
          + f" · 每配置重复 {args.repeats} 次\n")

    # {variant: [ {doc: score} per repeat ]}
    runs: dict[str, list[dict[str, float]]] = {k: [] for k in variants}
    for name, prompt in variants.items():
        for r in range(args.repeats):
            outputs = run_ocr_on_samples(
                db, sample_document_ids=ids, composed_prompt=prompt,
                composed_schema=v.composed_schema,
                processor_spec=api.processor_type or "gemini",
                model_name=api.model_name,
            )
            report = score_outputs(specs, outputs, gts, strict=True)
            scores = per_doc_scores(report)
            runs[name].append(scores)
            mean = sum(scores.values()) / len(scores) if scores else 0.0
            errs = len(report.ocr_error_doc_ids)
            print(f"  {name} r{r}: 全池均值 {mean:.4f}"
                  f"（{len(scores)} 份有效" + (f"，{errs} 份传输失败" if errs else "") + "）")

    # ── 逐份配对分析 ──
    print(f"\n═══ 逐份配对（A vs B，配对差 > {args.jitter_band} 才计入）═══")
    def spread(vals):  # 同配置重复间的最大差 = 该文档抖动幅度
        return max(vals) - min(vals) if vals else 0.0

    n_sys = n_jit = n_eq = 0
    rows = []
    for d in sorted(gts.keys()):
        a_vals = [r.get(d) for r in runs["A"] if r.get(d) is not None]
        b_vals = [r.get(d) for r in runs["B"] if r.get(d) is not None]
        if not a_vals or not b_vals:
            continue
        a_m, b_m = sum(a_vals)/len(a_vals), sum(b_vals)/len(b_vals)
        jit = max(spread(a_vals), spread(b_vals))
        diff = b_m - a_m
        if abs(diff) <= args.jitter_band:
            cls = "EQ"; n_eq += 1
        elif jit >= abs(diff):
            cls = "JITTER"; n_jit += 1     # 自身抖动就 ≥ 版本差 → 不可归因
        else:
            cls = "SYSTEMATIC"; n_sys += 1
        rows.append({"doc": d, "A": round(a_m,4), "B": round(b_m,4),
                     "diff": round(diff,4), "self_jitter": round(jit,4), "cls": cls})
        if cls != "EQ":
            print(f"  {cls:10} {d[:8]}  A={a_m:.3f} B={b_m:.3f} "
                  f"diff={diff:+.3f}  自抖动={jit:.3f}")

    a_all = [s for r in runs["A"] for s in r.values()]
    b_all = [s for r in runs["B"] for s in r.values()]
    print(f"\n═══ 汇总 ═══")
    print(f"  A 全池均值 {sum(a_all)/len(a_all):.4f} · B 全池均值 {sum(b_all)/len(b_all):.4f}")
    print(f"  持平 {n_eq} · 抖动（不可归因）{n_jit} · 系统性差异 {n_sys}")
    if n_sys:
        sys_diffs = [r["diff"] for r in rows if r["cls"] == "SYSTEMATIC"]
        print(f"  系统性差异净效应: {sum(sys_diffs):+.4f}（正=B 好）")
    (HERE / "paired_golden_results.json").write_text(
        json.dumps({"pool": len(pool), "cut": args.cut,
                    "repeats": args.repeats, "rows": rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
