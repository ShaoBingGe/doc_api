"""
Golden-seed review (platform admin · 国家模板页 · Part 3).

Surfaces, per country:
  - the available template kinds,
  - each golden seed (PDF + human-reviewed GT),
  - on-demand: the CURRENT country-template prompt's OCR result per seed, with a
    per-field conflict flag vs the GT (GT vs latest value).

The heavy evaluate step OCRs the on-disk golden PDFs with the freshly-composed
country prompt (reuses harness.evaluate_prompt, strict mode → per-sample
expected/got). Results are cached to `_latest_eval.json` so the page reads cheap;
a "重新评测" button re-runs it. Gemini outages degrade to a structured failure
(never 500); the GT view is always available.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from . import golden_loop
from .harness import ModuleSpec, evaluate_prompt

_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def _golden_dir(country: str) -> str:
    return os.path.join(_EVAL_DIR, "golden_set", country.upper())


def _cache_path(country: str) -> str:
    return os.path.join(_golden_dir(country), "_latest_eval.json")


# ── 3.1 templates / kinds ─────────────────────────────────────────────────────

def list_country_kinds() -> list[dict]:
    """Available countries + their template kinds. Today every country ships a
    single 'invoice' template; structured as a list so more kinds can be added."""
    from ..service import template_loader

    out: list[dict] = []
    for item in template_loader.list_available_countries():
        out.append({
            "country": item["country"],
            "available": item.get("available", False),
            "kinds": ["invoice"] if item.get("available") else [],
        })
    return out


# ── 3.2 seeds + GT ────────────────────────────────────────────────────────────

def _record(gt: Any) -> dict:
    """Unwrap the list-wrapped golden GT ([record]) → the field dict."""
    if isinstance(gt, list) and gt and isinstance(gt[0], dict):
        return gt[0]
    return gt if isinstance(gt, dict) else {}


def load_seeds(country: str) -> list[dict]:
    """List golden seeds: {seed_id, filename, gt (field→value), gt_fields}."""
    golden = golden_loop.load_golden(country)
    out: list[dict] = []
    for seed_id, e in golden.items():
        doc_rel = e.get("doc") or ""
        out.append({
            "seed_id": seed_id,
            "filename": os.path.basename(doc_rel),
            "gt": _record(e.get("gt")),
            "gt_fields": e.get("fields"),
        })
    # stable order: most GT fields first (richest samples on top)
    out.sort(key=lambda s: -(s.get("gt_fields") or 0))
    return out


def golden_pdf_path(country: str, seed_id: str) -> str | None:
    """Absolute path to a seed's PDF on disk, or None."""
    golden = golden_loop.load_golden(country)
    e = golden.get(seed_id)
    if not e or not e.get("doc"):
        return None
    path = os.path.join(_golden_dir(country), e["doc"])
    return path if os.path.isfile(path) else None


# ── current country prompt (non-persisting) ───────────────────────────────────

def _build_eval_inputs(country: str):
    """Compose the CURRENT country template into (modules, prompt, schema)
    without persisting anything (mirrors preset_init's v1 composition)."""
    from ..service import template_loader
    from ..service.composer import GLOBAL_OUTPUT_CONTRACT_DETAILS

    decomposed = template_loader.decompose_country_template(country)
    composed_prompt = (
        decomposed["prompt_format"].rstrip()
        + "\n\n"
        + GLOBAL_OUTPUT_CONTRACT_DETAILS
        + "\n"
    )
    composed_schema = decomposed["json_schema"]
    modules = [
        ModuleSpec(
            module_key=m["module_key"],
            json_path=m["json_path"],
            schema_fragment=m.get("schema_fragment"),
            display_name=m.get("display_name"),
        )
        for m in decomposed["modules"]
    ]
    return modules, composed_prompt, composed_schema


def _ensure_golden_documents(db, country: str) -> None:
    """Register each golden PDF as a Document (id = source_doc_id) so the OCR
    runner can resolve it. Idempotent; platform bucket (tenant_id=None)."""
    from app.models.document import Document, DocumentStatus

    golden = golden_loop.load_golden(country)
    for seed_id, e in golden.items():
        try:
            did = uuid.UUID(seed_id)
        except (ValueError, TypeError):
            continue
        if db.get(Document, did) is not None:
            continue
        path = golden_pdf_path(country, seed_id)
        if not path:
            continue
        db.add(Document(
            id=did,
            tenant_id=None,
            filename=os.path.basename(path),
            file_type="pdf",
            file_size=os.path.getsize(path),
            storage_path=path,
            status=DocumentStatus.completed.value,
        ))
    db.commit()


def _leaf(json_path: str | None) -> str:
    if not json_path:
        return ""
    leaf = json_path.split(".")[-1]
    return leaf.replace("[*]", "").replace("[", "").replace("]", "").strip()


# ── 3.2 evaluate (on-demand) + cache ──────────────────────────────────────────

def evaluate(db, country: str, *, processor_spec: str | None = None, limit: int = 0) -> dict:
    if not processor_spec:
        from app.core.config import get_settings
        processor_spec = get_settings().DEFAULT_PROCESSOR or "mock"
    """OCR golden seeds with the current country prompt, build per-seed per-field
    conflict data (GT vs latest), cache it, and return it. Never raises on OCR
    failure — degrades to a structured `failed`/`ocr_error` result."""
    country = country.upper()

    def _fail(payload: dict) -> dict:
        # always clear the "running" marker so polling/UI never sticks
        payload = {**payload, "running": False}
        _write_cache(country, payload)
        return payload

    seeds = load_seeds(country)
    if not seeds:
        return _fail({"country": country, "error": "no_golden_set", "per_seed": {}})

    try:
        modules, prompt, schema = _build_eval_inputs(country)
    except FileNotFoundError:
        return _fail({"country": country, "error": "no_template", "per_seed": {}})

    name_by_key = {m.module_key: (m.display_name or _leaf(m.json_path) or m.module_key)
                   for m in modules}

    _ensure_golden_documents(db, country)

    golden = golden_loop.load_golden(country)
    items = list(golden.items())
    if limit > 0:
        items = items[:limit]
    sample_ids = [uuid.UUID(did) for did, _ in items]
    # IMPORTANT: run_ocr_on_samples keys its outputs by str(uuid) (DASHED), so the
    # ground-truth dict must use the SAME dashed key or score_outputs can't pair
    # them (→ every field scores None). Golden manifest ids are no-dash hex.
    gts = {str(uuid.UUID(did)): g["gt"] for did, g in items}

    try:
        report = evaluate_prompt(
            db,
            modules=modules,
            sample_doc_ids=sample_ids,
            composed_prompt=prompt,
            composed_schema=schema,
            ground_truths=gts,
            processor_spec=processor_spec,
            model_name=None,
            strict=True,
        )
    except Exception as exc:  # processor unavailable, etc. — never 500
        return _fail({
            "country": country,
            "error": "eval_failed",
            "detail": str(exc)[:300],
            "per_seed": {},
        })

    # Group per_sample → per seed. per_seed is keyed by the no-dash hex seed id
    # (matches load_seeds + the frontend); score_outputs' doc_id is the dashed
    # uuid str, so normalize back to no-dash hex.
    def _hex(s) -> str:
        try:
            return uuid.UUID(str(s)).hex
        except (ValueError, TypeError):
            return str(s)

    per_seed: dict[str, dict] = {
        s["seed_id"]: {"ocr_error": False, "fields": []} for s in seeds
    }
    for m in report.module_scores:
        field_name = name_by_key.get(m.module_key, m.module_key)
        for p in m.per_sample:
            sid = _hex(p.get("doc_id"))
            bucket = per_seed.setdefault(sid, {"ocr_error": False, "fields": []})
            bucket["fields"].append({
                "field": field_name,
                "module_key": m.module_key,
                "gt": p.get("expected"),
                "latest": p.get("got"),
                "conflict": not p.get("matched", False),
            })
    for did in report.ocr_error_doc_ids:
        sid = _hex(did)
        if sid in per_seed:
            per_seed[sid]["ocr_error"] = True

    total_conflicts = sum(
        1 for b in per_seed.values() for f in b["fields"] if f["conflict"]
    )
    result = {
        "country": country,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "processor": processor_spec,
        "summary": {
            "seeds": len(items),
            "conflicts": total_conflicts,
            "ocr_errors": len(report.ocr_error_doc_ids),
            "overall_accuracy": round(report.overall_accuracy, 4),
        },
        "per_seed": per_seed,
    }
    _write_cache(country, result)
    return result


def _write_cache(country: str, result: dict) -> None:
    try:
        with open(_cache_path(country), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("failed to write golden eval cache")


def load_cached_eval(country: str) -> dict | None:
    path = _cache_path(country.upper())
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── background runner ─────────────────────────────────────────────────────────
#
# evaluate() OCRs every golden seed sequentially (minutes). Running it inline in
# the request worker froze the whole site (single worker, 1-by-1 OCR). So the
# endpoint kicks it off in a daemon thread (its OWN DB session) and returns
# immediately; status lives in the cache file so the frontend can poll it.

def is_running(country: str) -> bool:
    c = load_cached_eval(country)
    return bool(c and c.get("running"))


def start_evaluate_async(country: str, *, processor_spec: str | None = None, limit: int = 0) -> dict:
    """Launch evaluate() in the background. Returns immediately with a status."""
    import threading

    country = country.upper()
    if is_running(country):
        return {"country": country, "running": True, "already": True, "per_seed": {}}

    # mark running so polling + concurrent-click guard see it right away
    _write_cache(country, {"country": country, "running": True, "per_seed": {}})

    def _worker():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            evaluate(db, country, processor_spec=processor_spec, limit=limit)
        except Exception as exc:  # never leave a stuck "running" marker
            _write_cache(country, {
                "country": country, "running": False, "error": "eval_failed",
                "detail": str(exc)[:300], "per_seed": {},
            })
        finally:
            db.close()

    threading.Thread(target=_worker, daemon=True).start()
    return {"country": country, "running": True, "started": True, "per_seed": {}}
