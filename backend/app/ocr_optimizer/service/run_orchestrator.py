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
    field_constraints,
    ground_truth,
    meta_optimizer,
    module_optimizer,
    ocr_runner,
    persistence,
)
from .module_initializer import init_version

logger = logging.getLogger(__name__)


MIN_SAMPLES = 3
DEFAULT_MAX_ROUNDS = 5
DEFAULT_TARGET = 0.95

# 每轮 per-module 优化的 LLM 并发度（纯文本调用，不吃本地 CPU/内存，
# 可比 OCR 高）。受 DashScope QPS 与失败链兜底约束，取 6。
_MODULE_OPT_CONCURRENCY = 6
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
        metrics_acc = dict(run.metrics or {})
        rnd = _run_one_round(
            db,
            run=run,
            round_num=1,
            api_def=api_def,
            current_version=starting_version,
            sample_ids=sample_ids,
            ground_truths=ground_truths,
            metrics=metrics_acc,
            enable_meta=enable_meta,
        )
        run.rounds_completed = 1
        run.current_round_num = 1
        run.status = RunStatus.paused_for_review.value
        run.metrics = _accumulate_metrics(run.metrics, metrics_acc)
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
        metrics_acc = dict(run.metrics or {})
        rnd = _run_one_round(
            db,
            run=run,
            round_num=next_round_num,
            api_def=api_def,
            current_version=starting_version,
            sample_ids=sample_ids,
            ground_truths=ground_truths,
            metrics=metrics_acc,
            enable_meta=enable_meta,
        )
        run.rounds_completed = next_round_num
        run.current_round_num = next_round_num
        run.status = RunStatus.paused_for_review.value
        run.metrics = _accumulate_metrics(run.metrics, metrics_acc)
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
        # Customer per-field overrides (type / strip) win over everything —
        # re-assert AFTER the patch so Part 1 / reflection can't revert them.
        _cg = field_constraints.enforce(
            db, api_definition_id, new_modules, new_version.country_global_text,
        )
        from app.ocr_optimizer.service import skill_render
        _sk = skill_render.resolve(db, api_definition_id, new_modules)
        new_version.composed_schema = composer.assemble_schema(new_modules)
        new_version.composed_prompt = composer.assemble_prompt(
            new_modules,
            country_global=_cg,
            skill_content=_sk,
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

    # Noise-sample gate (ADR-001, flag-gated): require 3 anchors + N noise so the
    # held-out val split is meaningful. Default OFF → only MIN_SAMPLES applies.
    from app.core.config import get_settings as _get_settings
    if getattr(_get_settings(), "SKILL_NOISE_GATE", False):
        from app.ocr_optimizer.skilltrain import noise_gate
        if not noise_gate.is_ready(len(confirmed_ids)):
            need = noise_gate.shortfall(len(confirmed_ids))
            raise ValidationError(
                f"启动迭代需要至少 {noise_gate.required_total()} 个已审视样本"
                f"（3 锚点 + {noise_gate.NOISE_DEFAULT} 个多样化噪声样本），当前 "
                f"{len(confirmed_ids)} 个，还差 {need} 个。请再上传并审视 {need} 个"
                f"多样化样本后重试——这样留出验证才有足够样本，迭代结果才能泛化。"
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


def _accumulate_metrics(prev: dict | None, counters: dict | None) -> dict:
    """把本轮计数（_run_one_round 就地累加的 metrics dict）合并回 Run 级
    metrics。只按计数键合并——run.metrics 在轮内还承载 rejected_edits /
    meta_memory 等键（typed-edit 路径直写），不能整体覆盖。

    结构审查修正：此前这是个假装干活的 no-op（只 dict(prev) 返回、从不读
    第二个参数），_run_one_round 累加的 total_ocr_calls/total_llm_calls
    从未落回 run.metrics。
    """
    out = dict(prev or {})
    for k in ("total_ocr_calls", "total_llm_calls"):
        if counters and k in counters:
            out[k] = counters[k]
    return out


# ── Single round ─────────────────────────────────────────────────────────────

def detect_module_regressions(
    prev_acc: dict[str, float],
    curr_acc: dict[str, float],
    eps: float = 0.01,
) -> list[str]:
    """Pure: module_keys whose accuracy DROPPED vs the previous round.

    Per-field monotonic check (req 3). Only modules present in BOTH maps are
    considered; a drop greater than eps counts as a regression. Zero OCR — the
    caller feeds it the round-entry evaluations it already has.

    批次6：eps 从 1e-6 提到 0.01——生产 OCR 是 LLM，同 prompt 两次输出天然
    抖动；1e-6 会把纯浮点/采样噪声标成 REGRESSION，触发无谓的重反思。任何
    真实回归至少是「一个样本上一个单元」的量化步长（3 样本标量字段 ≈ 0.33），
    远大于 0.01。
    """
    out: list[str] = []
    for mk, cur in curr_acc.items():
        prev = prev_acc.get(mk)
        if prev is not None and cur < prev - eps:
            out.append(mk)
    return out


def _prev_round_module_acc(
    db: Session, run_id: uuid.UUID, round_num: int,
) -> dict[str, float]:
    """{module_key: aggregate_accuracy} for the round immediately before
    `round_num` in this run (empty for round 1)."""
    if round_num <= 1:
        return {}
    prev = (
        db.query(OcrOptimizationRound)
        .filter(
            OcrOptimizationRound.run_id == run_id,
            OcrOptimizationRound.round_num == round_num - 1,
        )
        .first()
    )
    if not prev:
        return {}
    its = (
        db.query(OcrModuleIteration)
        .filter(OcrModuleIteration.round_id == prev.id)
        .all()
    )
    return {it.module_key: (it.aggregate_accuracy or 0.0) for it in its}


def _field_trajectories(db: Session, run_id) -> dict[str, list[float]]:
    """Per-field accuracy trajectory across this run's rounds (chronological),
    for P3 slow-update guardians. Pure read of persisted iterations."""
    from collections import defaultdict

    from app.ocr_optimizer.models import OcrModuleIteration, OcrOptimizationRound

    rows = (
        db.query(
            OcrOptimizationRound.round_num,
            OcrModuleIteration.module_key,
            OcrModuleIteration.aggregate_accuracy,
        )
        .join(OcrModuleIteration, OcrModuleIteration.round_id == OcrOptimizationRound.id)
        .filter(
            OcrOptimizationRound.run_id == run_id,
            OcrModuleIteration.aggregate_accuracy.isnot(None),
        )
        .order_by(OcrOptimizationRound.round_num)
        .all()
    )
    traj: dict[str, list[float]] = defaultdict(list)
    for _rnum, mk, acc in rows:
        traj[mk].append(float(acc))
    return dict(traj)


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
    # Availability-aware resolution: a row pinned to an unreachable provider
    # (e.g. gemini on a 大陆 server) must not zero out every round's accuracy.
    from app.processors.factory import ProcessorFactory
    _proc, _model = ProcessorFactory.resolve_spec(
        api_def.processor_type, api_def.model_name
    )
    ocr_outputs = ocr_runner.run_ocr_on_samples(
        db,
        sample_document_ids=sample_ids,
        composed_prompt=current_version.composed_prompt,
        composed_schema=current_version.composed_schema,
        processor_spec=_proc,
        model_name=_model,
    )
    metrics["total_ocr_calls"] += len(sample_ids)
    rnd.ocr_raw_outputs = ocr_outputs
    rnd.phase = RoundPhase.analyzing.value
    db.commit()

    # ── Step 1.5: 评测有效性门（批次2）────────────────────────────────────
    # 传输/解析级失败的样本（_error / _raw / 空响应）不参与任何质量决策：
    # 按 0 分聚合会让一次网络抖动把整轮分数打塌，劫持早停/单调守护，并让
    # optimizer 对着「OCR error」假 diff 改 prompt。降级到 mock 的轮同理——
    # 对固定 fixture 打分再优化是纯噪声迭代。
    excluded_samples: dict[str, str] = {}
    for sid in sample_ids:
        reason = ocr_runner.invalid_output_reason(ocr_outputs.get(str(sid)))
        if reason:
            excluded_samples[str(sid)] = reason
    valid_sample_ids = [sid for sid in sample_ids if str(sid) not in excluded_samples]
    degraded_to_mock = ProcessorFactory.is_degraded_to_mock(_proc, api_def.processor_type)
    # 有效样本太少（< min(2, 总样本数)）时该轮不可判定
    min_valid = min(2, len(sample_ids))
    round_invalid = degraded_to_mock or len(valid_sample_ids) < min_valid
    rnd.eval_quality = {
        "excluded_samples": excluded_samples,
        "valid_sample_count": len(valid_sample_ids),
        "total_sample_count": len(sample_ids),
        "degraded_to_mock": degraded_to_mock,
        "invalid": round_invalid,
    }
    if round_invalid:
        logger.warning(
            "round %d: eval INVALID (degraded_to_mock=%s, valid=%d/%d, excluded=%s) "
            "— skipping scoring & optimization, keeping current version",
            round_num, degraded_to_mock, len(valid_sample_ids), len(sample_ids),
            excluded_samples,
        )
        rnd.phase = RoundPhase.failed.value
        rnd.overall_accuracy = None  # 不可信分数绝不落库参与决策
        rnd.next_version_id = current_version.id  # 合法「无变化」信号
        rnd.duration_ms = int(time.time() * 1000) - start_ms
        rnd.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(rnd)
        return rnd
    if excluded_samples:
        logger.warning(
            "round %d: excluding %d/%d samples from scoring: %s",
            round_num, len(excluded_samples), len(sample_ids), excluded_samples,
        )

    # ── Step 2: Slice + evaluate each module（仅有效样本）─────────────────
    from app.core.config import get_settings as _get_settings
    _s = _get_settings()
    iterations, _target_acc, _train_set = _evaluate_round(
        db, run=run, rnd=rnd, round_num=round_num, modules=modules,
        ocr_outputs=ocr_outputs, ground_truths=ground_truths,
        valid_sample_ids=valid_sample_ids, settings=_s,
    )

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
    # Country-locked fields are EXCLUDED from optimization: they're still
    # evaluated (measured) above, but their recognition spec is governed by
    # Part 1 and must not enter the Part-2 modifiable/reflection scope. The
    # composition step additionally PINS them back to the country spec.
    _locked = field_constraints.locked_fields_for_api(db, run.api_definition_id)
    updates = _optimize_failing_modules(
        db, run=run, round_num=round_num, modules=modules,
        iterations=iterations, target_acc=_target_acc, train_set=_train_set,
        locked=_locked, settings=_s, metrics=metrics,
    )

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
    return _compose_next_version(
        db, run=run, rnd=rnd, round_num=round_num,
        current_version=current_version, modules=modules,
        iterations=iterations, meta=meta, updates=updates,
        locked=_locked, settings=_s, start_ms=start_ms,
    )


def _evaluate_round(
    db: Session,
    *,
    run: OcrOptimizationRun,
    rnd: OcrOptimizationRound,
    round_num: int,
    modules: list[OcrModule],
    ocr_outputs: dict[str, Any],
    ground_truths: dict[str, dict],
    valid_sample_ids: list[uuid.UUID],
    settings: Any,
) -> tuple[list[OcrModuleIteration], dict[str, float], set[str]]:
    """Step 2（_run_one_round 拆分）：切片评估 + 回归标记 + 分数聚合。

    评分单一事实源（结构审查 F9）：切片/GT 对齐/模糊比较/聚合全部走
    eval/harness.score_outputs——此前 orchestrator 手写了一份与 harness
    刻意相同的循环，改评分时两边都要记得同步。这里只负责把 harness 的
    per_sample 形状映射进 OcrModuleIteration（消费方：optimizer 失败样本
    上下文、UI、历史轨迹——字段名保持不变）。

    设置 rnd.overall_accuracy / per_sample_accuracy；返回
    (iterations, target_acc, train_set) 供 Step 3 选目标。
    """
    from ..eval.harness import module_specs_from_orm, score_outputs

    iterations: list[OcrModuleIteration] = []

    _valid_keys = [str(s) for s in valid_sample_ids]
    report = score_outputs(
        module_specs_from_orm(modules),
        {k: ocr_outputs.get(k) for k in _valid_keys},
        {k: ground_truths.get(k) for k in _valid_keys},
    )
    # 非 strict 模式下 module_scores 与 modules 一一对应且保序（strict 才会
    # 跳过无 GT 模块），按序 zip 而非按 key 匹配——防重复 module_key 丢行。
    for mod, ms in zip(modules, report.module_scores):
        per_sample = [
            {
                "sample_doc_id": p["doc_id"],
                "ocr_sliced": p.get("got"),
                "ground_truth": p.get("expected"),
                "matched": p["matched"],
                "field_accuracy": p["accuracy"],
                "diff_detail": p.get("diff", ""),
            }
            for p in ms.per_sample
        ]
        it = OcrModuleIteration(
            id=uuid.uuid4(),
            round_id=rnd.id,
            module_id=mod.id,
            module_key=mod.module_key,
            per_sample_results=per_sample,
            aggregate_accuracy=round(ms.accuracy, 4),
        )
        iterations.append(it)
        db.add(it)

    # ── Per-field regression flag (CLAUDE.md §④, req 3) ──────────────────
    # Compare each module's accuracy at THIS round's entry against the prior
    # round's. A drop means the prior round's revision of that field made it
    # WORSE. We flag it (auditable "提示" + UI-surfaceable) so the loop's
    # existing machinery can react: the history-aware optimizer re-reflects it
    # this round (retry), and verify_module_fix can reject a bad new attempt
    # (fallback to the old prompt). Zero extra OCR — uses the entry eval.
    prev_acc = _prev_round_module_acc(db, run.id, round_num)
    if prev_acc:
        curr_acc = {it.module_key: (it.aggregate_accuracy or 0.0) for it in iterations}
        for mk in detect_module_regressions(prev_acc, curr_acc):
            for it in iterations:
                if it.module_key == mk:
                    note = (f"[REGRESSION] {mk} 准确率较上一轮下降 "
                            f"{prev_acc[mk]:.3f}→{curr_acc[mk]:.3f}；本轮将重试反思，"
                            f"若仍不优于上一轮则回退该字段旧 prompt。")
                    it.optimization_suggestion = (
                        (it.optimization_suggestion or "") + " " + note
                    ).strip()
                    break

    # Per-sample overall accuracy (avg of module accuracies for that sample)
    # 仅有效样本 —— 被剔除样本不产生 0 分记录（原因在 rnd.eval_quality）。
    per_sample_accuracy: dict[str, float] = {}
    for sid in valid_sample_ids:
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

    # ── Held-out validation gate (ADR-001 P1, flag-gated) ────────────────
    # Default OFF → `target_acc` is the all-sample aggregate and overall_accuracy
    # is unchanged → byte-identical behavior. When ON, optimize on a TRAIN split
    # but score/select versions on a held-out VAL split (the trailing noise
    # samples), so a round's improvement must GENERALIZE — the existing monotonic
    # `_best_evaluated_version` then picks the best-on-val version (zero extra OCR).
    target_acc = {it.module_key: (it.aggregate_accuracy or 0.0) for it in iterations}
    train_set = {str(s) for s in valid_sample_ids}  # 有效样本；held-out 时再切分
    if getattr(settings, "SKILL_HELDOUT_GATE", False) and len(valid_sample_ids) >= 8 and iterations:
        from app.ocr_optimizer.skilltrain import heldout
        val_set = {str(v) for v in heldout.val_ids(valid_sample_ids, frac=settings.SKILL_HELDOUT_VAL_FRAC)}
        train_set = {str(s) for s in valid_sample_ids} - val_set
        overall_val, target_acc = heldout.split_accuracy(iterations, val_set)
        rnd.overall_accuracy = overall_val  # version selection on held-out val
        logger.info(
            "round %d: held-out gate ON — val_acc=%.4f (%d val / %d train)",
            round_num, overall_val, len(val_set), len(valid_sample_ids) - len(val_set),
        )
    return iterations, target_acc, train_set


def _typed_optimize(
    out: dict,
    mod: OcrModule,
    it: OcrModuleIteration,
    result: dict,
    *,
    rej_buffer: Any,
    provider_spec: str,
    provider_model: str | None,
) -> None:
    """ADR-002: turn the LLM's `edits` into a bounded rule-section update.
    filter(rejected) → aggregate → clip(top-L) → apply_edits → verify the
    body+rules combo. On accept: out['new_rules']; on reject: buffer the edits."""
    from app.ocr_optimizer.service import composer as _comp
    from app.ocr_optimizer.skilltrain.apply import build_rule_update

    severity = 1.0 - float(getattr(it, "aggregate_accuracy", 0.0) or 0.0)
    new_rules, clipped = build_rule_update(
        getattr(mod, "rule_edits_text", "") or "", result["edits"],
        target_default=mod.module_key, rej_buffer=rej_buffer, severity=severity,
    )
    if not clipped:
        return
    rendered = _comp._render_rule_edits(new_rules)
    combined = mod.ocr_prompt or ""
    if rendered:
        combined = f"{combined}\n\n{_comp._RULE_EDITS_HEADER}\n{rendered}"
    proposed = dict(result)
    proposed["new_ocr_prompt"] = combined  # verify the body+rules combo
    verdict = module_optimizer.verify_module_fix(
        module=mod, iteration=it, proposed=proposed,
        processor_spec=provider_spec, model_name=provider_model,
    )
    out["llm_calls"] += 1
    if verdict.get("verdict") == "reject":
        out["rejected"] = result
        out["verdict"] = verdict
        out["rejected_edits"] = clipped
    else:
        out["result"] = result
        out["new_rules"] = new_rules
        out["accepted_edits"] = clipped


def _optimize_and_verify(
    mod: OcrModule,
    it: OcrModuleIteration,
    *,
    histories: dict[str, list],
    provider_spec: str,
    provider_model: str | None,
    typed: bool,
    meta_hint: str,
    uf_for: Any,
    rej_buffer: Any,
) -> dict:
    """纯 LLM（无 DB），可在工作线程跑。返回供主线程串行应用的结果。
    单字段任何异常都吞掉（返回空结果）——绝不让一个字段拖垮整轮。"""
    out = {"mod": mod, "it": it, "result": None, "rejected": None, "verdict": None,
           "llm_calls": 0, "new_rules": None, "accepted_edits": None, "rejected_edits": None}
    try:
        result = module_optimizer.optimize_module(
            module=mod, iteration=it, history=histories.get(mod.module_key, []),
            processor_spec=provider_spec, model_name=provider_model,
            meta_hint=meta_hint, user_feedback=uf_for(mod),
        )
        out["llm_calls"] += 1
        if typed and result and result.get("edits"):
            _typed_optimize(out, mod, it, result,
                            rej_buffer=rej_buffer,
                            provider_spec=provider_spec,
                            provider_model=provider_model)
        elif result and (result.get("new_ocr_prompt") or result.get("new_description")):
            # 批次6 保留性守护：整体重写丢弃客户反馈内容 → 直接 reject
            # （不浪费判官 LLM 调用）。红线⑤「累积不覆盖」的 round 路径闸门。
            if result.get("new_ocr_prompt") and not \
                    module_optimizer.customer_feedback_preserved(
                        mod.ocr_prompt, result["new_ocr_prompt"]):
                out["rejected"] = result
                out["verdict"] = {
                    "verdict": "reject",
                    "reasoning": "rewrite drops customer feedback lines (保留性守护)",
                }
                return out
            verdict = module_optimizer.verify_module_fix(
                module=mod, iteration=it, proposed=result,
                processor_spec=provider_spec, model_name=provider_model,
            )
            out["llm_calls"] += 1
            if verdict.get("verdict") == "reject":
                out["rejected"] = result
                out["verdict"] = verdict
                result = None
            out["result"] = result
        else:
            out["result"] = result
    except Exception as exc:  # noqa: BLE001
        logger.warning("module optimize failed for %s: %s", mod.module_key, exc)
    return out


def _optimize_failing_modules(
    db: Session,
    *,
    run: OcrOptimizationRun,
    round_num: int,
    modules: list[OcrModule],
    iterations: list[OcrModuleIteration],
    target_acc: dict[str, float],
    train_set: set[str],
    locked: set[str],
    settings: Any,
    metrics: dict,
) -> dict[str, dict]:
    """Step 3（_run_one_round 拆分）：对未达标字段并发跑 optimizer+判官，
    把 accept 的变更收进 `updates`（供 Step 5 组装），reject 的记注释保旧
    prompt。性能：各字段完全独立 → 线程池并发纯 LLM 部分；DB 写回主线程
    串行（SQLAlchemy session 非线程安全；session 配 expire_on_commit=False，
    工作线程读已加载 ORM 属性安全）。
    """
    provider_spec = _split_provider(run.llm_provider)[0]
    provider_model = _split_provider(run.llm_provider)[1]
    updates: dict[str, dict] = {}

    # ADR-002 typed-edit mode (flag-gated): bounded edits evolve a per-field rule
    # section instead of rewriting ocr_prompt. Rejected edits persist across rounds
    # so the optimizer doesn't re-propose them. Default OFF → none of this runs.
    # Per-field USER FEEDBACK (overlay) → injected as reflection context per module.
    from app.domain import overlay as _pes
    _field_feedback = (_pes.get_overlay(db, run.api_definition_id) or {}).get("field_feedback") or {}

    def _uf_for(mod) -> str:
        return (
            _field_feedback.get(mod.module_key)
            or _field_feedback.get(field_constraints.field_leaf(mod.json_path))
            or _field_feedback.get(getattr(mod, "display_name", "") or "")
            or ""
        )

    _typed = getattr(settings, "SKILL_TYPED_EDITS", False)
    _rej_buffer = None
    _round_accepted_ops: list[str] = []
    _round_rejected_ops: list[str] = []
    _meta_hint = ""
    if _typed:
        from app.ocr_optimizer.skilltrain.buffer import RejectedEditBuffer
        _rej_buffer = RejectedEditBuffer.from_list((run.metrics or {}).get("rejected_edits"))
        # P-D: meta-memory hint from PRIOR rounds (gated by SKILL_META_MEMORY).
        if getattr(settings, "SKILL_META_MEMORY", False):
            from app.ocr_optimizer.skilltrain import meta_skill
            _meta_hint = meta_skill.render_meta_hint((run.metrics or {}).get("meta_memory") or {})

    # Optimize modules under target on the TRAIN split (`target_acc`); with the
    # held-out gate OFF this equals the all-sample aggregate (unchanged behavior).
    targets = [(mod, it) for mod, it in zip(modules, iterations)
               if target_acc.get(it.module_key, it.aggregate_accuracy or 0.0) < 1.0
               and field_constraints.field_leaf(mod.json_path) not in locked]

    # Edit discipline (ADR-001 P1, flag-gated): only optimize modules whose error
    # is SYSTEMATIC (defect, not a one-off lapse), and clip to the top-L by
    # severity (learning-rate) so a round makes a bounded step. Default OFF →
    # targets unchanged.
    if getattr(settings, "SKILL_EDIT_DISCIPLINE", False) and iterations:
        from app.ocr_optimizer.skilltrain import targeting
        _keep = targeting.disciplined_targets(iterations, target_acc, train_ids=train_set)
        _before = len(targets)
        targets = [(m, it) for m, it in targets if it.module_key in _keep]
        if len(targets) != _before:
            logger.info(
                "round %d: edit-discipline narrowed targets %d→%d (defect+clip)",
                round_num, _before, len(targets),
            )
    # history 含 DB 查询 → 主线程预取
    histories: dict[str, list] = {}
    for mod, _it in targets:
        h = persistence.load_recent_module_history(
            db, run.api_definition_id, mod.module_key, k=3
        )
        histories[mod.module_key] = [x for x in h if x.get("round_num") != round_num]

    def _one(mod, it) -> dict:
        return _optimize_and_verify(
            mod, it, histories=histories,
            provider_spec=provider_spec, provider_model=provider_model,
            typed=_typed, meta_hint=_meta_hint, uf_for=_uf_for,
            rej_buffer=_rej_buffer,
        )

    if targets:
        from concurrent.futures import ThreadPoolExecutor
        workers = min(_MODULE_OPT_CONCURRENCY, len(targets))
        if workers <= 1:
            applied = [_one(mod, it) for mod, it in targets]
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                applied = list(ex.map(lambda mi: _one(*mi), targets))
    else:
        applied = []

    # 主线程串行应用结果到 ORM + 计数
    for out in applied:
        metrics["total_llm_calls"] += out["llm_calls"]
        mod, it = out["mod"], out["it"]
        if out["rejected"] is not None:
            verdict = out["verdict"] or {}
            logger.info(
                "round %d: verifier rejected mutation for %s — keeping old prompt. reason: %s",
                round_num, mod.module_key, verdict.get("reasoning", "")[:120],
            )
            it.optimization_suggestion = (
                (out["rejected"].get("optimization_suggestion") or "")
                + f"\n\n[VERIFIER REJECT] {verdict.get('reasoning', '')[:200]}"
            )
        if out.get("new_rules") is not None:
            # ADR-002 typed-edit ACCEPT: evolve the rule section, freeze the body.
            res = out["result"] or {}
            it.aggregate_diff = res.get("aggregate_diff")
            it.optimization_suggestion = res.get("optimization_suggestion")
            it.skill_feedback = res.get("skill_feedback") or None
            updates.setdefault(mod.module_key, {})["rule_edits_text"] = out["new_rules"]
        else:
            result = out["result"]
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
        # ADR-002: collect typed-edit ops → rejected buffer (filter next round) + meta (P-D)
        if _typed:
            _round_accepted_ops.extend(e.op for e in (out.get("accepted_edits") or []))
            for e in (out.get("rejected_edits") or []):
                _round_rejected_ops.append(e.op)
                if _rej_buffer is not None:
                    _rej_buffer.add(e)
    db.commit()
    # ADR-002 P-D: persist rejected buffer + accumulate edit-op outcomes → meta memory
    # (cheap, additive; recorded whenever typed mode is on, for the optimizer hint
    # and P5 visibility). The hint injection itself is gated by SKILL_META_MEMORY.
    if _typed:
        from app.ocr_optimizer.skilltrain import meta_skill
        from app.ocr_optimizer.skilltrain.types import FieldEdit
        m = dict(run.metrics or {})
        if _rej_buffer is not None:
            m["rejected_edits"] = _rej_buffer.to_list()
        ops = dict(m.get("edit_ops") or {})
        ops["accepted"] = list(ops.get("accepted") or []) + _round_accepted_ops
        ops["rejected"] = list(ops.get("rejected") or []) + _round_rejected_ops
        m["edit_ops"] = ops
        m["meta_memory"] = meta_skill.summarize_edit_outcomes(
            [FieldEdit(op=o, target="") for o in ops["accepted"]],
            [FieldEdit(op=o, target="") for o in ops["rejected"]],
        )
        run.metrics = m
        db.commit()
    return updates


def _compose_next_version(
    db: Session,
    *,
    run: OcrOptimizationRun,
    rnd: OcrOptimizationRound,
    round_num: int,
    current_version: OcrPromptVersion,
    modules: list[OcrModule],
    iterations: list[OcrModuleIteration],
    meta: dict,
    updates: dict[str, dict],
    locked: set[str],
    settings: Any,
    start_ms: int,
) -> OcrOptimizationRound:
    """Step 5（_run_one_round 拆分）：模块保全守护 + 克隆 + compose。

    compose 失败 → 复用 current_version（红线③：合法「无变化」信号），
    该轮标 failed 但 job 不挂。返回完成态的 rnd。
    """
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
    # Country-locked modules must never be removed by meta either.
    _locked_keys = {
        m.module_key for m in modules
        if field_constraints.field_leaf(m.json_path) in locked
    }
    blocked_removes = requested_removes & (well_performing | _locked_keys)
    safe_removes = requested_removes - well_performing - _locked_keys
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
    # Never let meta rename a country-locked module away from its pinned key.
    for _k in list(renames):
        if _k in _locked_keys:
            del renames[_k]

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
        # Re-assert customer per-field overrides AFTER the round optimizer has
        # rewritten the modules — this is what stops a 3-round reflection from
        # reverting an explicit type/strip back to the country default.
        _cg = field_constraints.enforce(
            db, run.api_definition_id, new_modules, next_version.country_global_text,
        )
        from app.ocr_optimizer.service import skill_render
        _sk = skill_render.resolve(db, run.api_definition_id, new_modules)
        # P3 slow-update: a version-level PROTECTED guardian block derived
        # deterministically from each field's cross-round accuracy trajectory.
        # Flag-gated; not stored in any module body → step edits can't touch it.
        _guardian = None
        if getattr(settings, "SKILL_SLOW_UPDATE", False):
            from app.ocr_optimizer.skilltrain import slow_update as _su
            _guardian = (
                _su.render_guardian_block(
                    _su.compute_guardians(_field_trajectories(db, run.id))
                )
                or None
            )
        next_version.composed_schema = composer.assemble_schema(new_modules)
        next_version.composed_prompt = composer.assemble_prompt(
            new_modules,
            country_global=_cg,
            skill_content=_sk,
            guardian_block=_guardian,
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
    from app.processors.factory import ProcessorFactory
    proc, model = ProcessorFactory.resolve_spec(
        api_def.processor_type, api_def.model_name
    )
    return f"{proc}|{model}" if model else proc


def _split_provider(spec: str) -> tuple[str, str | None]:
    if "|" in spec:
        a, b = spec.split("|", 1)
        return a, b
    return spec, None
