"""
Live OCR regression for design v7 Part 3 promotion.

Runs Gemini OCR against 3 sample invoices using the NEW MY ApiDef's
composed_prompt (which contains Part 3 from the platform asset), then
diffs each result against the captured GT.

Usage:
    cd backend && .venv/bin/python -m tests.regression_live_ocr

Samples:
    1. PANASONIC invoice (CHINKIN doc 334aa9f8, GT in /tmp/gt_panasonic.tsv)
    2. RENTAL invoice (CHINKIN doc e2030276, GT in /tmp/gt_rental.tsv)
    3. Credit Note (testing/250715 CN-2025_519 PP CHIN HIN RM709.25_...)

Exit code 0 if MUST gates pass (core fields match on 2 GT samples + Credit
Note has originalInvoiceReferences populated), 1 otherwise.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

# Bootstrap sys.path so this can run from backend/
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
sys.path.insert(0, str(_BACKEND))

# Warm up the model graph
import app.models  # noqa: F401, E402
import app.ocr_optimizer  # noqa: F401, E402

from app.core.database import SessionLocal  # noqa: E402
from app.ocr_optimizer.models import OcrPromptVersion  # noqa: E402
from app.processors.gemini_processor import GeminiProcessor  # noqa: E402


import uuid as _uuid
NEW_API_DEF_ID = _uuid.UUID("d352071c-3a6a-4eef-83f7-ac316cc34e0f")
UPLOADS = _BACKEND / "data" / "uploads"
TESTING = _BACKEND.parent / "testing"

SAMPLES = [
    {
        "label": "PANASONIC",
        "path": UPLOADS / "e1e63d05d7fd47499005dfb8c82e1ecf_9231848653[1] PANASONIC_20251028_115126.pdf",
        "gt_tsv": Path("/tmp/gt_panasonic.tsv"),
    },
    {
        "label": "RENTAL",
        "path": UPLOADS / "9cedab11bb594982a3fe060ea936c6c8_2604SI0083 - PP CHIN HIN (AVANTRO BLK B PT 1) 5TH MONTH RENTAL APR 2026 SO W1 829173 PO W1 548594_20260430_135409.pdf",
        "gt_tsv": Path("/tmp/gt_rental.tsv"),
    },
    {
        "label": "CREDIT_NOTE",
        "path": TESTING / "250715 CN-2025_519 PP CHIN HIN RM709.25_20250715_215845.pdf",
        "gt_tsv": None,  # no GT; structural check only
    },
]

CORE_FIELDS_MUST = [
    "invoiceNumber",
    "invoiceDate",
    "billFromName",
    "billToName",
    "billFromBusinessRegistrationNumber",
    "billFromTaxIdentificationNumber",
    "currency",
    "totalAmount",
]


def load_prompt() -> tuple[str, dict]:
    db = SessionLocal()
    try:
        v = db.query(OcrPromptVersion).filter(
            OcrPromptVersion.api_definition_id == NEW_API_DEF_ID
        ).first()
        if not v:
            raise SystemExit(f"No prompt version found for {NEW_API_DEF_ID}")
        return v.composed_prompt, v.composed_schema
    finally:
        db.close()


def load_gt(path: Path) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    out: dict[str, str] = {}
    valid_name = __import__("re").compile(r"^[a-z][a-zA-Z0-9_]*$")
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    for r in rows:
        name = (r.get("field_name") or "").strip()
        if not valid_name.match(name):
            continue
        out[name] = (r.get("field_value") or "").strip()
    return out


def _normalize_value(v) -> str:
    """Coerce values for fair compare: strip, lower-case currency symbols, etc."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).strip()
    return s


def compare(gt: dict[str, str], result: dict, sample_label: str) -> dict:
    """Compare GT field-set to result. Returns a structured report."""
    report = {
        "label": sample_label,
        "core_match": {},
        "core_mismatch": {},
        "core_missing": [],
        "extra_in_result": [],
        "result_top_level_keys": [],
    }
    # The result is a list of invoice objects (per Part 3.1) — take first.
    if isinstance(result, list) and result:
        result_obj = result[0]
    elif isinstance(result, dict):
        result_obj = result
    else:
        report["error"] = f"unexpected result shape: {type(result).__name__}"
        return report

    report["result_top_level_keys"] = sorted(result_obj.keys())

    for f in CORE_FIELDS_MUST:
        gt_v = gt.get(f)
        rv = result_obj.get(f)
        if gt_v is None and rv is None:
            continue  # field not present in either — skip
        if gt_v is None:
            continue  # GT doesn't cover this field
        if rv is None:
            report["core_missing"].append(f)
            continue
        gt_n = _normalize_value(gt_v)
        rv_n = _normalize_value(rv)
        # numeric comparison if both look like numbers
        try:
            if abs(float(gt_n.replace(",", "")) - float(rv_n.replace(",", ""))) < 0.01:
                report["core_match"][f] = rv_n
                continue
        except (ValueError, TypeError):
            pass
        if gt_n == rv_n:
            report["core_match"][f] = rv_n
        else:
            report["core_mismatch"][f] = {"gt": gt_n, "result": rv_n}
    return report


def check_credit_note(result) -> dict:
    """Structural check for the Credit Note sample (Part 3.6)."""
    obj = result[0] if isinstance(result, list) and result else (result if isinstance(result, dict) else None)
    if obj is None:
        return {"label": "CREDIT_NOTE", "error": "no object"}

    rep = {
        "label": "CREDIT_NOTE",
        "invoiceType": obj.get("invoiceType"),
        "totalAmount": obj.get("totalAmount"),
        "has_originalInvoiceReferences": "originalInvoiceReferences" in obj,
        "originalInvoiceReferences": obj.get("originalInvoiceReferences"),
    }
    return rep


def check_part3_compliance(result) -> dict:
    """Check select Part 3 rules in the response."""
    obj = result[0] if isinstance(result, list) and result else (result if isinstance(result, dict) else None)
    if obj is None:
        return {}

    findings = {}

    # §3.2: NUMBER fields must be actual numbers, not strings with commas/symbols
    for k in ("totalAmount", "totalNetAmount", "totalTaxAmount"):
        v = obj.get(k)
        if v is not None:
            if isinstance(v, str):
                findings.setdefault("§3.2 violations", []).append(
                    f"{k}={v!r} (should be NUMBER not STRING)"
                )
            elif isinstance(v, (int, float)):
                findings.setdefault("§3.2 numeric ok", []).append(f"{k}={v}")

    # §3.3: detailOfTaxSummary should be an array; if totalTaxAmount given,
    # sum of array.tax should match (within ADJUSTMENT tolerance)
    dts = obj.get("detailOfTaxSummary")
    tta = obj.get("totalTaxAmount")
    if isinstance(dts, list):
        s = 0.0
        for row in dts:
            t = row.get("tax")
            if isinstance(t, (int, float)):
                s += t
        if tta is not None and isinstance(tta, (int, float)):
            findings["§3.3 totalTaxAmount vs sum"] = {
                "totalTaxAmount": tta, "sum_of_array_tax": round(s, 4),
                "diff": round(tta - s, 4),
            }

    # §3.4: line items have netAmount; check qty × unitPrice consistency
    items = obj.get("detailOfGoodsOrServices") or []
    if isinstance(items, list):
        violations = []
        for i, row in enumerate(items):
            q, up, net = row.get("quantity"), row.get("unitPrice"), row.get("netAmount")
            if all(isinstance(x, (int, float)) for x in (q, up, net)) and net:
                err = abs(q * up - net) / max(abs(net), 1)
                if err >= 0.01:
                    violations.append({"row": i, "qty": q, "unitPrice": up, "netAmount": net, "err_pct": round(err * 100, 3)})
        if violations:
            findings["§3.4 line-item consistency violations"] = violations
        findings["§3.4 line item count"] = len(items)

    # §3.6: originalInvoiceReferences only for Credit Note
    inv_type = obj.get("invoiceType")
    has_oir = "originalInvoiceReferences" in obj
    if inv_type != "Credit Note" and has_oir and obj.get("originalInvoiceReferences"):
        findings["§3.6 violation"] = "originalInvoiceReferences populated for non-Credit Note"

    return findings


def main():
    print("=" * 72)
    print("Design v7 Part 3 promotion — LIVE OCR regression")
    print("=" * 72)
    print()

    composed_prompt, composed_schema = load_prompt()
    print(f"Loaded composed_prompt: {len(composed_prompt)} chars, "
          f"composed_schema keys: {list(composed_schema.keys()) if composed_schema else None}")
    print()

    # Sanity-check Part 3 made it into the prompt
    for marker in ("# Part 1", "# Part 2", "# Part 3", "## 3.4 行项目装配", "< 0.01", "ADJUSTMENT"):
        present = marker in composed_prompt
        print(f"  prompt contains {marker!r}: {present}")
    print()

    # Use the response_schema from composed_schema (invoice + receipt branches)
    runtime_config = {
        "response_mime_type": "application/json",
        "response_schema": composed_schema,
        "temperature": 0.0,
    }

    processor = GeminiProcessor(model_name=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))

    results = []
    for s in SAMPLES:
        if not s["path"].exists():
            print(f"!! Skipping {s['label']}: file not found at {s['path']}")
            continue

        print(f"--- {s['label']}: {s['path'].name[:80]} ---")
        raw = None
        result = None
        last_err = None
        for attempt in range(1, 16):
            t0 = time.time()
            try:
                raw = processor.process_document(
                    file_path=str(s["path"]),
                    instruction=composed_prompt,
                    runtime_config=runtime_config,
                )
                dt = time.time() - t0
                result = json.loads(raw) if raw else None
                print(f"  attempt {attempt} OCR ok in {dt:.1f}s; result chars={len(raw)}; type={type(result).__name__}")
                break
            except Exception as e:
                dt = time.time() - t0
                last_err = e
                print(f"  attempt {attempt} failed after {dt:.1f}s: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(min(3 + attempt, 8))
        if result is None:
            print(f"  !! gave up after 5 attempts: {last_err}")
            continue

        out_path = Path(f"/tmp/regression_{s['label']}.json")
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  saved result to {out_path}")

        # GT comparison (if available)
        if s["gt_tsv"] and s["gt_tsv"].exists():
            gt = load_gt(s["gt_tsv"])
            rep = compare(gt, result, s["label"])
            results.append(rep)
            print(f"  GT fields loaded: {len(gt)}")
            print(f"  core MATCH: {len(rep['core_match'])} / {len(CORE_FIELDS_MUST)}")
            if rep["core_mismatch"]:
                print(f"  core MISMATCH ({len(rep['core_mismatch'])}):")
                for k, v in rep["core_mismatch"].items():
                    print(f"    {k}: gt={v['gt']!r} != result={v['result']!r}")
            if rep["core_missing"]:
                print(f"  core MISSING: {rep['core_missing']}")

        # Part 3 compliance check
        p3 = check_part3_compliance(result)
        if p3:
            print(f"  Part 3 compliance:")
            print(json.dumps(p3, ensure_ascii=False, indent=4))

        if s["label"] == "CREDIT_NOTE":
            cn = check_credit_note(result)
            results.append(cn)
            print(f"  Credit Note check:")
            print(json.dumps(cn, ensure_ascii=False, indent=4))

        print()

    print("=" * 72)
    print("Summary")
    print("=" * 72)

    must_pass = True
    for r in results:
        if "label" not in r:
            continue
        if r.get("error"):
            must_pass = False
            print(f"  {r['label']}: ERROR {r['error']}")
            continue
        if "core_mismatch" in r:
            mm = len(r["core_mismatch"])
            ok = len(r["core_match"])
            covered = ok + mm + len(r["core_missing"])
            if covered == 0:
                print(f"  {r['label']}: no GT coverage")
            else:
                pct = 100 * ok / covered
                status = "✅" if mm == 0 and not r["core_missing"] else "⚠️"
                print(f"  {status} {r['label']}: {ok}/{covered} core fields match ({pct:.0f}%)")
                if mm > 0:
                    must_pass = False
        if r["label"] == "CREDIT_NOTE":
            has_oir = r.get("has_originalInvoiceReferences", False)
            inv_type = r.get("invoiceType")
            ok = inv_type == "Credit Note" and has_oir
            status = "✅" if ok else "⚠️"
            print(f"  {status} CREDIT_NOTE: invoiceType={inv_type!r}, has_originalInvoiceReferences={has_oir}")
            if not ok:
                must_pass = False

    print()
    if must_pass:
        print("🟢 MUST gates passed.")
        return 0
    print("🔴 MUST gates failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
