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
    OcrOptimizationRound,
    OcrOptimizationRun,
    OcrPromptVersion,
    PromptVersionStatus,
    RunStatus,
    VersionOrigin,
)
from ..reflection import reflect_on_diffs
from . import composer, field_constraints, persistence, run_orchestrator

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

MIN_SAMPLES_FOR_ITERATION = 3


def required_samples() -> int:
    """Min confirmed samples to START iteration. When the noise gate is active
    (ADR-001), iteration requires 3 anchors + N noise = 12 so the held-out
    validation split is meaningful; otherwise the legacy MIN_SAMPLES_FOR_ITERATION.
    Keeps the customize-flow readiness/auto-resume aligned with _resolve_run_inputs."""
    try:
        from app.core.config import get_settings
        if getattr(get_settings(), "SKILL_NOISE_GATE", False):
            from app.ocr_optimizer.skilltrain import noise_gate
            return noise_gate.required_total()
    except Exception:  # noqa: BLE001 — never let config break the flow
        pass
    return MIN_SAMPLES_FOR_ITERATION
STALE_OPTIMIZING_MIN = 10  # minutes before an "optimizing" job is considered stuck

# Confidence tiers by # of confirmed (human-reviewed GT) samples. The iteration
# can START at MIN_SAMPLES_FOR_ITERATION, but 3 samples carry weak statistical
# weight — a learned rule easily overfits those few invoices. Surface this so
# the customer knows whether to trust convergence or upload more.
LOW_CONFIDENCE_SAMPLES = 5    # < this (but >= MIN) → low confidence
HIGH_CONFIDENCE_SAMPLES = 10  # >= this → high confidence


def sample_confidence(confirmed: int) -> dict:
    """Map a confirmed-sample count to an iteration-confidence descriptor.

    Returns {level, label, can_iterate, message, confirmed, min_required,
    recommended} — consumed by the samples-review endpoint and the job dict so
    the UI can warn "3 个样本可启动但易过拟合，建议补到 ≥5".
    """
    confirmed = max(0, int(confirmed))
    req = required_samples()
    if confirmed < req:
        need = req - confirmed
        if req > MIN_SAMPLES_FOR_ITERATION:
            msg = (f"还需 {need} 个多样化样本才能启动迭代（共需 {req}：3 锚点 + "
                   f"{req - MIN_SAMPLES_FOR_ITERATION} 个噪声样本作留出验证；当前 {confirmed}）。")
        else:
            msg = f"还需 {need} 个「已审视」样本才能启动迭代（当前 {confirmed}/{req}）。"
        return {
            "level": "insufficient", "label": "样本不足", "can_iterate": False,
            "message": msg,
            "confirmed": confirmed, "min_required": req,
            "recommended": max(req, LOW_CONFIDENCE_SAMPLES),
        }
    if confirmed < LOW_CONFIDENCE_SAMPLES:
        return {
            "level": "low", "label": "低置信", "can_iterate": True,
            "message": f"样本偏少（{confirmed} 个），识别规则可能过拟合到当前样本；"
                       f"建议补到 ≥{LOW_CONFIDENCE_SAMPLES} 个以提升稳健性。",
            "confirmed": confirmed, "min_required": MIN_SAMPLES_FOR_ITERATION,
            "recommended": LOW_CONFIDENCE_SAMPLES,
        }
    if confirmed < HIGH_CONFIDENCE_SAMPLES:
        return {
            "level": "medium", "label": "中置信", "can_iterate": True,
            "message": f"样本数适中（{confirmed} 个）。补到 ≥{HIGH_CONFIDENCE_SAMPLES} 个可进一步提升泛化置信。",
            "confirmed": confirmed, "min_required": MIN_SAMPLES_FOR_ITERATION,
            "recommended": HIGH_CONFIDENCE_SAMPLES,
        }
    return {
        "level": "high", "label": "高置信", "can_iterate": True,
        "message": f"样本充足（{confirmed} 个），规则泛化置信度高。",
        "confirmed": confirmed, "min_required": MIN_SAMPLES_FOR_ITERATION,
        "recommended": HIGH_CONFIDENCE_SAMPLES,
    }

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
        "options": job.options or {},
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
    out = _job_to_dict(job)
    # Attach sample-confidence (computed on the source ApiDef's confirmed count)
    # so the UI can warn about low-sample overfitting alongside job progress.
    count_id = job.source_api_definition_id or job.new_api_definition_id
    if count_id is not None:
        confirmed, _total = count_confirmed_samples(db, count_id)
        out["sample_confidence"] = sample_confidence(confirmed)
    return out


def _update_job(db: Session, job: CustomizeJob, **kwargs) -> None:
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)


# ── 反思语料收集 ─────────────────────────────────────────────────────────────
# customer_iteration 拆分第二刀：纯读 DB 的证据语料构建移至
# reflection_context.py。facade 重导出，调用方零改动。
from .reflection_context import (  # noqa: E402,F401 — facade re-export
    _build_cross_doc_context_for_diffs,
    _build_sample_outputs,
)


# (C4 cleanup) — _mirror_source_samples_to_fork removed.
# Phase 19 collapsed the fork ApiDef onto source, so there's no longer a
# second ApiDef to mirror docs to. Every call site was either
# - the post-fork block in _execute_pipeline (removed in Phase 19), or
# - the auto-resume branch in maybe_auto_resume_for_api (removed in C5).


# ── 文档 structured_data 同步 ────────────────────────────────────────────────
# customer_iteration 拆分第三刀：定制/迭代后的文档同步（Phase 23.3 rename
# sweep + Phase 25 re-OCR）移至 doc_sync.py。facade 重导出，调用方零改动。
from .doc_sync import (  # noqa: E402,F401 — facade re-export
    _reocr_all_docs_with_active_prompt,
    _rewrite_all_docs_structured_data,
)


def find_waiting_job_for_api(db: Session, api_definition_id: uuid.UUID) -> CustomizeJob | None:
    """Return the most-recent `waiting_for_samples` job whose new ApiDef
    OR source ApiDef = api_definition_id.

    Phase 19 collapsed the fork onto source, so for any job created
    after Phase 19 the two foreign keys point at the same row — either
    side of the OR matches. We intentionally KEEP the OR for backwards
    compatibility with pre-Phase-19 job rows already in the DB, whose
    `new_api_definition_id` may point at a separate -c1 ApiDef. Without
    the OR, those legacy jobs would be invisible to auto-resume.
    """
    from sqlalchemy import or_
    return (
        db.query(CustomizeJob)
        .filter(
            or_(
                CustomizeJob.new_api_definition_id == api_definition_id,
                CustomizeJob.source_api_definition_id == api_definition_id,
            ),
            CustomizeJob.status == CustomizeJobStatus.waiting_for_samples.value,
        )
        .order_by(CustomizeJob.created_at.desc())
        .first()
    )


def count_confirmed_samples(db: Session, api_definition_id: uuid.UUID) -> tuple[int, int]:
    """Return (confirmed, total) for the samples bound to an ApiDef.

    "confirmed" = the sample has at least one GT annotation (is_corrected=True
    or source=manual). "total" = raw count of sample_document_ids. Used by
    both the waiting-banner progress UI and the auto-resume gate.
    """
    from app.ocr_optimizer.service.ground_truth import has_ground_truth

    api = db.get(ApiDefinition, api_definition_id)
    if not api:
        return 0, 0
    ids = (api.config or {}).get("sample_document_ids") or []
    total = len(ids)
    confirmed = sum(1 for sid in ids if has_ground_truth(db, uuid.UUID(sid)))
    return confirmed, total


def maybe_auto_resume_for_api(api_definition_id: uuid.UUID) -> None:
    """Re-evaluate the sample gate for an ApiDef and kick off resume in a
    background thread if it's now met. Idempotent and safe to call from
    request handlers.

    Phase 12: when the waiting job matches via source_api_definition_id
    (single-workspace UX), copy the source's confirmed sample list to the
    fork ApiDef's config so the iteration's existing sample_document_ids
    flow keeps working without further changes.
    """
    from threading import Thread

    db: Session = SessionLocal()
    try:
        waiting = find_waiting_job_for_api(db, api_definition_id)
        if not waiting:
            return
        # Phase 14a + 16 + 19: count confirmed on the SOURCE ApiDef.
        # Phase 19 collapsed customize onto source — the iteration runs
        # on source.id directly, so there's no fork-side count to consult.
        count_id = waiting.source_api_definition_id or waiting.new_api_definition_id
        if count_id is None:
            return
        confirmed, _ = count_confirmed_samples(db, count_id)
        if confirmed < required_samples():  # noise gate: don't auto-start until 12
            return
        # (C5 cleanup) — fork-mirror branch removed. Phase 19 makes
        # source ≡ fork, so the rebind condition is permanently false.
        job_id = waiting.id
        logger.info(
            "Auto-resuming customize job %s (confirmed %d/%d)",
            job_id, confirmed, MIN_SAMPLES_FOR_ITERATION,
        )
    finally:
        db.close()
    # Fire-and-forget; resume_customize_job owns its own session.
    Thread(
        target=resume_customize_job,
        args=(job_id,),
        daemon=True,
        name=f"customize-resume-{job_id}",
    ).start()


def set_sample_gt_confirmed(
    db: Session, document_id: uuid.UUID, *, confirmed: bool
) -> dict:
    """Toggle whether ALL annotations on a document are treated as GT.

    confirmed=True: bulk-set is_corrected=True on every annotation tied to
    the document.
    confirmed=False: bulk-set is_corrected=False (undo).

    Raises ValidationError when confirmed=True is requested but the document
    has zero annotations — typically because OCR failed during upload
    (Gemini outage). The customer should retry OCR before confirming.
    """
    from app.core.exceptions import ValidationError as _VE
    from app.models.annotation import Annotation
    from app.models.document import Document, DocumentStatus
    from sqlalchemy import func

    total = db.query(func.count(Annotation.id)).filter(
        Annotation.document_id == document_id
    ).scalar() or 0
    if confirmed and total == 0:
        doc = db.get(Document, document_id)
        # Document.status is a plain str column (not Enum); guard accordingly.
        status_str = str(doc.status) if doc and doc.status else "unknown"
        status_hint = (
            f"该样本 OCR 状态为 {status_str}；"
            "请先在文档工具栏点击「重试 OCR」生成标注，再确认 GT。"
        )
        raise _VE(f"无法确认空样本（标注数 = 0）。{status_hint}")

    db.query(Annotation).filter(
        Annotation.document_id == document_id
    ).update({Annotation.is_corrected: confirmed}, synchronize_session=False)
    db.commit()
    return {
        "document_id": str(document_id),
        "confirmed": confirmed,
        "annotations_total": int(total),
    }


def retry_ocr_on_sample(
    db: Session, *, api_definition_id: uuid.UUID, document_id: uuid.UUID
) -> dict:
    """Re-run OCR on a previously-failed sample using the ApiDef's current
    active prompt.

    The underlying Gemini call may still fail (proxy outage etc.). We
    catch that, mark the doc failed, and return a structured payload so
    the HTTP layer can return 200 with an error field instead of 500.

    Returns: {document_id, status, annotations_created, error?}
    """
    from app.models.annotation import Annotation
    from app.models.document import Document, DocumentStatus
    from app.schemas.document import ReprocessRequest
    from app.services.document_service import reprocess_document
    from sqlalchemy import func

    api_def = db.get(ApiDefinition, api_definition_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_definition_id} not found")
    doc = db.get(Document, document_id)
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")

    error_msg: str | None = None
    body = ReprocessRequest(prompt=None)
    try:
        reprocess_document(db, document_id, body)
    except Exception as exc:
        logger.warning(
            "retry_ocr_on_sample: reprocess failed for doc=%s — %s",
            document_id, exc,
        )
        # _run_extraction has set doc.status=failed and rolled back its own
        # txn; refresh and capture the message for the response.
        db.rollback()
        db.refresh(doc)
        doc.status = DocumentStatus.failed
        doc.error_message = (str(exc) or "OCR retry failed")[:1024]
        db.commit()
        error_msg = (str(exc) or "OCR retry failed")[:300]

    db.refresh(doc)
    total = db.query(func.count(Annotation.id)).filter(
        Annotation.document_id == document_id
    ).scalar() or 0

    return {
        "document_id": str(document_id),
        "status": str(doc.status) if doc.status else "unknown",
        "annotations_created": int(total),
        "error": error_msg,
    }


def find_latest_active_job_for_api(db: Session, api_definition_id: uuid.UUID) -> CustomizeJob | None:
    """Return the most-recent in-flight job tied to this ApiDefinition.

    Phase 19+ jobs always have source == new (the customize result lands
    on the source ApiDef itself), so the OR-match on either column is
    equivalent. The OR kept for pre-Phase-19 row compatibility.

    Used by the frontend on workspace load to rehydrate the customize banner.
    We exclude both `completed` and `failed` so:
      - completed jobs don't show "✓ 已激活新版本" cards forever
      - failed jobs (especially the reaped ones from boot) don't auto-resurface
        across sessions; a user can still find them by job_id if needed.
    """
    return (
        db.query(CustomizeJob)
        .filter(
            (CustomizeJob.source_api_definition_id == api_definition_id)
            | (CustomizeJob.new_api_definition_id == api_definition_id),
            CustomizeJob.status.notin_([
                CustomizeJobStatus.completed.value,
                CustomizeJobStatus.failed.value,
            ]),
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
    options: dict | None = None,
) -> CustomizeJob:
    """Persist a job. Caller schedules `run_customize_job(job.id)` after commit.

    options: {"save_as_new": bool, "new_name": str|None} — save_as_new runs the
    customize + iteration on a CLONE of the source ApiDef (own api_code), so
    the published source keeps serving unchanged.
    """
    if not diffs:
        raise ValidationError("No field corrections provided")

    job = CustomizeJob(
        id=uuid.uuid4(),
        source_api_definition_id=source_api_definition_id,
        diffs=diffs,
        options=options or None,
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

    Phase 17: Two execution paths now exist for a waiting job:
      (a) "post-fork" — legacy jobs that forked first then parked waiting
          for the FORK's sample count. Resume hands them straight to the
          iteration runner once the fork has 3 confirmed samples.
      (b) "pre-fork" — Phase 14a jobs that parked BEFORE reflection / fork
          to avoid burning LLM credits while the customer was still
          confirming samples on the SOURCE workspace. For these,
          `new_api_definition_id` is None. Resume re-enters _execute_pipeline
          which now sees ≥3 confirmed and proceeds through reflection +
          fork + iteration in one shot.
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

        # ── Path (b) pre-fork resume ──────────────────────────────────────
        if not job.new_api_definition_id:
            confirmed, total = count_confirmed_samples(db, job.source_api_definition_id)
            if confirmed < required_samples():
                logger.info(
                    "resume_customize_job(pre-fork): job %s source has %d/%d confirmed, parking",
                    job.id, confirmed, MIN_SAMPLES_FOR_ITERATION,
                )
                return False
            logger.info(
                "resume_customize_job(pre-fork): job %s source has %d/%d confirmed — entering _execute_pipeline",
                job.id, confirmed, MIN_SAMPLES_FOR_ITERATION,
            )
            try:
                _execute_pipeline(db, job)
            except Exception as exc:
                logger.exception("pre-fork resume failed for job %s: %s", job.id, exc)
                _update_job(db, job, status=CustomizeJobStatus.failed.value,
                            error_message=str(exc)[:1024],
                            completed_at=datetime.now(timezone.utc))
                return False
            return True

        # ── Path (a) post-fork resume ─────────────────────────────────────
        new_api = db.get(ApiDefinition, job.new_api_definition_id)
        if not new_api:
            _update_job(db, job, status=CustomizeJobStatus.failed.value,
                        error_message="forked ApiDefinition was deleted",
                        completed_at=datetime.now(timezone.utc))
            return False
        confirmed, total = count_confirmed_samples(db, job.new_api_definition_id)
        if confirmed < required_samples():
            logger.info(
                "resume_customize_job: job %s still has %d/%d confirmed samples (%d total), staying parked",
                job.id, confirmed, MIN_SAMPLES_FOR_ITERATION, total,
            )
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


def _clone_api_for_save_as_new(
    db: Session,
    src_api: ApiDefinition,
    *,
    new_name: str | None = None,
    user_id: uuid.UUID | None = None,
) -> ApiDefinition:
    """「另存为新模板」：把源 ApiDef 克隆成一个完全独立的新 API。

    复制内容：最优 prompt 版本（模块最多的那个）+ 全部字段模块 + 样本文档
    （Document/Annotation 行级复制，磁盘文件共享）+ pending_edits overlay。
    源 API（含已发布的）不被本次定制触碰，api_code 照常对外服务。

    GT 隔离是行级复制的根本原因：后续 cascade_rename_annotations 等操作按
    ApiDef 圈定文档；若与源共享 Document 行，对克隆的字段重命名会反向污染
    源工作区的 Ground Truth。
    """
    from app.models.annotation import Annotation as _Ann
    from app.models.document import Document as _Doc
    from app.models.api_definition import ApiDefinitionStatus as _Status

    # 1. 唯一 api_code：{源}-c1 / -c2 / ...
    base = src_api.api_code or "api"
    n = 1
    while (
        db.query(ApiDefinition)
        .filter(ApiDefinition.api_code == f"{base}-c{n}")
        .first()
    ):
        n += 1
    api_code = f"{base}-c{n}"

    # 2. 选源版本：模块最多者优先（与 _execute_pipeline 的防御规则一致）
    from sqlalchemy import func as _f
    cands = (
        db.query(OcrPromptVersion, _f.count(OcrModule.id).label("mc"))
        .outerjoin(OcrModule, OcrModule.prompt_version_id == OcrPromptVersion.id)
        .filter(OcrPromptVersion.api_definition_id == src_api.id)
        .group_by(OcrPromptVersion.id)
        .all()
    )
    if not cands:
        raise ValidationError("源 API 没有可复制的 prompt 版本")
    cands.sort(key=lambda x: (
        -int(x[1] or 0),
        -(x[0].created_at.timestamp() if x[0].created_at else 0),
    ))
    src_version = cands[0][0]
    src_modules = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == src_version.id)
        .order_by(OcrModule.order_index)
        .all()
    )

    # 3. 克隆 ApiDefinition 行（待验证状态——迭代完由客户决定是否发布）
    cfg = copy.deepcopy(src_api.config or {})
    src_sample_ids = list(cfg.get("sample_document_ids") or [])
    clone = ApiDefinition(
        id=uuid.uuid4(),
        user_id=user_id or src_api.user_id,
        tenant_id=src_api.tenant_id,
        name=(new_name or f"{src_api.name}-新模板"),
        api_code=api_code,
        description=src_api.description,
        status=_Status.pending_review,
        version=1,
        response_schema=copy.deepcopy(src_api.response_schema),
        processor_type=src_api.processor_type,
        model_name=src_api.model_name,
        config=cfg,
        pending_edits=copy.deepcopy(src_api.pending_edits) if src_api.pending_edits else None,
    )
    db.add(clone)
    db.flush()

    # 4. 行级复制样本文档 + 标注（GT 隔离）
    id_map: dict[str, str] = {}
    for sid in src_sample_ids:
        try:
            src_doc = db.get(_Doc, uuid.UUID(str(sid)))
        except Exception:  # noqa: BLE001
            src_doc = None
        if not src_doc:
            continue
        new_doc = _Doc(
            id=uuid.uuid4(),
            user_id=src_doc.user_id,
            organization_id=src_doc.organization_id,
            tenant_id=src_doc.tenant_id,
            filename=src_doc.filename,
            file_type=src_doc.file_type,
            file_size=src_doc.file_size,
            storage_path=src_doc.storage_path,  # 磁盘文件共享，不复制字节
            processor_key=src_doc.processor_key,
            api_definition_id=clone.id,
            status=src_doc.status,
        )
        db.add(new_doc)
        db.flush()
        id_map[str(src_doc.id)] = str(new_doc.id)
        for ann in db.query(_Ann).filter(_Ann.document_id == src_doc.id).all():
            db.add(_Ann(
                id=uuid.uuid4(),
                document_id=new_doc.id,
                processing_result_id=None,  # 源的 ProcessingResult 不随克隆
                result_version=None,
                created_by=ann.created_by,
                field_name=ann.field_name,
                field_value=ann.field_value,
                field_type=ann.field_type,
                bounding_box=copy.deepcopy(ann.bounding_box) if ann.bounding_box else None,
                source=ann.source,
                confidence=ann.confidence,
                is_corrected=ann.is_corrected,
                original_value=ann.original_value,
                original_bbox=copy.deepcopy(ann.original_bbox) if ann.original_bbox else None,
            ))

    cfg["sample_document_ids"] = [id_map[s] for s in src_sample_ids if s in id_map]
    cfg["cloned_from_api_id"] = str(src_api.id)
    clone.config = cfg
    flag_modified(clone, "config")

    # 5. 复制 prompt 版本 + 模块
    new_ver = OcrPromptVersion(
        id=uuid.uuid4(),
        api_definition_id=clone.id,
        version="1",
        parent_version_id=src_version.id,
        status=PromptVersionStatus.active.value,
        origin=VersionOrigin.init.value,
        composed_prompt=src_version.composed_prompt,
        composed_schema=copy.deepcopy(src_version.composed_schema),
        country_global_text=src_version.country_global_text,
        notes=f"save-as-new clone of {src_api.api_code} v{src_version.version}",
        activated_at=datetime.now(timezone.utc),
    )
    db.add(new_ver)
    db.flush()
    for m in src_modules:
        db.add(_clone_module(m, new_version_id=new_ver.id, patch={}))

    db.commit()
    db.refresh(clone)
    logger.info(
        "save-as-new: cloned ApiDef %s (%s) → %s (%s) with %d sample docs, %d modules",
        src_api.id, src_api.api_code, clone.id, clone.api_code,
        len(id_map), len(src_modules),
    )
    return clone


def _execute_pipeline(db: Session, job: CustomizeJob) -> None:
    diffs: list[dict] = list(job.diffs or [])
    src_id = job.source_api_definition_id
    src_api = db.get(ApiDefinition, src_id)
    if not src_api:
        raise NotFoundError(f"ApiDefinition {src_id} not found")

    # ── Phase 14a: sample gate FIRST ──────────────────────────────────────
    # Reflection burns LLM credits; we should not run it until we have all
    # the samples it can compare against. Per user spec: only after 3
    # already-confirmed samples does the reflection agent start.
    # We check the SOURCE ApiDef's count (single-workspace UX from Phase 12).
    confirmed_src, total_src = count_confirmed_samples(db, src_id)
    fork_done = job.new_api_definition_id is not None
    _req = required_samples()
    if not fork_done and confirmed_src < _req:
        n_edit = sum(1 for d in diffs if d.get("kind") == "edit")
        n_add = sum(1 for d in diffs if d.get("kind") == "add")
        _need = _req - confirmed_src
        if _req > MIN_SAMPLES_FOR_ITERATION:
            msg = (
                f"已记录 {n_edit} 个修改 + {n_add} 个新增字段。再上传并审视 {_need} 个"
                f"多样化噪声样本即可启动迭代（共需 {_req}：3 锚点 + {_req - MIN_SAMPLES_FOR_ITERATION} 噪声，"
                f"作留出验证；当前 {confirmed_src}/{_req}，共 {total_src} 个样本）。"
            )
        else:
            msg = (
                f"已记录 {n_edit} 个修改 + {n_add} 个新增字段。等待 "
                f"{_need} 个已审视样本以启动反思 agent。"
                f"（当前 {confirmed_src}/{_req}，共 {total_src} 个样本）"
            )
        _update_job(
            db, job,
            status=CustomizeJobStatus.waiting_for_samples.value,
            phase_detail=msg,
        )
        logger.info(
            "Job %s parked in waiting_for_samples PRE-reflection "
            "(source confirmed %d/%d, %d total)",
            job.id, confirmed_src, MIN_SAMPLES_FOR_ITERATION, total_src,
        )
        return

    # ── 「另存为新模板」分支（save_as_new）────────────────────────────────
    # 在克隆上做定制 + 迭代；源 API（含已发布的）保持原样继续服务。
    # 此处源已通过 ≥3 已审视样本的门禁，克隆会带走这些样本的行级副本，
    # 因此克隆的 Phase-3 门禁必然立即通过，不会二次驻留 waiting。
    opts = dict(job.options or {})
    if opts.get("save_as_new"):
        if job.new_api_definition_id and job.new_api_definition_id != job.source_api_definition_id:
            # 重入（如进程重启后 resume）：克隆已存在，直接续用
            existing = db.get(ApiDefinition, job.new_api_definition_id)
            if not existing:
                raise NotFoundError("save-as-new 克隆已被删除，请重新提交定制")
            src_api, src_id = existing, existing.id
        else:
            _update_job(db, job, phase_detail="正在创建新模板（克隆源 API 与样本）")
            clone = _clone_api_for_save_as_new(
                db, src_api,
                new_name=opts.get("new_name"),
                user_id=job.user_id,
            )
            _update_job(db, job,
                        new_api_definition_id=clone.id,
                        new_api_code=clone.api_code)
            src_api, src_id = clone, clone.id

    # ── Defensive source-version pick ─────────────────────────────────────
    # If the source's currently-active version has been mutated down to a
    # near-empty module set (e.g. a previous run hit the legacy meta bug),
    # the customer would inherit that damage on every subsequent fork —
    # a cascading loss of fields. Prefer the version with the MOST modules
    # among all the source's versions; tie-break by latest. Falls back to
    # the active version when no modules exist at all.
    from sqlalchemy import func
    candidates = (
        db.query(OcrPromptVersion, func.count(OcrModule.id).label('mc'))
        .outerjoin(OcrModule, OcrModule.prompt_version_id == OcrPromptVersion.id)
        .filter(OcrPromptVersion.api_definition_id == src_id)
        .group_by(OcrPromptVersion.id)
        .all()
    )
    if not candidates:
        raise ValidationError("Source API has no prompt versions")
    # max modules; tie-break = newest created_at
    candidates.sort(key=lambda x: (-int(x[1] or 0), -(x[0].created_at.timestamp() if x[0].created_at else 0)))
    src_version = candidates[0][0]
    chosen_module_count = int(candidates[0][1] or 0)
    active_version = persistence.get_active_version(db, src_id)
    if active_version and active_version.id != src_version.id:
        logger.warning(
            "Fork using v%s (origin=%s, modules=%d) instead of active v%s — "
            "active had only %d modules (recovery path)",
            src_version.version, src_version.origin, chosen_module_count,
            active_version.version if active_version else "?",
            db.query(func.count(OcrModule.id)).filter(
                OcrModule.prompt_version_id == active_version.id
            ).scalar() if active_version else 0,
        )
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

    # ── Phase 17 follow-up: force cascade-rename on ALL diff renames ────
    # Bug-fix context: cascade_rename_annotations only fires inside
    # commitCurrentDraft (Phase 10) when the customer explicitly clicks
    # "保存到模板（立即生效）". Customers who use "仅暂存" + the top-right
    # "保存并生成 API" button skip that path entirely. Result: source
    # ApiDef's Annotation.field_name stays at the OLD names, but the
    # new version's modules (built from diffs with rename intent) use
    # the NEW names. After iteration, the workspace sees a mismatch —
    # annotations labeled `billFromName` while every
    # ProcessingResult.structured_data emits `salerName` — and renders
    # what looks like "everything reverted to original prompt".
    #
    # Fix: at the start of pipeline execution, apply EVERY rename diff
    # to the source ApiDef's annotation rows. Phase 19 keeps docs on
    # source the whole time, so the rename + structured_data emission
    # line up naturally after this single cascade pass.
    try:
        from app.domain import overlay as _pes_rename
        for d in diffs:
            if d.get("kind") != "edit":
                continue
            on = (d.get("original_name") or "").strip()
            cn = (d.get("corrected_name") or "").strip()
            if (
                on and cn and on != cn
                and "[" not in on and "." not in on
                and "[" not in cn and "." not in cn
            ):
                n = _pes_rename.cascade_rename_annotations(
                    db, src_id, on, cn,
                )
                if n > 0:
                    logger.info(
                        "Pipeline pre-step: cascaded rename %r → %r across "
                        "%d source-ApiDef annotation rows",
                        on, cn, n,
                    )
    except Exception as _exc:  # noqa: BLE001
        logger.warning("Pre-fork cascade rename failed: %s", _exc)

    # ── Phase 11a/b: drop deleted fields BEFORE anything else ────────────
    # User contract: deleted fields have NO meta, NO reflection, NO module
    # slot. We filter them out at three places:
    #   (1) src_modules — so _clone_module never sees them and the fork's
    #       schema/prompt naturally excludes them.
    #   (2) diffs — so the reflection agent isn't invoked on them and they
    #       don't reach _fork_api_definition's add_specs.
    #   (3) modules_by_key — keeps the reflector's "sibling examples"
    #       context clean.
    try:
        from app.domain import overlay as _pes
        overlay_for_delete = _pes.get_overlay(db, src_id)
        deleted_field_names = set(overlay_for_delete.get("deleted_fields") or [])
    except Exception as _exc:  # noqa: BLE001
        logger.warning("Could not read deleted_fields from overlay: %s", _exc)
        deleted_field_names = set()

    if deleted_field_names:
        # Precompute the snake-cased forms of every deleted field name
        # so module_key (snake) and field_name (camel) both match.
        deleted_snake = {_snake(f) for f in deleted_field_names if f}
        all_deleted = deleted_field_names | deleted_snake

        def _is_deleted_diff(d: dict) -> bool:
            mk = (d.get("module_key") or "").strip()
            on = (d.get("original_name") or "").strip()
            cn = (d.get("corrected_name") or "").strip()
            for v in (mk, on, cn):
                if v and (v in all_deleted or _snake(v) in all_deleted):
                    return True
            return False

        before_modules = len(src_modules)
        before_diffs = len(diffs)
        # Filter src_modules — match by module_key against either camel
        # or snake form of any deleted field name.
        deleted_module_keys = {
            m.module_key for m in src_modules
            if m.module_key in all_deleted
        }
        src_modules = [m for m in src_modules if m.module_key not in deleted_module_keys]
        # Filter diffs
        diffs = [d for d in diffs if not _is_deleted_diff(d)]
        # Rebuild modules_by_key after the filter
        modules_by_key = {m.module_key: {
            "module_key": m.module_key,
            "display_name": m.display_name,
            "description": m.description,
            "ocr_prompt": m.ocr_prompt,
            "schema_fragment": m.schema_fragment,
        } for m in src_modules}
        logger.info(
            "Phase 11b — dropped %d deleted fields: modules %d→%d, diffs %d→%d, "
            "deleted_module_keys=%s",
            len(deleted_field_names),
            before_modules, len(src_modules),
            before_diffs, len(diffs),
            sorted(deleted_module_keys),
        )

    # ── Phase 1: reflection ───────────────────────────────────────────────
    _update_job(db, job,
                status=CustomizeJobStatus.reflecting.value,
                phase_detail="正在为每个字段调用反思 agent")
    # Country code (e.g. "MY") drives the per-country reflection agents.
    # Falls back to None for ApiDefs that didn't come from a country template.
    src_country = (src_api.config or {}).get("source_country") or None
    # Phase 14b — build cross-doc context so the agent sees how each
    # edited / added field actually appears on every confirmed sample.
    cross_doc_context = _build_cross_doc_context_for_diffs(db, src_id, diffs)
    # 全文检索语料：每个样本最新一次 OCR 的完整结构化输出。RETARGET 型
    # 修正（客户改了识别内容本身）靠它定位「正确值实际藏在哪个字段下」。
    sample_outputs = _build_sample_outputs(db, src_id)
    from app.processors.factory import ProcessorFactory as _PF
    _refl_proc, _refl_model = _PF.resolve_spec(
        src_api.processor_type, src_api.model_name
    )
    reflections = reflect_on_diffs(
        diffs,
        modules_by_key=modules_by_key,
        processor_spec=_refl_proc,
        model_name=_refl_model,
        country=src_country,
        cross_doc_context=cross_doc_context,
        sample_outputs=sample_outputs,
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

    # ── Phase 2: bump source ApiDef to a new OcrPromptVersion ────────────
    # The legacy "forking" status name is preserved for backwards
    # compatibility with persisted job rows; Phase 19 changed the
    # semantics so this step now mutates source in place instead of
    # creating a new ApiDef with -c1 api_code.
    _update_job(db, job,
                status=CustomizeJobStatus.forking.value,
                phase_detail="客户化版本生成中（在原工作区上 bump 新版本）",
                reflection_summary=reflection_summary)
    new_api, new_version, _new_modules = _fork_api_definition(
        db, src_api=src_api, src_version=src_version, src_modules=src_modules,
        diffs=diffs, reflections=reflections, user_id=job.user_id,
    )
    _update_job(db, job,
                new_api_definition_id=new_api.id,
                new_api_code=new_api.api_code)

    # Phase 23.3 — sweep all docs' cached structured_data so the
    # workspace's JSON view matches the new module key names. Without
    # this, OCR results captured pre-rename keep showing
    # "billFromName" while the new active modules emit "salerCompany".
    try:
        from app.domain import overlay as _pes_sweep
        _post_overlay = _pes_sweep.get_overlay(db, src_api.id)
        _sweep_renames = dict(_post_overlay.get("renames") or {})
        if _sweep_renames:
            n = _rewrite_all_docs_structured_data(db, src_api.id, _sweep_renames)
            logger.info("Phase 23.3 sweep touched %d ProcessingResult rows", n)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("Phase 23.3 structured_data sweep failed: %s", _exc)

    # Phase 19 — no doc rebinding. new_api IS src_api; iteration's
    # api_definition_id == source.id; ProcessingResults land on the
    # source workspace's docs directly. The customer's URL never
    # changes; refreshing the source workspace shows the new prompt's
    # output in-place.

    # Overlay-clear policy (post-Phase-19): the pending_edits overlay on
    # source SURVIVES the customize step. The customer stays on the
    # source workspace URL; clearing here would wipe every visible
    # rename/added/deleted badge. The DELETE /pending-edits endpoint
    # (or workspace "清理变更标识" button) is the explicit cleanup path.
    logger.info("Customize version bumped for ApiDef %s — overlay preserved", src_api.id)

    # ── Phase 3: sample gate ──────────────────────────────────────────────
    # We require at least MIN_SAMPLES_FOR_ITERATION samples whose annotations
    # the customer has confirmed as GT. Raw upload count is NOT sufficient.
    confirmed, total = count_confirmed_samples(db, new_api.id)
    _req = required_samples()
    if confirmed < _req:
        need = _req - confirmed
        noise = (f"（3 锚点 + {_req - MIN_SAMPLES_FOR_ITERATION} 多样化噪声样本作留出验证）"
                 if _req > MIN_SAMPLES_FOR_ITERATION else "")
        msg = (
            f"已生成新模板 {new_api.api_code}。"
            f"当前 {confirmed}/{_req} 已审视样本（共 {total} 个），"
            f"还需 {need} 个样本{noise}经客户确认 OCR 结果正确后才能启动 3 轮迭代。"
        )
        _update_job(db, job,
                    status=CustomizeJobStatus.waiting_for_samples.value,
                    phase_detail=msg)
        logger.info("Job %s parked in waiting_for_samples (%d/%d confirmed, %d total)",
                    job.id, confirmed, MIN_SAMPLES_FOR_ITERATION, total)
        return

    # ── Phase 4: 3-round optimization ─────────────────────────────────────
    _update_job(db, job,
                status=CustomizeJobStatus.optimizing.value,
                phase_detail="迭代优化 · 第 1 轮")
    _run_three_rounds(db, job, new_api)


def _run_three_rounds(db: Session, job: CustomizeJob, new_api: ApiDefinition) -> None:
    """Phase 4: start_optimization + advance_round × 2 + finalize.

    Per design v4 the customer-iteration path runs WITHOUT meta_optimizer
    (the module set is locked at the moment the new customize version is
    minted). Each round only refines the prompts of failing fields; the
    set of modules never changes.
    """
    try:
        run = run_orchestrator.start_optimization(
            db, new_api.id, max_rounds=3, enable_meta=False,
        )
    except ValidationError as exc:
        # Sample gate elsewhere should have prevented this, but guard anyway
        logger.warning("3-round optimization skipped for job %s: %s", job.id, exc)
        _update_job(db, job,
                    status=CustomizeJobStatus.completed.value,
                    phase_detail=f"已生成新模板（{new_api.api_code}），但跳过迭代：{exc}",
                    completed_at=datetime.now(timezone.utc))
        return

    _update_job(
        db, job,
        rounds_done=run.rounds_completed,
        phase_detail=_format_round_phase_detail(db, run, run.rounds_completed),
    )

    # Rounds 2 & 3 — with early stop on 100% accuracy
    best_version_id = _latest_round_version(db, run.id) or None
    for _ in range(2):
        db.refresh(run)
        if run.status != RunStatus.paused_for_review.value:
            break
        if run.current_round_num >= run.max_rounds:
            break
        # ── Early stop ────────────────────────────────────────────────
        # Look at the version we just produced. If the round delivered
        # 100% on all samples, we're done — running more rounds risks
        # regression (meta could over-mutate a perfect prompt).
        last_round = (
            db.query(OcrOptimizationRound)
            .filter(OcrOptimizationRound.run_id == run.id)
            .order_by(OcrOptimizationRound.round_num.desc())
            .first()
        )
        last_acc = (last_round.overall_accuracy if last_round else None)
        if last_acc is not None and last_acc >= 0.999:
            logger.info(
                "early-stop: job %s round %d hit %.2f%% accuracy; skipping further rounds",
                job.id, run.rounds_completed, last_acc * 100,
            )
            _update_job(db, job,
                        phase_detail=f"第 {run.rounds_completed} 轮已达 100% 准确率 · 提前完成")
            break
        try:
            run = run_orchestrator.advance_round(db, run.id, enable_meta=False)
            _update_job(
                db, job,
                rounds_done=run.rounds_completed,
                phase_detail=_format_round_phase_detail(db, run, run.rounds_completed),
            )
        except Exception as exc:
            logger.exception("advance_round failed for job %s: %s", job.id, exc)
            break

    # Monotonic guard (CLAUDE.md §④): activate the best EVALUATED version, so a
    # round's "optimization" can never regress the active prompt below the
    # starting version.
    best_eval_id, best_eval_acc = _best_evaluated_version(db, run.id)

    # Phase-4a「终轮确认评估」: the last round's OUTPUT is un-evaluated, so it's
    # not a candidate above. Confirm it with ONE OCR pass; adopt it only if it's
    # >= the best already-evaluated version (still monotonic, but now a genuine
    # final-round gain isn't thrown away). Graceful: on any failure we keep the
    # best evaluated version.
    latest_id = _latest_round_version(db, run.id)
    if latest_id and latest_id != best_eval_id:
        try:
            latest_acc = _confirm_version_accuracy(db, run, latest_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("final-confirmation eval failed: %s", exc)
            latest_acc = None
        # 批次6：终轮采纳要求**严格优于**已评估最优（含小平局带）——
        # 持平时保留已确认版本，避免噪声驱动的版本切换。
        if latest_acc is not None and latest_acc > best_eval_acc + 0.005:
            logger.info(
                "final-confirmation: latest version acc=%.4f > best_eval=%.4f → activate latest",
                latest_acc, best_eval_acc,
            )
            best_eval_id, best_eval_acc = latest_id, latest_acc

    if best_eval_id:
        logger.info("monotonic finalize: activating version acc=%.4f", best_eval_acc)
        best_version_id = best_eval_id
    else:
        best_version_id = latest_id or best_version_id

    # Finalize → activate the best version
    overall_acc: float | None = None
    finalized = False
    if best_version_id:
        try:
            run_orchestrator.finalize_run(db, run.id, best_version_id)
            finalized = True
            final_v = db.get(OcrPromptVersion, best_version_id)
            if final_v:
                overall_acc = final_v.overall_accuracy
        except Exception as exc:
            logger.exception("finalize_run failed for job %s: %s", job.id, exc)

    # Phase 25 — re-OCR ALL sample docs with the now-active final prompt so
    # every doc's persisted structured_data (and therefore the workspace's
    # JSON / XML / CSV output panel) reflects the SAME final prompt. Without
    # this, only the docs that happened to be extracted last with the final
    # prompt look right; the rest keep stale per-doc output and the JSON panel
    # shows inconsistent field sets/names across samples. See
    # `_reocr_all_docs_with_active_prompt` for the GT-safety rationale.
    if finalized:
        try:
            _update_job(db, job,
                        phase_detail="正在用最终 prompt 刷新所有样本的识别结果...")
            ok, bad = _reocr_all_docs_with_active_prompt(db, new_api.id)
            logger.info(
                "Phase 25 post-finalize re-OCR for job %s: %d ok, %d failed",
                job.id, ok, bad,
            )
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: the new version is still active; the workspace just
            # keeps stale per-doc output until a manual retry. Don't fail the job.
            logger.warning(
                "Phase 25 post-finalize re-OCR sweep failed for job %s: %s",
                job.id, exc,
            )

    _update_job(db, job,
                status=CustomizeJobStatus.completed.value,
                overall_accuracy=overall_acc,
                phase_detail=f"已完成 3 轮迭代，新模板 api_code = {new_api.api_code}",
                completed_at=datetime.now(timezone.utc))


def _format_round_phase_detail(
    db: Session, run: OcrOptimizationRun, round_num: int,
) -> str:
    """N5 — build a customer-friendly phase_detail that surfaces WHICH
    fields the optimizer just refined this round.

    Round-N's per-module work lives in OcrModuleIteration rows tied to
    the latest OcrOptimizationRound. A module that was actually touched
    has at least one of (new_description, new_ocr_suggestions,
    new_ocr_prompt) non-null; pure "passed evaluation, no change"
    iterations are skipped from the banner.

    Output examples:
        "第 1 轮完成 · 本轮优化 8 个字段: invoiceNumber, billFromName, …"
        "第 2 轮完成 · 本轮所有字段评估通过，无 prompt 改写"
    """
    from ..models import OcrModuleIteration, OcrOptimizationRound, OcrModule

    base = f"第 {round_num} 轮（分拆→局部验证→重组）完成"

    try:
        last_round = (
            db.query(OcrOptimizationRound)
            .filter(OcrOptimizationRound.run_id == run.id)
            .order_by(OcrOptimizationRound.round_num.desc())
            .first()
        )
        if not last_round:
            return base

        iters = (
            db.query(OcrModuleIteration)
            .filter(OcrModuleIteration.round_id == last_round.id)
            .all()
        )

        # Collect module_keys whose iteration touched the prompt material
        touched_module_ids: set[uuid.UUID] = set()
        for it in iters:
            changed = (
                (it.new_description is not None and it.new_description != "")
                or (it.new_ocr_suggestions is not None)
                or (it.new_ocr_prompt is not None and it.new_ocr_prompt != "")
            )
            if changed and it.module_id:
                touched_module_ids.add(it.module_id)

        if not touched_module_ids:
            return base + " · 本轮所有字段评估通过，无 prompt 改写"

        modules = (
            db.query(OcrModule)
            .filter(OcrModule.id.in_(touched_module_ids))
            .order_by(OcrModule.order_index)
            .all()
        )
        # Prefer display_name (post-rename camelCase, see Phase 17) over module_key
        names = [
            (m.display_name or m.module_key or "").strip()
            for m in modules
        ]
        names = [n for n in names if n]
        n = len(names)
        if n == 0:
            return base
        # Cap at 6 names + suffix to keep the banner concise
        head = ", ".join(names[:6])
        tail = "" if n <= 6 else f"…（共 {n} 个）"
        return f"第 {round_num} 轮完成 · 本轮优化 {n} 个字段: {head}{tail}"
    except Exception as _exc:  # noqa: BLE001
        logger.warning("Could not format round-%d phase_detail: %s", round_num, _exc)
        return base


# ── Version selection / 单调守护 ─────────────────────────────────────────────
# customer_iteration 拆分第一刀（结构审查 1.1）：红线④「准确率永不下降」的
# 实现移至 version_selection.py（可单测打靶）。这里保留 facade 重导出，
# 管线内部与既有测试（test_monotonic_finalize / test_stability_guards /
# test_eval_validity）零改动。
from .version_selection import (  # noqa: E402,F401 — facade re-export
    _best_evaluated_version,
    _confirm_version_accuracy,
    _latest_round_version,
    _tie_band,
)


# ── Forking / 模块克隆 / 新增字段 ────────────────────────────────────────────
# customer_iteration 拆分第四刀（结构审查 1.1）：最大的一块职责（约 770 行）
# 移至 customize_fork.py。facade 重导出，_execute_pipeline 与既有测试
# （test_overlay_first_fork / test_pending_edits / test_reconciler /
# test_reflection_landing）零改动。
from .customize_fork import (  # noqa: E402,F401 — facade re-export
    _clone_module,
    _extract_removed_rules,
    _fork_api_definition,
    _llm_expand_new_field,
    _module_from_add_diff,
    _snake,
    _to_snake,
)
