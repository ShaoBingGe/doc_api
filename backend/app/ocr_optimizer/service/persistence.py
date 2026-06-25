"""
Persistence helpers for OcrPromptVersion / OcrModule / Run / Round / Iteration.

Also exposes the production-facing entry point `get_active_composed_prompt`
that extract_service calls before each public OCR request.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.api_definition import ApiDefinition

from ..models import (
    OcrModule,
    OcrModuleIteration,
    OcrOptimizationRound,
    OcrOptimizationRun,
    OcrPromptVersion,
    PromptVersionStatus,
)

logger = logging.getLogger(__name__)


# ── Production entry point (called by extract_service) ───────────────────────

def get_active_composed_prompt(db: Session, api_definition_id: uuid.UUID) -> str | None:
    """Fetch the composed_prompt of the active version for an API, or None."""
    v = (
        db.query(OcrPromptVersion)
        .filter(OcrPromptVersion.api_definition_id == api_definition_id)
        .filter(OcrPromptVersion.status == PromptVersionStatus.active.value)
        .first()
    )
    return v.composed_prompt if v else None


def get_active_version(db: Session, api_definition_id: uuid.UUID) -> OcrPromptVersion | None:
    return (
        db.query(OcrPromptVersion)
        .filter(OcrPromptVersion.api_definition_id == api_definition_id)
        .filter(OcrPromptVersion.status == PromptVersionStatus.active.value)
        .first()
    )


# ── Version queries ───────────────────────────────────────────────────────────

def list_versions(db: Session, api_definition_id: uuid.UUID) -> list[OcrPromptVersion]:
    return (
        db.query(OcrPromptVersion)
        .filter(OcrPromptVersion.api_definition_id == api_definition_id)
        .order_by(desc(OcrPromptVersion.version))
        .all()
    )


def get_version_or_404(db: Session, version_id: uuid.UUID) -> OcrPromptVersion:
    v = db.get(OcrPromptVersion, version_id)
    if not v:
        raise NotFoundError(f"OcrPromptVersion {version_id} not found")
    return v


def activate_version(
    db: Session, api_definition_id: uuid.UUID, version_id: uuid.UUID
) -> OcrPromptVersion:
    target = get_version_or_404(db, version_id)
    if target.api_definition_id != api_definition_id:
        raise NotFoundError(f"Version {version_id} does not belong to API {api_definition_id}")

    db.query(OcrPromptVersion).filter(
        OcrPromptVersion.api_definition_id == api_definition_id,
        OcrPromptVersion.status == PromptVersionStatus.active.value,
        OcrPromptVersion.id != version_id,
    ).update({"status": PromptVersionStatus.archived.value})

    target.status = PromptVersionStatus.active.value
    target.activated_at = datetime.now(timezone.utc)

    # Sync ApiDefinition.prompt_version_id
    api_def = db.get(ApiDefinition, api_definition_id)
    if api_def:
        api_def.prompt_version_id = target.id

    db.commit()
    db.refresh(target)
    return target


# ── Run / Round / Iteration queries ───────────────────────────────────────────

def list_runs(db: Session, api_definition_id: uuid.UUID) -> list[OcrOptimizationRun]:
    return (
        db.query(OcrOptimizationRun)
        .filter(OcrOptimizationRun.api_definition_id == api_definition_id)
        .order_by(desc(OcrOptimizationRun.started_at))
        .all()
    )


def get_run_or_404(db: Session, run_id: uuid.UUID) -> OcrOptimizationRun:
    r = db.get(OcrOptimizationRun, run_id)
    if not r:
        raise NotFoundError(f"OcrOptimizationRun {run_id} not found")
    return r


def get_round_or_404(
    db: Session, run_id: uuid.UUID, round_num: int
) -> OcrOptimizationRound:
    r = (
        db.query(OcrOptimizationRound)
        .filter(
            OcrOptimizationRound.run_id == run_id,
            OcrOptimizationRound.round_num == round_num,
        )
        .first()
    )
    if not r:
        raise NotFoundError(f"Round {round_num} of run {run_id} not found")
    return r


def list_iterations_for_run(db: Session, run_id: uuid.UUID) -> list[OcrModuleIteration]:
    return (
        db.query(OcrModuleIteration)
        .join(OcrOptimizationRound, OcrModuleIteration.round_id == OcrOptimizationRound.id)
        .filter(OcrOptimizationRound.run_id == run_id)
        .order_by(OcrOptimizationRound.round_num, OcrModuleIteration.module_key)
        .all()
    )


def field_accuracy_timeline(db: Session, run_id: uuid.UUID) -> dict:
    """Per-round × per-field accuracy for one Run — the convergence dashboard.

    Shape (ready for a multi-line chart / heatmap on the 迭代过程 tab):
        {
          "run_id": "...",
          "fields": [{"module_key": "...", "display_name": "..."}, ...],
          "rounds": [
            {"round_num": 1, "overall_accuracy": 0.83, "phase": "completed",
             "fields": {"doc_type": 1.0, "invoice_number": 0.6, ...}},
            ...
          ],
        }
    Field accuracy comes straight from OcrModuleIteration.aggregate_accuracy
    (the same per-module score the iteration computed), so the dashboard shows
    exactly what drove each round's optimize decisions.
    """
    run = db.get(OcrOptimizationRun, run_id)
    if not run:
        raise NotFoundError(f"OcrOptimizationRun {run_id} not found")

    rounds = (
        db.query(OcrOptimizationRound)
        .filter(OcrOptimizationRound.run_id == run_id)
        .order_by(OcrOptimizationRound.round_num)
        .all()
    )

    field_keys: set[str] = set()
    timeline: list[dict] = []
    for rnd in rounds:
        iters = (
            db.query(OcrModuleIteration)
            .filter(OcrModuleIteration.round_id == rnd.id)
            .all()
        )
        fields = {it.module_key: round(it.aggregate_accuracy or 0.0, 4) for it in iters}
        field_keys |= set(fields.keys())
        timeline.append({
            "round_num": rnd.round_num,
            "overall_accuracy": rnd.overall_accuracy,
            "phase": rnd.phase,
            "fields": fields,
        })

    # Resolve display names from any of this API's modules carrying the key.
    display: dict[str, str] = {}
    if field_keys:
        rows = (
            db.query(OcrModule.module_key, OcrModule.display_name)
            .join(OcrPromptVersion, OcrModule.prompt_version_id == OcrPromptVersion.id)
            .filter(OcrPromptVersion.api_definition_id == run.api_definition_id)
            .filter(OcrModule.module_key.in_(field_keys))
            .all()
        )
        for mk, dn in rows:
            display.setdefault(mk, dn or mk)

    return {
        "run_id": str(run_id),
        "fields": [
            {"module_key": k, "display_name": display.get(k, k)}
            for k in sorted(field_keys)
        ],
        "rounds": timeline,
    }


# ── Module history (for module_optimizer's history input) ────────────────────

def load_recent_module_history(
    db: Session,
    api_definition_id: uuid.UUID,
    module_key: str,
    k: int = 3,
) -> list[dict]:
    """
    Return up to K most-recent iterations for this module across all runs of
    this API definition. Each entry is a dict suitable for LLM context.
    """
    rows = (
        db.query(OcrModuleIteration, OcrOptimizationRound, OcrOptimizationRun)
        .join(OcrOptimizationRound, OcrModuleIteration.round_id == OcrOptimizationRound.id)
        .join(OcrOptimizationRun, OcrOptimizationRound.run_id == OcrOptimizationRun.id)
        .filter(OcrOptimizationRun.api_definition_id == api_definition_id)
        .filter(OcrModuleIteration.module_key == module_key)
        .order_by(desc(OcrModuleIteration.created_at))
        .limit(k)
        .all()
    )
    out: list[dict] = []
    for it, rnd, _run in rows:
        out.append({
            "round_num": rnd.round_num,
            "aggregate_accuracy": it.aggregate_accuracy,
            "optimization_suggestion": it.optimization_suggestion,
            "diff_summary": (it.aggregate_diff or {}).get("differences_description", ""),
        })
    # Caller wants oldest → newest
    return list(reversed(out))


# ── Snapshot helpers (copy modules into next version) ────────────────────────

def _merge_round_suggestions(
    *,
    previous: Any,
    new_text: Any,
    round_no: int | None,
    kind: str,
    rationale: str,
) -> dict:
    """Phase 14c — return a fresh ocr_suggestions dict that PRESERVES the
    prior reflection history list and APPENDS a new entry capturing this
    round's update.

    Shape ({"semantics", "position", "most_common_feature", "extra_features"
    + "reflections": [{...}, ...]}).

    `previous` may be a dict (preferred), None, or a plain string from very
    old data. `new_text` is whatever the optimizer's "new_ocr_suggestions"
    returned (usually a string or dict).
    """
    import copy as _copy
    base: dict = _copy.deepcopy(previous) if isinstance(previous, dict) else {}
    if not isinstance(base, dict):
        base = {}
    # If the optimizer produced a fresh structured dict, take its
    # top-level keys (but never let it clobber an existing reflections list)
    if isinstance(new_text, dict):
        for k, v in new_text.items():
            if k == "reflections":
                continue
            base[k] = v
        summary_text = (
            new_text.get("semantics")
            or new_text.get("most_common_feature")
            or ""
        )
    else:
        summary_text = new_text if isinstance(new_text, str) else ""

    history = list(base.get("reflections") or [])
    if summary_text or rationale:
        history.append({
            "round": round_no,
            "kind": kind,
            "rationale": (rationale or "")[:600],
            "summary": (summary_text or "")[:600],
        })
    base["reflections"] = history
    return base


def clone_modules_to_new_version(
    db: Session,
    new_version: OcrPromptVersion,
    base_modules: list[OcrModule],
    updates: dict[str, dict[str, Any]] | None = None,
    keep_keys: set[str] | None = None,
    add_specs: list[dict] | None = None,
    renames: dict[str, str] | None = None,
) -> list[OcrModule]:
    """
    Build a fresh OcrModule set for `new_version` by copying `base_modules`,
    applying per-module updates, optional removals, additions, and renames.

    Args:
        new_version:  unsaved version (id already populated)
        base_modules: existing modules (sorted by order_index)
        updates:      {module_key: {description?, ocr_suggestions?, ocr_prompt?}}
        keep_keys:    if provided, drop any base module whose key is not in this set
        add_specs:    list of new module dicts (from meta_optimizer.add_modules)
        renames:      {old_key: new_key}

    Returns the new list of OcrModule (unsaved; caller commits).
    """
    updates = updates or {}
    renames = renames or {}
    keep_keys = keep_keys if keep_keys is not None else {m.module_key for m in base_modules}

    seen_keys: set[str] = set()
    new_modules: list[OcrModule] = []
    order = 0

    for m in base_modules:
        if m.module_key not in keep_keys:
            continue
        upd = updates.get(m.module_key, {})
        new_key = renames.get(m.module_key, m.module_key)
        if new_key in seen_keys:
            continue  # avoid duplicate keys from renaming collisions
        seen_keys.add(new_key)

        # Phase 14c — preserve per-round reflection history on every module.
        # When the optimizer hands us a fresh "new_ocr_suggestions" string,
        # we still want the prior `reflections` list (Phase 8 round-0 entries
        # + earlier rounds' entries) to survive. Wrap rather than replace.
        merged_suggestions = _merge_round_suggestions(
            previous=m.ocr_suggestions,
            new_text=upd.get("ocr_suggestions"),
            round_no=getattr(new_version, "produced_in_round", None),
            kind="round",
            rationale=upd.get("description") or "",
        )

        new_modules.append(
            OcrModule(
                id=uuid.uuid4(),
                prompt_version_id=new_version.id,
                module_key=new_key,
                display_name=m.display_name,
                description=upd.get("description") or m.description,
                json_path=m.json_path,
                schema_fragment=m.schema_fragment,
                ocr_suggestions=merged_suggestions,
                ocr_prompt=upd.get("ocr_prompt") or m.ocr_prompt,
                # ★ HARD COPY: skill_ids never come from updates dict — design §15.10.
                # Optimizer's `updates` payload is filtered upstream to exclude skills,
                # but defense in depth: we never read skill_ids from upd here.
                skill_ids=list(getattr(m, "skill_ids", None) or []),
                order_index=order,
                status=m.status,
            )
        )
        order += 1

    for spec in (add_specs or []):
        key = spec.get("module_key")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        new_modules.append(
            OcrModule(
                id=uuid.uuid4(),
                prompt_version_id=new_version.id,
                module_key=key,
                display_name=spec.get("display_name") or key,
                description=spec.get("description") or "",
                json_path=spec.get("json_path") or "$",
                schema_fragment=spec.get("schema_fragment") or {},
                ocr_suggestions=spec.get("ocr_suggestions") or {},
                ocr_prompt=spec.get("ocr_prompt") or "",
                # New modules start with empty skill_ids; even if Meta Optimizer
                # tries to set them, we ignore that field entirely.
                skill_ids=[],
                order_index=order,
            )
        )
        order += 1
    return new_modules
