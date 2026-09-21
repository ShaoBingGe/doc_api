"""JP 输出契约对齐（jp-v8 → jp-v9）的黄金集 A/B —— 验证「统一格式」没有牺牲识别质量。

改动是**契约层**的（enum 词表、补 originalInvoiceReferences、字段增删改名），
识别规则一字未动。但新增 ~8 个字段会分摊模型注意力，必须实测核心字段没退化。

黄金集 GT 只覆盖 11 个核心字段（不含 invoiceType，故 enum 改动无法用黄金集验证，
它是纯词表替换）。本脚本逐字段精确比对，并单独报「实体条数」是否一致——
切分错了比字段错更严重。

用法：cd backend && python eval_samples/schema_ab/jp_contract_ab.py [--repeats 1]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")
sys.path.insert(0, "eval_samples/schema_ab")

from translation_ab import _fix_unquoted_scalars                   # noqa: E402

from app.ocr_optimizer.service import template_loader              # noqa: E402
from app.ocr_optimizer.service.preset_init import build_v1_prompt  # noqa: E402
from app.processors.gemini_processor import GeminiProcessor        # noqa: E402

HERE = pathlib.Path(__file__).parent
REPO = HERE.parents[2]
GOLDEN = REPO / "backend/app/ocr_optimizer/eval/golden_set/JP"

OLD_YAML = REPO / "template_backups/JP_invoice_prompt.jp-v8.20260903.yaml"
NEW_YAML = REPO / "JP_invoice_prompt.yaml"

# GT 实际覆盖的字段（golden_set/JP/ground_truth/*.json 的并集）
CORE = ["docType", "nameOfInvoice", "invoiceNumber", "invoiceDate", "currency",
        "totalAmount", "totalTaxAmount", "billFromName",
        "billFromTaxIdentificationNumber", "billToName", "page"]

CONCURRENCY = 4      # 实测甜点（见 concurrency_probe.py）


def load_yaml(path: pathlib.Path) -> tuple[str, dict]:
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / "JP_invoice_prompt.yaml").write_text(
            _fix_unquoted_scalars(path.read_text(encoding="utf-8")), encoding="utf-8")
        orig = template_loader._REPO_ROOT
        template_loader._REPO_ROOT = pathlib.Path(td)
        try:
            d = template_loader.decompose_country_template("JP")
        finally:
            template_loader._REPO_ROOT = orig
    return build_v1_prompt(d), d["json_schema"]


def norm(v):
    """比较前的归一化：数字去尾零、字符串去首尾空白，None/'' 视为缺失。"""
    if v is None or v == "" or v == []:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if isinstance(v, list):
        return [norm(x) for x in v]
    s = str(v).strip()
    try:
        return round(float(s.replace(",", "")), 2)
    except ValueError:
        return s


def parse(raw):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        from app.processors.base import extract_json
        for b in extract_json(raw or ""):
            try:
                return json.loads(b)
            except (json.JSONDecodeError, TypeError):
                pass
        return None


def score(out, gt) -> tuple[dict[str, tuple[int, int]], bool]:
    """→ ({字段: (命中, 判据数)}, 条数是否一致)。GT 有值的字段才计分。"""
    outs = out if isinstance(out, list) else []
    outs = [e for e in outs if isinstance(e, dict)]
    gts = gt if isinstance(gt, list) else [gt]
    per: dict[str, tuple[int, int]] = {}
    for f in CORE:
        hit = tot = 0
        for i, g in enumerate(gts):
            exp = norm(g.get(f))
            if exp is None:            # GT 没覆盖该字段 → 不计分（避免假分）
                continue
            tot += 1
            got = norm(outs[i].get(f)) if i < len(outs) else None
            if got == exp:
                hit += 1
        if tot:
            per[f] = (hit, tot)
    return per, len(outs) == len(gts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    items = [it for it in manifest["items"]
             if (GOLDEN / it["doc"]).exists() and (GOLDEN / it["ground_truth"]).exists()]
    print(f"黄金集 {len(items)} 份（manifest 声明 {manifest.get('count')}）\n")

    variants = {"A_jp-v8_旧": load_yaml(OLD_YAML), "B_jp-v9_新": load_yaml(NEW_YAML)}
    for n, (p, _) in variants.items():
        print(f"  {n}: prompt {len(p):,} 字符")

    results: dict[str, dict] = {}
    for name, (prompt, schema) in variants.items():
        agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        count_ok = 0
        errors = 0

        def run(it):
            proc = GeminiProcessor()
            doc = GOLDEN / it["doc"]
            gt = json.loads((GOLDEN / it["ground_truth"]).read_text())
            for _ in range(3):                       # 传输失败重试，不计入质量
                try:
                    out = parse(proc.process_document(str(doc), prompt,
                                                      {"response_schema": schema}))
                    if out is not None:
                        return it, out, gt, None
                except Exception as exc:             # noqa: BLE001
                    err = str(exc)[:90]
                    time.sleep(3)
            return it, None, gt, locals().get("err", "parse failed")

        print(f"\n═══ {name} ═══", flush=True)
        for r in range(args.repeats):
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
                for it, out, gt, err in ex.map(run, items):
                    if err or out is None:
                        errors += 1
                        print(f"  ❌ {it['doc'].split('/')[-1][:34]} {err}", flush=True)
                        continue
                    per, cok = score(out, gt)
                    count_ok += cok
                    for f, (h, t) in per.items():
                        agg[f][0] += h
                        agg[f][1] += t
        tot_h = sum(v[0] for v in agg.values())
        tot_t = sum(v[1] for v in agg.values())
        results[name] = {"per_field": {f: tuple(v) for f, v in agg.items()},
                         "overall": tot_h / tot_t if tot_t else 0,
                         "count_ok": count_ok, "n": len(items) * args.repeats,
                         "errors": errors}
        print(f"  总体 {tot_h}/{tot_t} = {tot_h/tot_t:.4f} · "
              f"条数一致 {count_ok}/{len(items)*args.repeats} · 传输失败 {errors}")

    a, b = results["A_jp-v8_旧"], results["B_jp-v9_新"]
    print("\n═══ 逐字段对照（命中/判据）═══")
    print(f"  {'字段':<34} {'旧 v8':>12} {'新 v9':>12}   判定")
    for f in CORE:
        ah, at = a["per_field"].get(f, (0, 0))
        bh, bt = b["per_field"].get(f, (0, 0))
        if not at and not bt:
            continue
        ar = ah / at if at else 0
        br = bh / bt if bt else 0
        mark = "持平" if abs(br - ar) < 1e-9 else ("✅ 提升" if br > ar else "⚠️ 退化")
        print(f"  {f:<34} {ah:>4}/{at:<3}{ar:>5.0%} {bh:>4}/{bt:<3}{br:>5.0%}   {mark}")
    print(f"\n  {'总体':<34} {a['overall']:>11.4f} {b['overall']:>11.4f}   "
          f"{'✅ 不劣于旧版' if b['overall'] >= a['overall'] - 1e-9 else '⚠️ 退化，需回滚'}")
    print(f"  {'切分条数一致':<32} {a['count_ok']:>9}/{a['n']} {b['count_ok']:>9}/{b['n']}")

    (HERE / "jp_contract_ab_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n→ {HERE / 'jp_contract_ab_results.json'}")


if __name__ == "__main__":
    main()
