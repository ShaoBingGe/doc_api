"""
Top-level orchestrator for the OCR optimizer.

**Design v2 (paused-for-review)**: Run no longer self-drives multiple rounds.
Each round is a separate API call. After every round the Run is paused
(`status=paused_for_review`) until the user calls `advance_round` or
`finalize_run`. See docs/ocr-optimizer-design.md §7.

Public API:
    start_optimization(db, api_definition_id, **opts)
        → Create Run, run Round 1, pause.
    advance_round(db, run_id, use_version_id=None)
        → Run one more round starting from use_version_id (or last round's
          next_version), pause again.
    manual_patch(db, api_def_id, source_version_id, edits)
        → Derive a new OcrPromptVersion(origin='manual_edit') from edits.
          Does NOT run any OCR — purely a metadata operation.
    finalize_run(db, run_id, version_id)
        → Activate version_id, complete the Run.
    abort_run(db, run_id)
        → Aborted without activating anything.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models.api_definition import ApiDefinition

from ..models import (
    OcrModule,
    OcrModuleIteration,
    OcrOptimizationRound,
    OcrOptimizationRun,
    OcrPromptVersion,
    PromptVersionStatus,
    RoundPhase,
    RunStatus,
    VersionOrigin,
)
from . import (
    composer,
    evaluator,
    ground_truth,
    meta_optimizer,
    module_optimizer,
    ocr_runner,
    persistence,
    slicer,
)
from .module_initializer import init_version

logger = logging.getLogger(__name__)


MIN_SAMPLES = 3
DEFAULT_MAX_ROUNDS = 5
DEFAULT_TARGET = 0.95
HARD_MODULE_LIMIT = 20


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def start_optimization(
    db: Session,
    api_definition_id: uuid.UUID,
    *,
    max_rounds: int | None = None,
    target_accuracy: float | None = None,
    sample_document_ids_override: list[uuid.UUID] | None = None,
    llm_provider_override: str | None = None,
    enable_meta: bool = True,
) -> OcrOptimizationRun:
    """
    Create a new Run, execute Round 1, leave Run in `paused_for_review`.

    Synchronous (blocking, ~30-60s for OCR + LLM). Returns the Run with
    Round 1 attached. Caller must subsequently call `advance_round` or
    `finalize_run`.
    """
    api_def, sample_ids, ground_truths = _resolve_run_inputs(
        db, api_definition_id, sample_document_ids_override
    )
    starting_version = _resolve_starting_version(db, api_definition_id, sample_ids)
    llm_provider = llm_provider_override or _default_llm_provider(api_def)

    run = OcrOptimizationRun(
        id=uuid.uuid4(),
        api_definition_id=api_definition_id,
        starting_version_id=starting_version.id,
        status=RunStatus.running.value,
        max_rounds=max_rounds or DEFAULT_MAX_ROUNDS,
        target_accuracy=target_accuracy if target_accuracy is not None else DEFAULT_TARGET,
        rounds_completed=0,
        current_round_num=0,
        sample_document_ids=[str(x) for x in sample_ids],
        llm_provider=llm_provider,
        metrics={"total_ocr_calls": 0, "total_llm_calls": 0},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        rnd = _run_one_round(
            db,
            run=run,
            round_num=1,
            api_def=api_def,
            current_version=starting_version,
            sample_ids=sample_ids,
            ground_truths=ground_truths,
            metrics=dict(run.metrics or {}),
            enable_meta=enable_meta,
        )
        run.rounds_completed = 1
        run.current_round_num = 1
        run.status = RunStatus.paused_for_review.value
        run.metrics = _accumulate_metrics(run.metrics, rnd)
        db.commit()
        db.refresh(run)
    except Exception as exc:
        logger.exception("Run %s failed at Round 1: %s", run.id, exc)
        run.status = RunStatus.failed.value
        run.error_message = str(exc)[:1024]
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    return run


def advance_round(
    db: Session,
    run_id: uuid.UUID,
    *,
    use_version_id: uuid.UUID | None = None,
    enable_meta: bool = True,
) -> OcrOptimizationRun:
    """
    Run the next round of an existing Run.

    `use_version_id` (optional): start this round from this version. Typically
    a manual_edit derived version (§7.4). If omitted, uses the latest round's
    next_version_id.

    Returns the Run with the new round attached, status back to
    `paused_for_review` (or `failed`).
    """
    run = db.get(OcrOptimizationRun, run_id)
    if not run:
        raise NotFoundError(f"Run {run_id} not found")
    if run.status != RunStatus.paused_for_review.value:
        raise ValidationError(
            f"Run is in {run.status!r}, advance requires paused_for_review"
        )
    if run.current_round_num >= run.max_rounds:
        raise ValidationError(
            f"Run has reached max_rounds={run.max_rounds}; "
            "you must finalize the Run instead of advancing"
        )

    api_def = db.get(ApiDefinition, run.api_definition_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {run.api_definition_id} not found")

    # advance_round uses the same sample list the run was started with —
    # those samples WERE confirmed at start time. If any have since lost
    # GT (rare: customer un-confirmed), skip them silently rather than abort.
    raw_sample_ids = [_to_uuid(x) for x in (run.sample_document_ids or [])]
    ground_truths: dict[str, dict] = {}
    sample_ids: list[uuid.UUID] = []
    for sid in raw_sample_ids:
        gt = ground_truth.build(db, sid)
        if gt:
            sample_ids.append(sid)
            ground_truths[str(sid)] = gt
    if len(sample_ids) < MIN_SAMPLES:
        raise ValidationError(
            f"Run lost ground-truth coverage: only {len(sample_ids)}/{len(raw_sample_ids)} "
            f"samples remain confirmed (need {MIN_SAMPLES})."
        )

    # Resolve starting version for this round
    if use_version_id:
        starting_version = db.get(OcrPromptVersion, use_version_id)
        if not starting_version or starting_version.api_definition_id != run.api_definition_id:
            raise ValidationError(f"Version {use_version_id} invalid for this Run")
    else:
        # Use most recent round's next_version
        last_round = (
            db.query(OcrOptimizationRound)
            .filter(OcrOptimizationRound.run_id == run.id)
            .order_by(OcrOptimizationRound.round_num.desc())
            .first()
        )
        nv_id = last_round.next_version_id if last_round else None
        starting_version = db.get(OcrPromptVersion, nv_id) if nv_id else None
        if not starting_version:
            raise ValidationError(
                "No version available to advance from; pass use_version_id explicitly"
            )

    run.status = RunStatus.running.value
    db.commit()

    next_round_num = run.current_round_num + 1
    try:
        rnd = _run_one_round(
            db,
            run=run,
            round_num=next_round_num,
            api_def=api_def,
            current_version=starting_version,
            sample_ids=sample_ids,
            ground_truths=ground_truths,
            metrics=dict(run.metrics or {}),
            enable_meta=enable_meta,
        )
        run.rounds_completed = next_round_num
        run.current_round_num = next_round_num
        run.status = RunStatus.paused_for_review.value
        run.metrics = _accumulate_metrics(run.metrics, rnd)
        db.commit()
        db.refresh(run)
    except Exception as exc:
        logger.exception("Run %s failed at Round %d: %s", run.id, next_round_num, exc)
        run.status = RunStatus.failed.value
        run.error_message = str(exc)[:1024]
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    return run


def manual_patch(
    db: Session,
    *,
    api_definition_id: uuid.UUID,
    source_version_id: uuid.UUID,
    edits: list[dict],
) -> OcrPromptVersion:
    """
    Derive a new OcrPromptVersion(origin='manual_edit') from `source_version_id`,
    applying the given module-level edits.

    `edits` is a list of dicts shaped:
        {module_key: str, description?: str, ocr_suggestions?: dict}
    Fields not listed above are silently ignored. **skill_ids cannot be edited
    via this path (design §15.10)**; any "skill_ids" key in edits is dropped.

    Returns the new draft version (NOT activated).
    """
    source = db.get(OcrPromptVersion, source_version_id)
    if not source or source.api_definition_id != api_definition_id:
        raise NotFoundError(f"Version {source_version_id} not found for this API")

    # Find the open Run for context (if any) — manual_patch is typically called
    # during paused_for_review but we don't strictly require a Run.
    open_run = (
        db.query(OcrOptimizationRun)
        .filter(
            OcrOptimizationRun.api_definition_id == api_definition_id,
            OcrOptimizationRun.status == RunStatus.paused_for_review.value,
        )
        .order_by(OcrOptimizationRun.started_at.desc())
        .first()
    )

    edits_by_key: dict[str, dict] = {}
    forbidden_keys = {"skill_ids", "new_skill_ids", "skills", "ocr_prompt"}
    for e in edits or []:
        key = e.get("module_key")
        if not key:
            continue
        cleaned = {
            k: v
            for k, v in e.items()
            if k in {"description", "ocr_suggestions"}
        }
        # Hard reject skill mutations even via manual patch
        dropped = [k for k in e if k in forbidden_keys]
        if dropped:
            logger.warning(
                "manual_patch: ignoring forbidden edit keys %s on module %s",
                dropped, key,
            )
        if cleaned:
            edits_by_key[key] = cleaned

    base_modules: list[OcrModule] = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == source.id)
        .order_by(OcrModule.order_index)
        .all()
    )

    new_version_label = _next_manual_label(db, source)
    new_version = OcrPromptVersion(
        id=uuid.uuid4(),
        api_definition_id=api_definition_id,
        version=new_version_label,
        parent_version_id=source.id,
        status=PromptVersionStatus.draft.value,
        origin=VersionOrigin.manual_edit.value,
        composed_prompt="",
        composed_schema=None,
        # country-wide rules are version-level — inherit from source.
        country_global_text=source.country_global_text,
        overall_accuracy=None,
        produced_by_run_id=open_run.id if open_run else None,
        produced_in_round=open_run.current_round_num if open_run else None,
        notes=f"manual_edit derived from v{source.version}",
    )

    new_modules: list[OcrModule] = []
    for m in base_modules:
        patch = edits_by_key.get(m.module_key, {})
        new_modules.append(
            OcrModule(
                id=uuid.uuid4(),
                prompt_version_id=new_version.id,
                module_key=m.module_key,
                display_name=m.display_name,
                description=patch.get("description") or m.description,
                json_path=m.json_path,
                schema_fragment=m.schema_fragment,
                ocr_suggestions=persistence._merge_round_suggestions(
                    previous=m.ocr_suggestions,
                    new_text=patch.get("ocr_suggestions"),
                    round_no=None,  # manual_patch is not round-tied
                    kind="manual",
                    rationale=patch.get("description") or "",
                ),
                ocr_prompt=m.ocr_prompt,  # not regenerated in manual patch
                # ★ skill_ids HARD COPY — never user-editable here
                skill_ids=list(m.skill_ids or []),
                order_index=m.order_index,
                status=m.status,
                module_accuracy=m.module_accuracy,
            )
        )

    try:
        new_version.composed_schema = composer.assemble_schema(new_modules)
        new_version.composed_prompt = composer.assemble_prompt(
            new_modules,
            country_global=new_version.country_global_text,
        )
    except ValueError as exc:
        raise ValidationError(f"Compose failed for manual patch: {exc}") from exc

    db.add(new_version)
    for m in new_modules:
        db.add(m)
    db.commit()
    db.refresh(new_version)
    return new_version


def finalize_run(
    db: Session,
    run_id: uuid.UUID,
    version_id: uuid.UUID,
) -> OcrOptimizationRun:
    """
    End the Run by activating `version_id`. version_id must belong to the
    Run's evolution chain (any round.next_version_id, or a manual_edit
    version produced during this Run).
    """
    run = db.get(OcrOptimizationRun, run_id)
    if not run:
        raise NotFoundError(f"Run {run_id} not found")
    if run.status not in {
        RunStatus.paused_for_review.value,
        RunStatus.failed.value,
    }:
        raise ValidationError(
            f"Cannot finalize Run in status {run.status!r}"
        )

    target = db.get(OcrPromptVersion, version_id)
    if not target or target.api_definition_id != run.api_definition_id:
        raise NotFoundError(f"Version {version_id} not found for this API")

    # Verify version belongs to this Run's chain (or is the starting version)
    valid_ids = {run.starting_version_id}
    rounds = (
        db.query(OcrOptimizationRound)
        .filter(OcrOptimizationRound.run_id == run.id)
        .all()
    )
    for r in rounds:
        if r.next_version_id:
            valid_ids.add(r.next_version_id)
    manual_versions = (
        db.query(OcrPromptVersion)
        .filter(
            OcrPromptVersion.produced_by_run_id == run.id,
            OcrPromptVersion.origin == VersionOrigin.manual_edit.value,
        )
        .all()
    )
    for v in manual_versions:
        valid_ids.add(v.id)
    if version_id not in valid_ids:
        raise ValidationError(
            f"Version {version_id} is not in this Run's chain"
        )

    persistence.activate_version(db, run.api_definition_id, version_id)
    run.resulting_version_id = version_id
    run.status = RunStatus.completed.value
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def abort_run(db: Session, run_id: uuid.UUID) -> OcrOptimizationRun:
    """Abort the Run without modifying any active version."""
    run = db.get(OcrOptimizationRun, run_id)
    if not run:
        raise NotFoundError(f"Run {run_id} not found")
    if run.status in {RunStatus.completed.value, RunStatus.aborted.value}:
        return run
    run.status = RunStatus.aborted.value
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


# Legacy alias — keep `optimize()` callable as "start_optimization", returning
# a Run that is paused_for_review after Round 1. Callers should migrate to
# start_optimization explicitly.
def optimize(*args, **kwargs):
    """Deprecated. Use start_optimization. Now runs one round and pauses."""
    return start_optimization(*args, **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_run_inputs(
    db: Session,
    api_definition_id: uuid.UUID,
    sample_document_ids_override: list[uuid.UUID] | None,
) -> tuple[ApiDefinition, list[uuid.UUID], dict[str, dict]]:
    """Shared validation: api def, sample list, GT preload.

    Per design v3 we only use samples the customer has confirmed as GT
    ("已审视"). Samples without GT are silently dropped from the run; the
    customer can confirm them later and re-trigger. We still require
    at least MIN_SAMPLES *confirmed* samples to proceed.
    """
    api_def: ApiDefinition | None = db.get(ApiDefinition, api_definition_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_definition_id} not found")

    if sample_document_ids_override is not None:
        raw_sample_ids = list(sample_document_ids_override)
    else:
        cfg = api_def.config or {}
        raw = cfg.get("sample_document_ids") or cfg.get("sample_document_id")
        if isinstance(raw, str):
            raw = [raw]
        raw_sample_ids = [_to_uuid(x) for x in (raw or [])]

    # Filter to samples with GT only — silently drop "待审视" ones
    ground_truths: dict[str, dict] = {}
    confirmed_ids: list[uuid.UUID] = []
    for sid in raw_sample_ids:
        gt = ground_truth.build(db, sid)
        if gt:
            confirmed_ids.append(sid)
            ground_truths[str(sid)] = gt

    if len(confirmed_ids) < MIN_SAMPLES:
        raise ValidationError(
            f"至少需要 {MIN_SAMPLES} 个已审视的样本才能启动迭代 "
            f"（当前共 {len(raw_sample_ids)} 个样本，仅 {len(confirmed_ids)} 个已审视）"
        )

    return api_def, confirmed_ids, ground_truths


def _resolve_starting_version(
    db: Session,
    api_definition_id: uuid.UUID,
    sample_ids: list[uuid.UUID],
) -> OcrPromptVersion:
    starting_version = persistence.get_active_version(db, api_definition_id)
    if not starting_version:
        logger.info(
            "No active version for API %s — auto-initializing", api_definition_id
        )
        starting_version = init_version(
            db,
            api_definition_id,
            sample_document_ids=sample_ids,
            activate=True,
            use_llm_for_modules=False,
        )
    return starting_version


def _next_manual_label(db: Session, source: OcrPromptVersion) -> str:
    """
    Generate the next manual_edit version label as "<parent>.<seq>".

    If source is itself a manual_edit (e.g. "2.1"), strip the suffix and
    base off the original integer parent.
    """
    base = str(source.version)
    if "." in base:
        base = base.split(".")[0]

    existing_labels = (
        db.query(OcrPromptVersion.version)
        .filter(
            OcrPromptVersion.api_definition_id == source.api_definition_id,
            OcrPromptVersion.origin == VersionOrigin.manual_edit.value,
        )
        .all()
    )
    max_seq = 0
    for (label,) in existing_labels:
        if not label or not str(label).startswith(f"{base}."):
            continue
        try:
            seq = int(str(label).split(".", 1)[1])
            if seq > max_seq:
                max_seq = seq
        except (ValueError, IndexError):
            continue
    return f"{base}.{max_seq + 1}"


def _next_round_version(db: Session, api_definition_id: uuid.UUID) -> int:
    """Find the next integer version label not yet used by any round product."""
    labels = (
        db.query(OcrPromptVersion.version)
        .filter(OcrPromptVersion.api_definition_id == api_definition_id)
        .all()
    )
    used_ints: set[int] = set()
    for (label,) in labels:
        if label is None:
            continue
        s = str(label)
        if "." in s:
            continue  # manual_edit suffix — doesn't claim an integer slot
        try:
            used_ints.add(int(s))
        except ValueError:
            continue
    nxt = 1
    while nxt in used_ints:
        nxt += 1
    return nxt


def _accumulate_metrics(prev: dict | None, rnd: OcrOptimizationRound) -> dict:
    """Merge per-round metrics into the Run-level accumulator."""
    out = dict(prev or {})
    # _run_one_round writes its incremental counts into `metrics` dict passed in.
    # We just re-read what's been committed (the orchestrator is single-threaded
    # so a shallow merge from current is fine).
    return out


# ── Single round ─────────────────────────────────────────────────────────────

def _run_one_round(
    db: Session,
    *,
    run: OcrOptimizationRun,
    round_num: int,
    api_def: ApiDefinition,
    current_version: OcrPromptVersion,
    sample_ids: list[uuid.UUID],
    ground_truths: dict[str, dict],
    metrics: dict,
    enable_meta: bool = True,
) -> OcrOptimizationRound:
    start_ms = int(time.time() * 1000)

    rnd = OcrOptimizationRound(
        id=uuid.uuid4(),
        run_id=run.id,
        round_num=round_num,
        prompt_version_id=current_version.id,
        phase=RoundPhase.ocr_running.value,
    )
    db.add(rnd)
    db.commit()
    db.refresh(rnd)

    # Re-fetch modules with explicit ordering (relationship may be stale)
    modules: list[OcrModule] = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == current_version.id)
        .order_by(OcrModule.order_index)
        .all()
    )
    if not modules:
        raise ValidationError(
            f"Version {current_version.id} has no modules to optimize"
        )

    # ── Step 1: Full OCR on every sample ─────────────────────────────────
    ocr_outputs = ocr_runner.run_ocr_on_samples(
        db,
        sample_document_ids=sample_ids,
        composed_prompt=current_version.composed_prompt,
        composed_schema=current_version.composed_schema,
        processor_spec=api_def.processor_type or "mock",
        model_name=api_def.model_name,
    )
    metrics["total_ocr_calls"] += len(sample_ids)
    rnd.ocr_raw_outputs = ocr_outputs
    rnd.phase = RoundPhase.analyzing.value
    db.commit()

    # ── Step 2: Slice + evaluate each module ────────────────────────────
    iterations: list[OcrModuleIteration] = []
    per_sample_accuracy: dict[str, float] = {}

    for mod in modules:
        per_sample: list[dict] = []
        for sid in sample_ids:
            sid_str = str(sid)
            ocr_full = ocr_outputs.get(sid_str)
            if isinstance(ocr_full, dict) and "_error" in ocr_full:
                # OCR failed for this sample
                per_sample.append({
                    "sample_doc_id": sid_str,
                    "ocr_sliced": None,
                    "ground_truth": slicer.extract(ground_truths.get(sid_str), mod.json_path),
                    "matched": False,
                    "field_accuracy": 0.0,
                    "diff_detail": f"OCR error: {ocr_full.get('_error', '')[:200]}",
                })
                continue
            sliced = slicer.extract(ocr_full, mod.json_path)
            gt_sliced = slicer.extract(ground_truths.get(sid_str), mod.json_path)
            matched, acc, diff = evaluator.compare(sliced, gt_sliced, mod.schema_fragment)
            per_sample.append({
                "sample_doc_id": sid_str,
                "ocr_sliced": sliced,
                "ground_truth": gt_sliced,
                "matched": matched,
                "field_accuracy": acc,
                "diff_detail": diff,
            })

        agg_acc = mean(p["field_accuracy"] for p in per_sample) if per_sample else 0.0
        it = OcrModuleIteration(
            id=uuid.uuid4(),
            round_id=rnd.id,
            module_id=mod.id,
            module_key=mod.module_key,
            per_sample_results=per_sample,
            aggregate_accuracy=round(agg_acc, 4),
        )
        iterations.append(it)
        db.add(it)

    # Per-sample overall accuracy (avg of module accuracies for that sample)
    for sid in sample_ids:
        sid_str = str(sid)
        sample_accs = []
        for it in iterations:
            for p in it.per_sample_results:
                if p["sample_doc_id"] == sid_str:
                    sample_accs.append(p["field_accuracy"])
                    break
        per_sample_accuracy[sid_str] = round(mean(sample_accs), 4) if sample_accs else 0.0

    rnd.overall_accuracy = round(
        mean(it.aggregate_accuracy for it in iterations) if iterations else 0.0, 4
    )
    rnd.per_sample_accuracy = per_sample_accuracy

    # ── Round-start early stop (design v4) ───────────────────────────────
    # If the OCR+eval at the start of this round already matches GT on
    # EVERY field across EVERY sample, the previous prompt is already
    # correct — no need to mutate it. Skip steps 3-5, reuse the current
    # version as the round's "next" version (idempotent).
    if rnd.overall_accuracy >= 0.999:
        logger.info(
            "round %d: round-start eval shows %.2f%% — skipping all mutations",
            round_num, rnd.overall_accuracy * 100,
        )
        rnd.phase = RoundPhase.completed.value
        rnd.next_version_id = current_version.id
        rnd.duration_ms = int(time.time() * 1000) - start_ms
        rnd.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(rnd)
        return rnd

    rnd.phase = RoundPhase.optimizing.value
    db.commit()

    # ── Step 3: Per-module optimizer (skip modules already at 1.0) ───────
    updates: dict[str, dict] = {}
    for mod, it in zip(modules, iterations):
        if it.aggregate_accuracy >= 1.0:
            continue
        history = persistence.load_recent_module_history(
            db, run.api_definition_id, mod.module_key, k=3
        )
        # Exclude current iteration from history (it lives in DB but is "now")
        history = [h for h in history if h.get("round_num") != round_num]

        result = module_optimizer.optimize_module(
            module=mod,
            iteration=it,
            history=history,
            processor_spec=_split_provider(run.llm_provider)[0],
            model_name=_split_provider(run.llm_provider)[1],
        )
        metrics["total_llm_calls"] += 1

        # ── Local self-verify (design v4 "局部验证") ──────────────────
        # Before accepting the mutation, ask the verifier judge whether
        # the new prompt actually fixes the failing samples. A `reject`
        # verdict makes us drop the mutation and keep the old module
        # prompt for this round.
        if result and (result.get("new_ocr_prompt") or result.get("new_description")):
            verdict = module_optimizer.verify_module_fix(
                module=mod,
                iteration=it,
                proposed=result,
                processor_spec=_split_provider(run.llm_provider)[0],
                model_name=_split_provider(run.llm_provider)[1],
            )
            metrics["total_llm_calls"] += 1
            if verdict.get("verdict") == "reject":
                logger.info(
                    "round %d: verifier rejected mutation for %s — keeping old prompt. reason: %s",
                    round_num, mod.module_key, verdict.get("reasoning", "")[:120],
                )
                # Persist the rejected attempt in the iteration for audit
                it.optimization_suggestion = (
                    (result.get("optimization_suggestion") or "")
                    + f"\n\n[VERIFIER REJECT] {verdict.get('reasoning', '')[:200]}"
                )
                result = None  # discard the mutation

        if result:
            it.aggregate_diff = result.get("aggregate_diff")
            it.optimization_suggestion = result.get("optimization_suggestion")
            it.new_description = result.get("new_description")
            it.new_ocr_suggestions = result.get("new_ocr_suggestions")
            it.new_ocr_prompt = result.get("new_ocr_prompt")
            # skill_feedback — the ONLY skill-related field optimizer may set
            it.skill_feedback = result.get("skill_feedback") or None
            if any([result.get("new_description"),
                    result.get("new_ocr_suggestions"),
                    result.get("new_ocr_prompt")]):
                updates[mod.module_key] = {
                    "description": result.get("new_description"),
                    "ocr_suggestions": result.get("new_ocr_suggestions"),
                    "ocr_prompt": result.get("new_ocr_prompt"),
                }
    db.commit()

    # ── Step 4: Meta optimizer (1 LLM call) ──────────────────────────────
    # When `enable_meta=False` (the customer-iteration path) we skip
    # add/remove/rename entirely — the customer's module set is locked at
    # fork time and only failing fields' prompts may be refined.
    if enable_meta:
        meta = meta_optimizer.run_meta_optimization(
            api_def=api_def,
            modules=modules,
            iterations=iterations,
            ocr_outputs=ocr_outputs,
            ground_truths=ground_truths,
            processor_spec=_split_provider(run.llm_provider)[0],
            model_name=_split_provider(run.llm_provider)[1],
        )
        if meta.get("rationale") and not meta["rationale"].startswith(("no unclaimed", "meta optimizer skipped")):
            metrics["total_llm_calls"] += 1
    else:
        meta = {
            "add_modules": [],
            "remove_module_keys": [],
            "rename": [],
            "rationale": "meta disabled (customer-iteration mode — modules locked at fork)",
        }
    rnd.meta_decision = meta
    rnd.phase = RoundPhase.composing.value
    db.commit()

    # ── Step 5: Compose next version ─────────────────────────────────────
    requested_removes = set(meta.get("remove_module_keys") or [])
    all_keys = {m.module_key for m in modules}
    # ── Module preservation guard ────────────────────────────────────────
    # Past runs showed meta_optimizer aggressively requesting removal of
    # most modules, collapsing the prompt to a near-empty schema and tanking
    # accuracy from ~98% to 0. Enforce two safeguards:
    #
    #   1. Never remove a module that was scoring ≥ 0.5 in this round.
    #      Removing well-performing fields is almost always a mistake.
    #   2. After applying removals + adds, the projected module count must
    #      keep at least MIN_MODULES_AFTER_META modules; otherwise we drop
    #      the entire remove set for this round.
    well_performing = {
        it.module_key for it in iterations
        if (it.aggregate_accuracy or 0) >= 0.5
    }
    blocked_removes = requested_removes & well_performing
    safe_removes = requested_removes - well_performing
    if blocked_removes:
        logger.info(
            "round %d: meta wanted to remove %d well-performing module(s) — blocked: %s",
            round_num, len(blocked_removes), sorted(blocked_removes),
        )

    keep_keys = all_keys - safe_removes
    add_specs = meta.get("add_modules") or []
    projected = len(keep_keys) + len(add_specs)
    MIN_MODULES_AFTER_META = max(MIN_SAMPLES, len(all_keys) // 2)  # at least half survive
    if projected < MIN_MODULES_AFTER_META:
        logger.warning(
            "round %d: meta projected only %d modules (min %d) — ignoring all removes",
            round_num, projected, MIN_MODULES_AFTER_META,
        )
        safe_removes = set()
        keep_keys = all_keys
    renames = {r["old"]: r["new"] for r in meta.get("rename", []) if isinstance(r, dict) and "old" in r and "new" in r}

    # Enforce hard module limit (silently truncate adds)
    projected_count = len(keep_keys) + len(add_specs)
    if projected_count > HARD_MODULE_LIMIT:
        add_specs = add_specs[: max(0, HARD_MODULE_LIMIT - len(keep_keys))]

    # Version label is now a string. Round products use the next integer that
    # isn't yet taken for this API (manual_edit versions use "X.Y" labels and
    # don't claim integer slots).
    next_int_version = _next_round_version(db, run.api_definition_id)
    next_version = OcrPromptVersion(
        id=uuid.uuid4(),
        api_definition_id=run.api_definition_id,
        version=str(next_int_version),
        parent_version_id=current_version.id,
        status=PromptVersionStatus.draft.value,
        origin=VersionOrigin.round.value,
        composed_prompt="",
        composed_schema=None,
        # Inherit country-wide rules — rounds don't touch this text.
        country_global_text=current_version.country_global_text,
        produced_by_run_id=run.id,
        produced_in_round=round_num,
        overall_accuracy=rnd.overall_accuracy,
        notes=f"round {round_num} of run {run.id}",
    )

    new_modules = persistence.clone_modules_to_new_version(
        db,
        new_version=next_version,
        base_modules=modules,
        updates=updates,
        keep_keys=keep_keys,
        add_specs=add_specs,
        renames=renames,
    )

    try:
        next_version.composed_schema = composer.assemble_schema(new_modules)
        next_version.composed_prompt = composer.assemble_prompt(
            new_modules,
            country_global=next_version.country_global_text,
        )
    except ValueError as exc:
        logger.warning("Compose failed for round %d, reusing current version: %s",
                       round_num, exc)
        rnd.next_version_id = current_version.id
        rnd.phase = RoundPhase.failed.value
        rnd.duration_ms = int(time.time() * 1000) - start_ms
        rnd.completed_at = datetime.now(timezone.utc)
        db.commit()
        return rnd

    db.add(next_version)
    for m in new_modules:
        db.add(m)
    db.flush()

    rnd.next_version_id = next_version.id
    rnd.phase = RoundPhase.completed.value
    rnd.duration_ms = int(time.time() * 1000) - start_ms
    rnd.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rnd)
    return rnd


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_uuid(x: Any) -> uuid.UUID:
    if isinstance(x, uuid.UUID):
        return x
    return uuid.UUID(str(x))


def _default_llm_provider(api_def: ApiDefinition) -> str:
    proc = api_def.processor_type or "mock"
    model = api_def.model_name
    return f"{proc}|{model}" if model else proc


def _split_provider(spec: str) -> tuple[str, str | None]:
    if "|" in spec:
        a, b = spec.split("|", 1)
        return a, b
    return spec, None
