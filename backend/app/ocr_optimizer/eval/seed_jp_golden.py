"""
Seed a JP customer ApiDef + GT documents from the Japan-inv/train labelled set.

This script makes the JP country REPRODUCIBLE end-to-end with the SAME machinery
MY uses — it does NOT special-case the golden pipeline. It only does what a real
JP customer would have done by hand:

  1. Create a `jp-invoice-<hex>` ApiDefinition + v1 OcrPromptVersion + modules
     from JP_invoice_prompt.yaml  (preset_init.init_from_country_template).
  2. For N high-quality train samples: copy the PDF into UPLOAD_DIR, create a
     Document row bound to that ApiDef, and write its human label as GT
     Annotations (source='manual', is_corrected=True) — exactly the rows
     ground_truth.build() reads.

After seeding, build the frozen golden set with the EXISTING, country-agnostic
builder (proving 机器国别无关):

    python -m app.ocr_optimizer.eval.seed_jp_golden --limit 20
    python -m app.ocr_optimizer.eval.build_golden_set --country JP --min-fields 8

Sample selection (quality-first, per golden req 1 = no empty GT, rich fields):
  - exactly ONE entity in the label (golden GT root = single invoice dict)
  - docType in {invoice, receipt}
  - at least `--min-core` of the 10 core scalar fields present & non-empty

Idempotency: re-running creates a NEW jp-invoice ApiDef each time (fresh hex).
Pass --reuse <api_code> to add docs to an existing one instead.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid

import app.models  # noqa: F401  — mapper warmup (break circular import)
import app.ocr_optimizer  # noqa: F401

_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.abspath(os.path.join(_EVAL_DIR, "..", "..", ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
_TRAIN_DIR = os.path.join(_REPO_ROOT, "Japan-inv", "train")

# The finalized JP core field set (must match JP_invoice_prompt.yaml schema).
# Scalars are scored by exact field name; `page` is the one structural array.
_CORE_SCALARS = [
    "docType", "nameOfInvoice", "invoiceNumber", "invoiceDate", "currency",
    "totalAmount", "totalTaxAmount", "billFromName",
    "billFromTaxIdentificationNumber", "billToName",
]
_NUMBER_FIELDS = {"totalAmount", "totalTaxAmount"}
_DATE_FIELDS = {"invoiceDate"}


def _nonempty(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def _core_count(entity: dict) -> int:
    return sum(1 for k in _CORE_SCALARS if _nonempty(entity.get(k)))


def _load_candidates(min_core: int) -> list[dict]:
    """Return ranked single-entity, core-rich train samples (richest first)."""
    labels_dir = os.path.join(_TRAIN_DIR, "labels")
    docs_dir = os.path.join(_TRAIN_DIR, "docs")
    out: list[dict] = []
    for fn in sorted(os.listdir(labels_dir)):
        if not fn.endswith(".pdf.json"):
            continue
        pdf_name = fn[: -len(".json")]  # strip trailing .json → "<x>.pdf"
        pdf_path = os.path.join(docs_dir, pdf_name)
        if not os.path.isfile(pdf_path):
            continue
        try:
            data = json.load(open(os.path.join(labels_dir, fn), encoding="utf-8"))
        except Exception:
            continue
        entities = data.get("entities") or []
        if len(entities) != 1:
            continue  # golden GT root assumes a single invoice per doc
        e = entities[0]
        if e.get("docType") not in ("invoice", "receipt"):
            continue
        cc = _core_count(e)
        if cc < min_core:
            continue
        out.append({"pdf_name": pdf_name, "pdf_path": pdf_path, "entity": e, "core": cc})
    out.sort(key=lambda r: -r["core"])
    return out


def _entity_to_annotations(entity: dict) -> list[dict]:
    """Map a JP label entity → flat GT annotation rows (only the core fields)."""
    rows: list[dict] = []
    for k in _CORE_SCALARS:
        v = entity.get(k)
        if not _nonempty(v):
            continue
        ftype = "number" if k in _NUMBER_FIELDS else ("date" if k in _DATE_FIELDS else "string")
        if k in _NUMBER_FIELDS:
            # store an integer string when the value is whole (yen is integer)
            fv = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
        else:
            fv = str(v)
        rows.append({"field_name": k, "field_value": fv, "field_type": ftype})
    # page → page[i] (number leaves)
    page = entity.get("page")
    if isinstance(page, list) and page:
        for i, p in enumerate(page):
            if _nonempty(p):
                rows.append({
                    "field_name": f"page[{i}]",
                    "field_value": str(int(p)) if isinstance(p, (int, float)) else str(p),
                    "field_type": "number",
                })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed a JP ApiDef + GT docs from Japan-inv/train")
    ap.add_argument("--limit", type=int, default=20, help="how many samples to seed (>=15 recommended)")
    ap.add_argument("--min-core", type=int, default=8, help="min non-empty core scalar fields per sample")
    ap.add_argument("--reuse", default="", help="add docs to an existing jp-invoice api_code instead of creating one")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.models.annotation import Annotation, AnnotationSource, FieldType
    from app.models.api_definition import ApiDefinition
    from app.models.document import Document, DocumentStatus
    from app.ocr_optimizer.service import preset_init

    cands = _load_candidates(args.min_core)
    print(f"[seed:JP] candidates (single-entity, >={args.min_core} core fields): {len(cands)}")
    if len(cands) < args.limit:
        print(f"  note: only {len(cands)} candidates available, seeding all of them")
    chosen = cands[: args.limit]
    if args.dry_run:
        for r in chosen:
            print(f"  - core={r['core']:2}  {r['pdf_name']}")
        print(f"[seed:JP] DRY RUN — would seed {len(chosen)} docs")
        return 0

    settings = get_settings()
    upload_dir = os.path.join(_BACKEND_DIR, "data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    db = SessionLocal()
    try:
        if args.reuse:
            api = db.query(ApiDefinition).filter(ApiDefinition.api_code == args.reuse).first()
            if not api:
                raise SystemExit(f"--reuse api_code {args.reuse!r} not found")
            api_id = api.id
            api_code = api.api_code
        else:
            res = preset_init.init_from_country_template(db, "JP")
            api_id = uuid.UUID(res["api_definition_id"])
            api = db.get(ApiDefinition, api_id)
            api_code = api.api_code
            print(f"[seed:JP] created ApiDef api_code={api_code}  modules={res['module_count']}")

        seeded = 0
        for r in chosen:
            safe = f"{uuid.uuid4().hex}_{''.join(c if c.isalnum() or c in '._-' else '_' for c in r['pdf_name'])[:100]}"
            dest_abs = os.path.join(upload_dir, safe)
            shutil.copy2(r["pdf_path"], dest_abs)
            storage_path = f"data/uploads/{safe}"  # relative to backend (build_golden_set joins _BACKEND_DIR)

            doc = Document(
                id=uuid.uuid4(),
                filename=r["pdf_name"],
                file_type="pdf",
                file_size=os.path.getsize(dest_abs),
                storage_path=storage_path,
                api_definition_id=api_id,
                status=DocumentStatus.completed.value,
                tenant_id=None,  # platform bucket
            )
            db.add(doc)
            db.flush()  # populate doc.id

            for a in _entity_to_annotations(r["entity"]):
                db.add(Annotation(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    field_name=a["field_name"],
                    field_value=a["field_value"],
                    field_type=a["field_type"],
                    source=AnnotationSource.manual.value,
                    is_corrected=True,
                ))
            seeded += 1

        db.commit()
        print(f"[seed:JP] seeded {seeded} documents with manual GT under api_code={api_code}")
        print("[seed:JP] next:")
        print("  python -m app.ocr_optimizer.eval.build_golden_set --country JP --min-fields 8")
        print(f"  python -m app.ocr_optimizer.eval.run_golden_batch --country JP --candidate {api_code}")
        print("  (--processor 省略则跟随 DEFAULT_PROCESSOR：大陆部署=qwen，需设 QWEN_API_KEY)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
