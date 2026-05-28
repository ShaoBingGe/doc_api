"""
Customer-driven iteration pipeline (DB-backed v2).

Sequence triggered by `POST /api-definitions/{id}/customize`:
  1. Receive field diffs from the workspace (edits + adds).
  2. Persist a CustomizeJob row (status=queued, diffs stored in JSON column).
  3. In the background task:
     a. Run reflection over each diff → ReflectionResult by module_key.
     b. Fork the source ApiDefinition into a new one with its own api_code.
     c. Check the new ApiDefinition's sample count:
        - >= MIN_SAMPLES (3): proceed immediately to 3-round optimization.
        - < MIN_SAMPLES: park job in `waiting_for_samples`. Customer must
          upload more docs to the new ApiDefinition. When count crosses the
          threshold, the upload hook auto-resumes the job.
     d. After optimization (or skip): mark `completed`, store new_api_code.

Recovery on process boot:
  - Any job in `optimizing` that hasn't updated for STALE_OPTIMIZING_MIN is
    marked `failed` (the customer can re-trigger by saving again).
  - `waiting_for_samples` jobs stay parked — sample uploads pick them up.
  - `reflecting` / `forking` jobs are short-lived; if stuck, marked failed.
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.exceptions import NotFoundError, ValidationError
from app.core.database import SessionLocal
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus

from ..models import (
    CustomizeJob,
    CustomizeJobStatus,
    OcrModule,
    OcrPromptVersion,
    PromptVersionStatus,
    RunStatus,
    VersionOrigin,
)
from ..reflection import reflect_on_diffs
from . import composer, persistence, run_orchestrator

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

MIN_SAMPLES_FOR_ITERATION = 3
MAX_SAMPLES_HINT = 10  # informational only; messaging caps the suggestion at 9 new uploads
STALE_OPTIMIZING_MIN = 10  # minutes before an "optimizing" job is considered stuck


# ── DB serialization helpers ────────────────────────────────────────────────


def _job_to_dict(job: CustomizeJob) -> dict:
    """Render a CustomizeJob for the HTTP layer."""
    return {
        "job_id": str(job.id),
        "source_api_definition_id": str(job.source_api_definition_id),
        "new_api_definition_id": str(job.new_api_definition_id) if job.new_api_definition_id else None,
        "new_api_code": job.new_api_code,
        "status": job.status,
        "phase_detail": job.phase_detail or "",
        "rounds_done": job.rounds_done or 0,
        "rounds_total": job.rounds_total or 3,
        "overall_accuracy": job.overall_accuracy,
        "error_message": job.error_message,
        "reflection_summary": job.reflection_summary or [],
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def get_job_dict(db: Session, job_id: uuid.UUID) -> dict | None:
    job = db.get(CustomizeJob, job_id)
    if not job:
        return None
    return _job_to_dict(job)


def _update_job(db: Session, job: CustomizeJob, **kwargs) -> None:
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)


def find_waiting_job_for_api(db: Session, api_definition_id: uuid.UUID) -> CustomizeJob | None:
    """Return the most-recent `waiting_for_samples` job whose new ApiDef = api_definition_id.

    Used by the sample-upload hook to auto-resume.
    """
    return (
        db.query(CustomizeJob)
        .filter(
            CustomizeJob.new_api_definition_id == api_definition_id,
            CustomizeJob.status == CustomizeJobStatus.waiting_for_samples.value,
        )
        .order_by(CustomizeJob.created_at.desc())
        .first()
    )


def find_latest_active_job_for_api(db: Session, api_definition_id: uuid.UUID) -> CustomizeJob | None:
    """Return the most-recent job (any status except completed) tied to this
    ApiDefinition — either as source OR as fork target.

    Used by the frontend on workspace load to rehydrate the customize banner
    after a navigation. We exclude `completed` so users don't see stale
    "✓ 已生成新模板" cards forever. `failed` IS included so the customer can
    see what went wrong on the failed run.
    """
    return (
        db.query(CustomizeJob)
        .filter(
            (CustomizeJob.source_api_definition_id == api_definition_id)
            | (CustomizeJob.new_api_definition_id == api_definition_id),
            CustomizeJob.status != CustomizeJobStatus.completed.value,
        )
        .order_by(CustomizeJob.created_at.desc())
        .first()
    )


# ── Public API ───────────────────────────────────────────────────────────────


def submit_customize_job(
    db: Session,
    *,
    source_api_definition_id: uuid.UUID,
    diffs: list[dict],
    user_id: uuid.UUID | None = None,
) -> CustomizeJob:
    """Persist a job. Caller schedules `run_customize_job(job.id)` after commit."""
    if not diffs:
        raise ValidationError("No field corrections provided")

    job = CustomizeJob(
        id=uuid.uuid4(),
        source_api_definition_id=source_api_definition_id,
        diffs=diffs,
        status=CustomizeJobStatus.queued.value,
        phase_detail="排队中...",
        rounds_total=3,
        user_id=user_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("Created customize job %s for API %s with %d diff(s)",
                job.id, source_api_definition_id, len(diffs))
    return job


def run_customize_job(job_id: uuid.UUID) -> None:
    """Background task entry. Owns its own DB session."""
    db: Session = SessionLocal()
    try:
        job = db.get(CustomizeJob, job_id)
        if not job:
            logger.error("run_customize_job: no such job id=%s", job_id)
            return
        try:
            _execute_pipeline(db, job)
        except Exception as exc:
            logger.exception("Customize job %s failed: %s", job.id, exc)
            _update_job(
                db, job,
                status=CustomizeJobStatus.failed.value,
                error_message=str(exc)[:1024],
                completed_at=datetime.now(timezone.utc),
            )
    finally:
        db.close()


def resume_customize_job(job_id: uuid.UUID) -> bool:
    """Resume a waiting_for_samples job. Returns True if resumption was started.

    Safe to call multiple times (idempotent): if the job is already past
    `waiting_for_samples`, returns False without doing anything.
    """
    db: Session = SessionLocal()
    try:
        job = db.get(CustomizeJob, job_id)
        if not job:
            logger.warning("resume_customize_job: no such job %s", job_id)
            return False
        if job.status != CustomizeJobStatus.waiting_for_samples.value:
            logger.info("resume_customize_job: job %s not in waiting_for_samples (=%s)",
                        job.id, job.status)
            return False
        if not job.new_api_definition_id:
            logger.error("resume_customize_job: job %s has no new_api_definition_id", job.id)
            return False
        # Verify the new ApiDef now has enough samples
        new_api = db.get(ApiDefinition, job.new_api_definition_id)
        if not new_api:
            _update_job(db, job, status=CustomizeJobStatus.failed.value,
                        error_message="forked ApiDefinition was deleted",
                        completed_at=datetime.now(timezone.utc))
            return False
        cfg = new_api.config or {}
        ids = cfg.get("sample_document_ids") or []
        if len(ids) < MIN_SAMPLES_FOR_ITERATION:
            logger.info("resume_customize_job: job %s still has %d/%d samples, staying parked",
                        job.id, len(ids), MIN_SAMPLES_FOR_ITERATION)
            return False
        # Transition: kick off optimization
        _update_job(db, job,
                    status=CustomizeJobStatus.optimizing.value,
                    phase_detail="样本就绪，开始 3 轮迭代优化")
        try:
            _run_three_rounds(db, job, new_api)
        except Exception as exc:
            logger.exception("resume optimization failed for job %s: %s", job.id, exc)
            _update_job(db, job, status=CustomizeJobStatus.failed.value,
                        error_message=str(exc)[:1024],
                        completed_at=datetime.now(timezone.utc))
            return False
        return True
    finally:
        db.close()


def reap_stale_jobs() -> int:
    """Mark jobs stuck in transient phases as failed.

    Called once on process boot (see app.main lifespan). Counts marked.
    """
    db: Session = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_OPTIMIZING_MIN)
        # SQLite stores naive datetimes; compare via cast at the DB level.
        stuck_states = {
            CustomizeJobStatus.optimizing.value,
            CustomizeJobStatus.reflecting.value,
            CustomizeJobStatus.forking.value,
            CustomizeJobStatus.queued.value,
        }
        candidates = (
            db.query(CustomizeJob)
            .filter(CustomizeJob.status.in_(stuck_states))
            .all()
        )
        marked = 0
        for job in candidates:
            last_touch = job.updated_at or job.created_at
            if last_touch and last_touch.tzinfo is None:
                # Treat naive as UTC
                last_touch = last_touch.replace(tzinfo=timezone.utc)
            if last_touch and last_touch < cutoff:
                prior_status = job.status
                job.status = CustomizeJobStatus.failed.value
                job.error_message = (job.error_message or "") + \
                    f" [reaped: stale {prior_status} > {STALE_OPTIMIZING_MIN}min on boot]"
                job.completed_at = datetime.now(timezone.utc)
                marked += 1
        if marked:
            db.commit()
            logger.info("reap_stale_jobs: marked %d stale customize jobs as failed", marked)
        return marked
    finally:
        db.close()


# ── Pipeline ─────────────────────────────────────────────────────────────────


def _execute_pipeline(db: Session, job: CustomizeJob) -> None:
    diffs: list[dict] = list(job.diffs or [])
    src_id = job.source_api_definition_id
    src_api = db.get(ApiDefinition, src_id)
    if not src_api:
        raise NotFoundError(f"ApiDefinition {src_id} not found")
    src_version = persistence.get_active_version(db, src_id)
    if not src_version:
        raise ValidationError("Source API has no active prompt version")
    src_modules: list[OcrModule] = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == src_version.id)
        .order_by(OcrModule.order_index)
        .all()
    )
    modules_by_key = {m.module_key: {
        "module_key": m.module_key,
        "display_name": m.display_name,
        "description": m.description,
        "ocr_prompt": m.ocr_prompt,
        "schema_fragment": m.schema_fragment,
    } for m in src_modules}

    # ── Phase 1: reflection ───────────────────────────────────────────────
    _update_job(db, job,
                status=CustomizeJobStatus.reflecting.value,
                phase_detail="正在为每个字段调用反思 agent")
    reflections = reflect_on_diffs(
        diffs,
        modules_by_key=modules_by_key,
        processor_spec=src_api.processor_type or "gemini",
        model_name=src_api.model_name,
    )
    reflection_summary = [
        {
            "module_key": r.module_key,
            "kind": r.kind,
            "rationale": (r.rationale_summary or "")[:300],
            "skill_count": len(r.skill_outputs),
            "fix_suggestion_count": len(r.fix_suggestions),
        }
        for r in reflections.values()
    ]

    # ── Phase 2: fork ApiDefinition with new api_code ─────────────────────
    _update_job(db, job,
                status=CustomizeJobStatus.forking.value,
                phase_detail="复制为客户专属模板，分配新 api_code",
                reflection_summary=reflection_summary)
    new_api, new_version, _new_modules = _fork_api_definition(
        db, src_api=src_api, src_version=src_version, src_modules=src_modules,
        diffs=diffs, reflections=reflections, user_id=job.user_id,
    )
    _update_job(db, job,
                new_api_definition_id=new_api.id,
                new_api_code=new_api.api_code)

    # ── Phase 3: sample gate ──────────────────────────────────────────────
    # The fork inherits sample_document_ids from source. If the source had at
    # least MIN_SAMPLES, we proceed; otherwise park the job.
    cfg = new_api.config or {}
    ids = cfg.get("sample_document_ids") or []
    have = len(ids)
    if have < MIN_SAMPLES_FOR_ITERATION:
        need = MIN_SAMPLES_FOR_ITERATION - have
        msg = (
            f"已生成新模板 {new_api.api_code}。"
            f"当前 {have}/{MIN_SAMPLES_FOR_ITERATION} 样本，"
            f"还需上传 {need} 个不同格式的样本以启动 3 轮迭代优化。"
        )
        _update_job(db, job,
                    status=CustomizeJobStatus.waiting_for_samples.value,
                    phase_detail=msg)
        logger.info("Job %s parked in waiting_for_samples (%d/%d samples)",
                    job.id, have, MIN_SAMPLES_FOR_ITERATION)
        return

    # ── Phase 4: 3-round optimization ─────────────────────────────────────
    _update_job(db, job,
                status=CustomizeJobStatus.optimizing.value,
                phase_detail="迭代优化 · 第 1 轮")
    _run_three_rounds(db, job, new_api)


def _run_three_rounds(db: Session, job: CustomizeJob, new_api: ApiDefinition) -> None:
    """Phase 4: start_optimization + advance_round × 2 + finalize."""
    try:
        run = run_orchestrator.start_optimization(db, new_api.id, max_rounds=3)
    except ValidationError as exc:
        # Sample gate elsewhere should have prevented this, but guard anyway
        logger.warning("3-round optimization skipped for job %s: %s", job.id, exc)
        _update_job(db, job,
                    status=CustomizeJobStatus.completed.value,
                    phase_detail=f"已生成新模板（{new_api.api_code}），但跳过迭代：{exc}",
                    completed_at=datetime.now(timezone.utc))
        return

    _update_job(db, job, rounds_done=run.rounds_completed,
                phase_detail=f"迭代优化 · 第 {run.rounds_completed} 轮完成")

    # Rounds 2 & 3
    best_version_id = _latest_round_version(db, run.id) or None
    for _ in range(2):
        db.refresh(run)
        if run.status != RunStatus.paused_for_review.value:
            break
        if run.current_round_num >= run.max_rounds:
            break
        try:
            run = run_orchestrator.advance_round(db, run.id)
            _update_job(db, job, rounds_done=run.rounds_completed,
                        phase_detail=f"迭代优化 · 第 {run.rounds_completed} 轮完成")
        except Exception as exc:
            logger.exception("advance_round failed for job %s: %s", job.id, exc)
            break

    best_version_id = _latest_round_version(db, run.id) or best_version_id

    # Finalize → activate the best version
    overall_acc: float | None = None
    if best_version_id:
        try:
            run_orchestrator.finalize_run(db, run.id, best_version_id)
            final_v = db.get(OcrPromptVersion, best_version_id)
            if final_v:
                overall_acc = final_v.overall_accuracy
        except Exception as exc:
            logger.exception("finalize_run failed for job %s: %s", job.id, exc)

    _update_job(db, job,
                status=CustomizeJobStatus.completed.value,
                overall_accuracy=overall_acc,
                phase_detail=f"已完成 3 轮迭代，新模板 api_code = {new_api.api_code}",
                completed_at=datetime.now(timezone.utc))


def _latest_round_version(db: Session, run_id: uuid.UUID) -> uuid.UUID | None:
    from ..models import OcrOptimizationRound
    r = (
        db.query(OcrOptimizationRound)
        .filter(OcrOptimizationRound.run_id == run_id)
        .order_by(OcrOptimizationRound.round_num.desc())
        .first()
    )
    return r.next_version_id if r and r.next_version_id else None


# ── Forking ──────────────────────────────────────────────────────────────────


def _fork_api_definition(
    db: Session,
    *,
    src_api: ApiDefinition,
    src_version: OcrPromptVersion,
    src_modules: list[OcrModule],
    diffs: list[dict],
    reflections: dict[str, Any],
    user_id: uuid.UUID | None,
) -> tuple[ApiDefinition, OcrPromptVersion, list[OcrModule]]:
    new_api_code = _next_customer_api_code(db, src_api)
    new_name = f"{src_api.name} (自定义)"

    src_cfg = dict(src_api.config or {})
    new_cfg = copy.deepcopy(src_cfg)
    new_cfg["source_template_id"] = str(src_api.id)
    new_cfg["fork_origin"] = "customer_customize"

    new_api = ApiDefinition(
        user_id=user_id,
        name=new_name,
        api_code=new_api_code,
        description=src_api.description,
        status=ApiDefinitionStatus.draft,
        version=1,
        response_schema=src_api.response_schema,
        processor_type=src_api.processor_type,
        model_name=src_api.model_name,
        config=new_cfg,
    )
    db.add(new_api)
    db.flush()

    new_version = OcrPromptVersion(
        id=uuid.uuid4(),
        api_definition_id=new_api.id,
        version="1",
        parent_version_id=src_version.id,
        status=PromptVersionStatus.active.value,
        origin=VersionOrigin.manual_edit.value,
        composed_prompt="",
        composed_schema=None,
        notes=f"forked from {src_api.api_code} v{src_version.version} via customer customize",
        activated_at=datetime.now(timezone.utc),
    )

    edits_by_key: dict[str, dict] = {}
    add_specs: list[dict] = []
    for d in diffs:
        if d.get("kind") == "edit":
            mk = d.get("module_key")
            if not mk:
                continue
            patch: dict[str, Any] = {}
            r = reflections.get(mk)
            if r and r.description_patch:
                patch["description"] = r.description_patch
            fix_text = "\n\n".join(r.fix_suggestions) if r else ""
            if fix_text:
                patch["__prompt_suffix"] = fix_text
            if d.get("corrected_value"):
                hint = f"客户在样本中提供的正确值示例：{d['corrected_value']}"
                patch["__prompt_suffix"] = (patch.get("__prompt_suffix", "") + "\n" + hint).strip()
            if d.get("corrected_format") and d.get("corrected_format") != d.get("original_format"):
                patch["__schema_type"] = d["corrected_format"]
            if patch:
                edits_by_key[mk] = patch
        elif d.get("kind") == "add":
            add_specs.append(d)

    new_modules: list[OcrModule] = []
    for m in src_modules:
        patch = edits_by_key.get(m.module_key, {})
        new_modules.append(_clone_module(m, new_version_id=new_version.id, patch=patch))

    order_start = max((m.order_index for m in new_modules), default=-1) + 1
    for i, d in enumerate(add_specs):
        new_modules.append(_module_from_add_diff(
            d, new_version_id=new_version.id, order_index=order_start + i,
            reflection_outputs=reflections.get(f"_new_{diffs.index(d)}"),
        ))

    try:
        new_version.composed_schema = composer.assemble_schema(new_modules)
        new_version.composed_prompt = composer.assemble_prompt(new_modules)
    except ValueError as exc:
        raise ValidationError(f"Compose failed for forked version: {exc}") from exc

    db.add(new_version)
    for m in new_modules:
        db.add(m)

    new_api.prompt_version_id = new_version.id
    new_api.status = ApiDefinitionStatus.draft
    db.commit()
    db.refresh(new_api)
    db.refresh(new_version)
    return new_api, new_version, new_modules


def _clone_module(src: OcrModule, *, new_version_id: uuid.UUID, patch: dict) -> OcrModule:
    new_description = patch.get("description") or src.description
    new_prompt = src.ocr_prompt
    suffix = patch.get("__prompt_suffix")
    if suffix:
        new_prompt = (src.ocr_prompt or "").rstrip() + "\n\n# 客户反馈补充\n" + suffix.strip()

    new_schema = src.schema_fragment
    schema_type = patch.get("__schema_type")
    if schema_type:
        new_schema = dict(src.schema_fragment or {})
        type_map = {
            "string": "STRING", "text": "STRING",
            "number": "NUMBER", "integer": "INTEGER",
            "date": "STRING", "boolean": "BOOLEAN",
            "array": "ARRAY",
        }
        mapped = type_map.get(schema_type.lower(), schema_type.upper())
        new_schema["type"] = mapped
        if schema_type.lower() == "date":
            new_schema["format"] = "date"

    return OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=new_version_id,
        module_key=src.module_key,
        display_name=src.display_name,
        description=new_description,
        json_path=src.json_path,
        schema_fragment=new_schema,
        ocr_suggestions=copy.deepcopy(src.ocr_suggestions or {}),
        ocr_prompt=new_prompt,
        skill_ids=list(src.skill_ids or []),
        order_index=src.order_index,
        status=src.status,
        module_accuracy=None,
    )


def _module_from_add_diff(
    diff: dict, *, new_version_id: uuid.UUID, order_index: int, reflection_outputs,
) -> OcrModule:
    new_name = diff.get("corrected_name") or "new_field"
    module_key = _to_snake(new_name)
    format_str = (diff.get("corrected_format") or "string").lower()
    type_map = {
        "string": "STRING", "text": "STRING",
        "number": "NUMBER", "integer": "INTEGER",
        "date": "STRING", "boolean": "BOOLEAN", "array": "ARRAY",
    }
    schema_type = type_map.get(format_str, "STRING")
    schema_fragment: dict = {"type": schema_type}
    if format_str == "date":
        schema_fragment["format"] = "date"

    corrected_value_hint = ""
    if diff.get("corrected_value"):
        corrected_value_hint = f"客户提供的样例值：{diff['corrected_value']}"
    ocr_prompt = (
        f"你负责从文档中识别「{new_name}」字段。\n\n"
        f"输出位置（json_path）：$[*].{new_name}\n"
        f"该字段类型：{schema_type}\n\n"
        f"# 识别规则\n"
        f"{corrected_value_hint}\n\n"
        f"# 输出要求\n"
        f"找不到时输出 null。"
    )
    if reflection_outputs and reflection_outputs.skill_outputs:
        for so in reflection_outputs.skill_outputs:
            out = so.get("output") or {}
            if isinstance(out, dict):
                if isinstance(out.get("ocr_prompt"), str) and out["ocr_prompt"].strip():
                    ocr_prompt = out["ocr_prompt"]
                if isinstance(out.get("schema_fragment"), dict):
                    schema_fragment = out["schema_fragment"]
                if isinstance(out.get("module_key"), str) and out["module_key"]:
                    module_key = _to_snake(out["module_key"])

    return OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=new_version_id,
        module_key=module_key,
        display_name=new_name,
        description=f"客户新增字段：{new_name}",
        json_path=f"$[*].{new_name}",
        schema_fragment=schema_fragment,
        ocr_suggestions={"semantics": "客户新增 — 待优化器学习",
                         "position": "客户新增 — 待优化器学习",
                         "most_common_feature": "—",
                         "extra_features": []},
        ocr_prompt=ocr_prompt,
        skill_ids=[],
        order_index=order_index,
        status="active",
        module_accuracy=None,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


_SNAKE_RE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_RE_2 = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _to_snake(name: str) -> str:
    s1 = _SNAKE_RE_1.sub(r"\1_\2", name)
    s2 = _SNAKE_RE_2.sub(r"\1_\2", s1).lower()
    s3 = _NON_ALNUM_RE.sub("_", s2).strip("_")
    return s3 or "field"


def _next_customer_api_code(db: Session, src_api: ApiDefinition) -> str:
    base = src_api.api_code or "api"
    m = re.match(r"^(.*)-c(\d+)$", base)
    if m:
        base = m.group(1)

    n = 1
    while True:
        candidate = f"{base}-c{n}"
        exists = db.query(ApiDefinition).filter(ApiDefinition.api_code == candidate).first()
        if not exists:
            return candidate
        n += 1
        if n > 9999:
            return f"{base}-c{uuid.uuid4().hex[:8]}"
