"""Phase 0 摸底：同一 prompt、同一模型，「传 response_schema」vs「不传」逐样本对照。

直接调 GeminiProcessor（与生产同一个类），prompt 用国家模板组装出的完整
生产 prompt（build_v1_prompt，即线上 active 版本的同款文本）。
每个样本各跑两轮取稳定性。零服务依赖，只需 backend/.env 的 key。

用法：cd backend && .venv/bin/python eval_samples/schema_ab/run_ab.py [--rounds 1]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, ".")

from app.ocr_optimizer.service import template_loader          # noqa: E402
from app.ocr_optimizer.service.preset_init import build_v1_prompt  # noqa: E402
from app.processors.gemini_processor import GeminiProcessor    # noqa: E402

HERE = pathlib.Path(__file__).parent
SAMPLES = [
    ("S1_single_invoice.pdf", "单页扫描发票（注册号有印刷噪声）"),
    ("S2_six_invoices.pdf", "6页6张独立发票（无文本层，考切分+数组根）"),
    ("S3_credit_note.pdf", "Credit Note（负金额+原发票引用，考enum强制）"),
]


def parse(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        from app.processors.base import extract_json
        blocks = extract_json(raw or "")
        if blocks:
            try:
                return json.loads(blocks[0])
            except (json.JSONDecodeError, TypeError):
                pass
        return {"_raw": (raw or "")[:200]}


def summarize(out) -> dict:
    """把一次输出压成可对照的指纹。"""
    if isinstance(out, dict):
        ents = out.get("entities") or out.get("data") or [out]
        root = "object"
    elif isinstance(out, list):
        ents, root = out, "array"
    else:
        return {"root": type(out).__name__}
    ents = [e for e in ents if isinstance(e, dict)]
    return {
        "root": root,
        "n": len(ents),
        "docTypes": [e.get("docType") for e in ents],
        "invoiceTypes": [e.get("invoiceType") for e in ents if e.get("docType") != "other"],
        "invoiceNumbers": [e.get("invoiceNumber") for e in ents if e.get("docType") != "other"],
        "totals": [e.get("totalAmount") for e in ents if e.get("docType") != "other"],
        "brn": [e.get("billFromBusinessRegistrationNumber") for e in ents
                if e.get("docType") != "other"],
        "origRefs": [len(e.get("originalInvoiceReferences") or []) for e in ents
                     if e.get("invoiceType") == "Credit Note"],
        "lineRows": [len(e.get("detailOfGoodsOrServices") or []) for e in ents
                     if e.get("docType") != "other"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()

    d = template_loader.decompose_country_template("MY")
    prompt = build_v1_prompt(d)
    schema = d["json_schema"]
    print(f"prompt {len(prompt)} 字符 · schema 字段数 "
          f"{len(schema['items']['anyOf'][0]['properties'])}\n")

    proc = GeminiProcessor()
    results = {}
    for fname, tag in SAMPLES:
        path = str(HERE / fname)
        for mode, runtime in [("无schema", None),
                              ("传schema", {"response_schema": schema})]:
            for r in range(args.rounds):
                key = f"{fname}|{mode}|r{r}"
                try:
                    raw = proc.process_document(path, prompt, runtime)
                    results[key] = summarize(parse(raw))
                except Exception as exc:  # noqa: BLE001
                    results[key] = {"_error": str(exc)[:200]}
                s = results[key]
                print(f"[{fname[:2]} {mode} r{r}] {json.dumps(s, ensure_ascii=False)[:220]}")
        print()

    out = HERE / "ab_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
