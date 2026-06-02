"""
CLI: evaluate an ApiDef's ACTIVE composed_prompt against its confirmed
samples, and print a per-module accuracy report.

Usage:
    python -m app.ocr_optimizer.eval.run_eval --api <api_def_id> [--processor gemini]

Read-only: no rounds, no version bumps, no DB writes. This is the baseline
measurement for Prompt System v2 (see docs/prompt-system-v2-plan.md, Phase 0).
Run it before and after a prompt-structure change to confirm no regression.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

# Warm up mapper resolution (mirror tests/conftest) to dodge the cold-start
# circular import between app.models and app.ocr_optimizer.models.
import app.models  # noqa: F401
import app.ocr_optimizer  # noqa: F401


def _load_active(db, api_def_id: uuid.UUID):
    """Return (api_def, active_version, module_specs)."""
    from app.models.api_definition import ApiDefinition
    from app.ocr_optimizer.models import (
        OcrModule, OcrPromptVersion, PromptVersionStatus,
    )
    from .harness import module_specs_from_orm

    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise SystemExit(f"ApiDefinition {api_def_id} not found")
    version = (
        db.query(OcrPromptVersion)
        .filter(
            OcrPromptVersion.api_definition_id == api_def_id,
            OcrPromptVersion.status == PromptVersionStatus.active.value,
        )
        .first()
    )
    if not version:
        raise SystemExit(f"ApiDefinition {api_def_id} has no active version")
    modules = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == version.id)
        .order_by(OcrModule.order_index)
        .all()
    )
    return api_def, version, module_specs_from_orm(modules)


def _confirmed_samples(db, api_def) -> dict[str, dict]:
    """{str(doc_id): gt_json} for every confirmed (GT-having) sample."""
    from app.ocr_optimizer.service import ground_truth

    cfg = api_def.config or {}
    raw = cfg.get("sample_document_ids") or []
    if isinstance(raw, str):
        raw = [raw]
    gts: dict[str, dict] = {}
    for sid in raw:
        try:
            suid = uuid.UUID(str(sid))
        except (ValueError, TypeError):
            continue
        gt = ground_truth.build(db, suid)
        if gt:
            gts[str(suid)] = gt
    return gts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline prompt eval for an ApiDef")
    parser.add_argument("--api", required=True, help="ApiDefinition id (uuid)")
    parser.add_argument("--processor", default="gemini", help="processor spec (gemini|mock)")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)

    from app.core.database import SessionLocal
    from .harness import evaluate_prompt

    db = SessionLocal()
    try:
        api_def, version, specs = _load_active(db, uuid.UUID(args.api))
        gts = _confirmed_samples(db, api_def)
        if not gts:
            raise SystemExit("No confirmed (GT) samples for this ApiDef")
        report = evaluate_prompt(
            db,
            modules=specs,
            sample_doc_ids=[uuid.UUID(d) for d in gts.keys()],
            composed_prompt=version.composed_prompt or "",
            composed_schema=version.composed_schema,
            ground_truths=gts,
            processor_spec=args.processor,
            model_name=getattr(api_def, "model_name", None),
        )
    finally:
        db.close()

    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"ApiDef {args.api}  active v{version.version}")
    print(f"overall_accuracy = {payload['overall_accuracy']}  "
          f"({payload['sample_count']} samples × {payload['module_count']} modules)")
    if payload["ocr_error_doc_ids"]:
        print(f"OCR errors on: {payload['ocr_error_doc_ids']}")
    print("-" * 60)
    for m in sorted(payload["modules"], key=lambda x: x["accuracy"]):
        print(f"  {m['accuracy']:.4f}  {m['matched']:>6}  {m['module_key']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
