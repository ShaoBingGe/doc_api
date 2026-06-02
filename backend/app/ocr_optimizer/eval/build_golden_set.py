"""
Build a per-country golden eval set from existing confirmed (GT) samples.

Usage:
    python -m app.ocr_optimizer.eval.build_golden_set --country MY [--limit N] [--min-fields K]

For every document bound to a <country> ApiDef that has ground truth, copy the
source file into golden_set/<country>/docs/ and write its GT JSON into
golden_set/<country>/ground_truth/ (matching basename). Produces a manifest.json.

Dedup: the same physical invoice was re-uploaded under many ApiDefs, so we
dedup by FILE CONTENT HASH and keep the richest GT (most fields) per file.

CRITICAL — GT root shape:
    ground_truth.build() reassembles a DICT root (annotation field_names lost
    the leading "[0]" when the list-rooted structured_data was flattened). But
    module json_paths are array-rooted ("$[*].invoiceNumber") and the OCR
    output is a list. Slicing a dict GT with "$[*].x" yields None for every
    field — which is exactly the production scoring bug (see notes in
    README / the turn that discovered it). The golden set must measure TRUTH,
    so we wrap the GT in a single-element list: [gt_dict]. That makes it
    structurally match both the json_paths and the list-rooted OCR output.

Read-only against the DB; writes only under golden_set/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid

import app.models  # noqa: F401  — mapper warmup
import app.ocr_optimizer  # noqa: F401

_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.abspath(os.path.join(_EVAL_DIR, "..", "..", ".."))


def _strip_empty(node):
    """Recursively drop null / empty-string leaves and prune empty containers.

    Golden-set requirement #1: a golden GT must contain NO empty field — a
    null GT field that the model also returns null would be a false pass. By
    stripping empties, every field that survives in the golden GT is a real,
    verifiable value the model must extract. Returns None when nothing remains.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            sv = _strip_empty(v)
            if sv is not None:
                out[k] = sv
        return out or None
    if isinstance(node, list):
        out = [sv for sv in (_strip_empty(v) for v in node) if sv is not None]
        return out or None
    if node is None:
        return None
    if isinstance(node, str) and node.strip() == "":
        return None
    return node


def _leaf_count(node) -> int:
    if isinstance(node, dict):
        return sum(_leaf_count(v) for v in node.values())
    if isinstance(node, list):
        return sum(_leaf_count(v) for v in node)
    return 0 if node is None else 1


def _file_hash(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(filename: str) -> str:
    base = os.path.basename(filename or "doc")
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in base)[:120]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a country golden eval set from confirmed samples")
    ap.add_argument("--country", default="MY")
    ap.add_argument("--limit", type=int, default=0, help="cap exported docs (0 = all unique)")
    ap.add_argument("--min-fields", type=int, default=12,
                    help="skip docs with fewer NON-EMPTY GT leaf values (req 1: completeness)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.ocr_optimizer.service import ground_truth
    from app.models.document import Document

    country = args.country.upper()
    code_prefix = f"{country.lower()}-invoice%"
    out_dir = os.path.join(_EVAL_DIR, "golden_set", country)
    docs_dir = os.path.join(out_dir, "docs")
    gt_dir = os.path.join(out_dir, "ground_truth")
    os.makedirs(docs_dir, exist_ok=True)
    os.makedirs(gt_dir, exist_ok=True)

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT DISTINCT d.id, d.filename, d.storage_path, a.api_code "
                "FROM documents d JOIN api_definitions a ON a.id = d.api_definition_id "
                "WHERE a.api_code LIKE :p AND d.id IN ("
                "  SELECT document_id FROM annotations WHERE is_corrected=1 OR source='manual')"
            ),
            {"p": code_prefix},
        ).fetchall()

        # Dedup by file content hash, keep richest GT.
        best: dict[str, dict] = {}
        skipped_nofile = skipped_thin = 0
        for did, filename, storage_path, api_code in rows:
            abspath = os.path.join(_BACKEND_DIR, storage_path) if storage_path else ""
            fh = _file_hash(abspath)
            if not fh:
                skipped_nofile += 1
                continue
            gt = _strip_empty(ground_truth.build(db, uuid.UUID(str(did)))) or {}
            fields = _leaf_count(gt)
            if fields < args.min_fields:
                skipped_thin += 1
                continue
            entry = {
                "doc_id": str(did), "filename": filename, "abspath": abspath,
                "api_code": api_code, "gt": gt, "fields": fields, "hash": fh,
            }
            if fh not in best or fields > best[fh]["fields"]:
                best[fh] = entry

        ranked = sorted(best.values(), key=lambda e: -e["fields"])
        if args.limit > 0:
            ranked = ranked[:args.limit]

        manifest = []
        for e in ranked:
            stem = f"{e['hash'][:8]}_{_safe_name(e['filename'])}"
            doc_dst = os.path.join(docs_dir, stem)
            gt_dst = os.path.join(gt_dir, os.path.splitext(stem)[0] + ".json")
            if not args.dry_run:
                shutil.copy2(e["abspath"], doc_dst)
                # GT wrapped in a list → matches array-rooted json_paths + OCR list root.
                with open(gt_dst, "w", encoding="utf-8") as f:
                    json.dump([e["gt"]], f, ensure_ascii=False, indent=2)
            manifest.append({
                "doc": os.path.relpath(doc_dst, out_dir),
                "ground_truth": os.path.relpath(gt_dst, out_dir),
                "gt_fields": e["fields"],
                "source_api_code": e["api_code"],
                "source_doc_id": e["doc_id"],
                "file_md5": e["hash"],
            })

        if not args.dry_run:
            with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"country": country, "count": len(manifest), "items": manifest},
                          f, ensure_ascii=False, indent=2)
    finally:
        db.close()

    print(f"[golden:{country}] candidates={len(rows)}  unique-files={len(best)}  "
          f"exported={len(manifest)}  (skipped: no-file={skipped_nofile}, thin={skipped_thin})")
    print(f"  GT root is LIST-wrapped ([gt]) to match $[*]. json_paths + list OCR root.")
    for m in manifest:
        print(f"  - {m['gt_fields']:3} fields  {m['doc']}  (from {m['source_api_code']})")
    print(f"  → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
