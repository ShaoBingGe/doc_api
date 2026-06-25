"""
CLI: run a comparable golden STRICT batch for a candidate prompt.

Usage:
    python -m app.ocr_optimizer.eval.run_golden_batch \
        --country MY --candidate my-invoice-abf4f0 --size 5 [--seed 42] [--processor gemini]

Picks <=5 random golden seeds that all cover the country's core field set, OCRs
them with the candidate ApiDef's ACTIVE composed_prompt, scores ONLY the core
fields with zero-tolerance exact match, and prints accuracy + per-field
deviations. Needs a live OCR backend (gemini).

This is the platform-side gate (CLAUDE.md: golden门槛 = 离线平台 CI) — run it when
changing the shared machinery (composer/skill/reconciler) to confirm no
regression. It never runs inside a customer iteration.
"""

from __future__ import annotations

import argparse
import json
import sys

import app.models  # noqa: F401  — mapper warmup
import app.ocr_optimizer  # noqa: F401


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Golden strict batch for a candidate prompt")
    ap.add_argument("--country", default="MY")
    ap.add_argument("--candidate", required=True, help="candidate ApiDef api_code (its active version is evaluated)")
    ap.add_argument("--size", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=None, help="rng seed (omit = random each run)")
    ap.add_argument("--processor", default=None,
                    help="OCR backend; omit to follow DEFAULT_PROCESSOR "
                         "(大陆部署=qwen / 海外=gemini)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    # Don't hard-pin gemini — the production processor is deployment-specific
    # (CLAUDE.md §四⑥). Default to the configured DEFAULT_PROCESSOR so the
    # baseline reflects what production actually runs (qwen on mainland).
    if not args.processor:
        from app.core.config import get_settings
        args.processor = (get_settings().DEFAULT_PROCESSOR or "mock").strip().lower()

    from app.core.database import SessionLocal
    from app.models.api_definition import ApiDefinition
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus
    from app.ocr_optimizer.eval.harness import module_specs_from_orm
    from app.ocr_optimizer.eval.golden_loop import golden_strict_batch

    db = SessionLocal()
    try:
        api = db.query(ApiDefinition).filter(ApiDefinition.api_code == args.candidate).first()
        if not api:
            raise SystemExit(f"candidate api_code {args.candidate!r} not found")
        v = (db.query(OcrPromptVersion)
             .filter(OcrPromptVersion.api_definition_id == api.id,
                     OcrPromptVersion.status == PromptVersionStatus.active.value)
             .first())
        if not v:
            raise SystemExit(f"{args.candidate} has no active version")
        mods = (db.query(OcrModule)
                .filter(OcrModule.prompt_version_id == v.id)
                .order_by(OcrModule.order_index).all())

        report, deviations, batch = golden_strict_batch(
            db, country=args.country, modules=module_specs_from_orm(mods),
            composed_prompt=v.composed_prompt or "", composed_schema=v.composed_schema,
            size=args.size, threshold=args.threshold, rng_seed=args.seed,
            processor_spec=args.processor, model_name=getattr(api, "model_name", None),
        )
    finally:
        db.close()

    payload = {
        "country": args.country, "candidate": args.candidate,
        "batch_size": batch["batch_size"], "pool_size": batch["pool_size"],
        "core_fields": batch["core_fields"],
        "strict_overall_accuracy": round(report.overall_accuracy, 4),
        "modules": report.to_dict()["modules"],
        "deviations": len(deviations),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"GOLDEN STRICT · {args.country} · candidate={args.candidate}")
    print(f"  batch {batch['batch_size']} seeds (pool {batch['pool_size']}) · "
          f"{len(batch['core_fields'])} core fields")
    print(f"  strict overall (exact-match) = {payload['strict_overall_accuracy']}")
    print("-" * 60)
    for m in sorted(payload["modules"], key=lambda x: x["accuracy"]):
        print(f"  {m['accuracy']:.2f}  {m['matched']:>6}  {m['module_key']}")
    print("-" * 60)
    print(f"deviations: {len(deviations)} (each → a diff for reflect_on_golden)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
