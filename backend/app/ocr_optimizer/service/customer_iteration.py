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
    OcrOptimizationRound,
    OcrOptimizationRun,
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
    return _job_to_dict(job)


def _update_job(db: Session, job: CustomizeJob, **kwargs) -> None:
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)


def _build_cross_doc_context_for_diffs(
    db: Session,
    api_def_id: uuid.UUID,
    diffs: list[dict],
) -> dict[str, list[dict]]:
    """Phase 14b — collect each diff's field-name across every confirmed
    sample of the ApiDef.

    For each diff (edit/add/rename), produce a list of {doc_id, doc_filename,
    value, is_corrected, bbox} for every annotation matching either the
    original_name or corrected_name. The reflection agent uses this to
    compare values/formatting across the 3 invoices.
    """
    from app.models.api_definition import ApiDefinition as _ApiDef
    from app.models.annotation import Annotation as _Annotation
    from app.models.document import Document as _Document

    api_def = db.get(_ApiDef, api_def_id)
    if not api_def:
        return {}
    sample_ids = (api_def.config or {}).get("sample_document_ids") or []
    if not sample_ids:
        return {}

    # Collect the field names we care about (both old and new forms)
    field_names: set[str] = set()
    for d in diffs:
        for k in ("original_name", "corrected_name"):
            v = (d.get(k) or "").strip()
            if v and "[" not in v and "." not in v:
                field_names.add(v)
    if not field_names:
        return {}

    # Resolve docs once
    doc_uuids: list[uuid.UUID] = []
    for s in sample_ids:
        try:
            doc_uuids.append(uuid.UUID(str(s)))
        except Exception:  # noqa: BLE001
            continue
    if not doc_uuids:
        return {}

    docs = db.query(_Document).filter(_Document.id.in_(doc_uuids)).all()
    doc_by_id = {d.id: d for d in docs}

    out: dict[str, list[dict]] = {f: [] for f in field_names}
    rows = (
        db.query(_Annotation)
        .filter(
            _Annotation.document_id.in_(doc_uuids),
            _Annotation.field_name.in_(field_names),
        )
        .all()
    )
    for ann in rows:
        doc = doc_by_id.get(ann.document_id)
        out[ann.field_name].append({
            "doc_id": str(ann.document_id),
            "doc_filename": (doc.filename if doc else None) or str(ann.document_id),
            "value": ann.field_value,
            "is_corrected": bool(ann.is_corrected),
            "bbox": ann.bounding_box,
        })

    # Phase 15 — dedup duplicate values within each field's sample list.
    # If two docs show the same (value, is_corrected) tuple, we collapse
    # them and annotate "× N docs" so the LLM doesn't see the same data
    # point repeated. Preserves the first occurrence's doc_filename + bbox.
    deduped: dict[str, list[dict]] = {}
    for field, samples in out.items():
        if not samples:
            continue
        # Key by (value, is_corrected) — bbox usually varies across docs
        # even when value is identical, so we keep the first one seen.
        by_key: dict[tuple, dict] = {}
        for s in samples:
            key = (
                # repr handles None / strings / numbers consistently
                repr(s.get("value")),
                bool(s.get("is_corrected")),
            )
            if key in by_key:
                by_key[key]["dup_count"] = by_key[key].get("dup_count", 1) + 1
                # accumulate doc filenames for transparency
                other_files = by_key[key].setdefault("dup_doc_filenames", [])
                other_files.append(s.get("doc_filename"))
            else:
                by_key[key] = dict(s)
        deduped[field] = list(by_key.values())
    return deduped


# (C4 cleanup) — _mirror_source_samples_to_fork removed.
# Phase 19 collapsed the fork ApiDef onto source, so there's no longer a
# second ApiDef to mirror docs to. Every call site was either
# - the post-fork block in _execute_pipeline (removed in Phase 19), or
# - the auto-resume branch in maybe_auto_resume_for_api (removed in C5).


def _rewrite_all_docs_structured_data(
    db: Session,
    api_def_id: uuid.UUID,
    renames: dict[str, str],
) -> int:
    """Phase 23.3 post-customize sweep.

    For every ProcessingResult on every Document bound to api_def_id,
    rewrite top-level structured_data keys per the renames map. Used
    immediately after _fork_api_definition so the workspace's cached
    OCR outputs match the new module key names — eliminating the
    "JSON shows old name / module list shows new name" drift.

    Returns the number of ProcessingResult rows touched.
    """
    if not renames:
        return 0
    from app.models.document import Document as _Document, ProcessingResult as _PR
    from app.services.document_service import _rewrite_structured_data_keys

    doc_ids = [d.id for d in db.query(_Document.id)
               .filter(_Document.api_definition_id == api_def_id).all()]
    if not doc_ids:
        return 0

    rows = db.query(_PR).filter(_PR.document_id.in_(doc_ids)).all()
    touched = 0
    for pr in rows:
        if not pr.structured_data:
            continue
        new_sd = _rewrite_structured_data_keys(pr.structured_data, renames)
        if new_sd != pr.structured_data:
            pr.structured_data = new_sd
            touched += 1
    db.commit()
    logger.info(
        "Phase 23.3: rewrote structured_data on %d ProcessingResult rows "
        "across %d docs of ApiDef %s (renames=%d)",
        touched, len(doc_ids), api_def_id, len(renames),
    )
    return touched


def _reocr_all_docs_with_active_prompt(
    db: Session,
    api_def_id: uuid.UUID,
) -> tuple[int, int]:
    """Phase 25 — after the 3-round iteration finalizes (the final prompt is
    now the ACTIVE OcrPromptVersion), re-run OCR on every sample document of
    the ApiDef so each doc's persisted ProcessingResult.structured_data
    reflects the SAME final prompt.

    Why this is needed
    ------------------
    During the 3 rounds, `run_orchestrator._run_one_round` OCRs the samples
    only to *evaluate* accuracy — the output lives on
    OcrOptimizationRound.ocr_raw_outputs and is never written back to the
    documents. `finalize_run` only flips the active-version pointer. So each
    doc's structured_data still reflects whatever prompt last extracted IT
    (initial upload, the augmented new-sample upload, a manual retry) — which
    differs per doc. The workspace's middle field column papers over this with
    a client-side rename overlay, but the JSON output panel reads the raw
    per-doc structured_data and exposes the drift: one file shows 38 fields
    with `billFromName`, another shows 14 fields with `salerName`, etc.

    Phase 23.3's `_rewrite_all_docs_structured_data` only renames TOP-LEVEL
    keys — it can't reconcile genuinely different field SETS / nesting / new
    fields. A real re-extraction with the unified final prompt is the only
    fix that makes every doc's JSON output consistent.

    GT safety (CLAUDE.md invariants)
    --------------------------------
    `reprocess_document` creates a NEW ProcessingResult version with fresh
    `ai_detected` annotations (is_corrected=False) and does NOT delete prior
    annotations. `ground_truth.build` reads is_corrected/manual annotations
    across ALL versions, so the customer's confirmed GT survives untouched
    and no OCR output is auto-promoted to GT.

    Graceful degradation: a per-doc OCR failure marks that doc failed and is
    skipped; the job still completes. Returns (succeeded, failed).
    """
    from app.models.document import Document, DocumentStatus
    from app.schemas.document import ReprocessRequest
    from app.services.document_service import reprocess_document

    docs = (
        db.query(Document)
        .filter(Document.api_definition_id == api_def_id)
        .all()
    )
    succeeded = failed = 0
    for doc in docs:
        if not doc.storage_path:
            continue
        try:
            # prompt=None → reprocess_document resolves the ApiDef's ACTIVE
            # composed_prompt, which is the just-finalized final version.
            reprocess_document(db, doc.id, ReprocessRequest(prompt=None))
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Phase 25 re-OCR failed for doc=%s of ApiDef %s — %s",
                doc.id, api_def_id, exc,
            )
            # _run_extraction set status=failed + re-raised without commit;
            # roll back its partial txn, then persist the failure marker.
            db.rollback()
            d = db.get(Document, doc.id)
            if d:
                d.status = DocumentStatus.failed
                d.error_message = (str(exc) or "post-iteration re-OCR failed")[:1024]
                db.commit()
            failed += 1
    logger.info(
        "Phase 25: re-OCR'd %d/%d docs of ApiDef %s with final active prompt "
        "(%d failed)",
        succeeded, succeeded + failed, api_def_id, failed,
    )
    return succeeded, failed


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
    from app.models.api_definition import ApiDefinition as _ApiDef
    from app.ocr_optimizer.service.ground_truth import has_ground_truth

    api = db.get(_ApiDef, api_definition_id)
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
        if confirmed < MIN_SAMPLES_FOR_ITERATION:
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
    from app.models.api_definition import ApiDefinition
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
            if confirmed < MIN_SAMPLES_FOR_ITERATION:
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
        if confirmed < MIN_SAMPLES_FOR_ITERATION:
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
    if not fork_done and confirmed_src < MIN_SAMPLES_FOR_ITERATION:
        n_edit = sum(1 for d in diffs if d.get("kind") == "edit")
        n_add = sum(1 for d in diffs if d.get("kind") == "add")
        msg = (
            f"已记录 {n_edit} 个修改 + {n_add} 个新增字段。等待 "
            f"{MIN_SAMPLES_FOR_ITERATION - confirmed_src} 个已审视样本以启动反思 agent。"
            f"（当前 {confirmed_src}/{MIN_SAMPLES_FOR_ITERATION}，共 {total_src} 个样本）"
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
        from app.services import pending_edits_service as _pes_rename
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
        from app.services import pending_edits_service as _pes
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
        from app.services import pending_edits_service as _pes_sweep
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
    if confirmed < MIN_SAMPLES_FOR_ITERATION:
        need = MIN_SAMPLES_FOR_ITERATION - confirmed
        msg = (
            f"已生成新模板 {new_api.api_code}。"
            f"当前 {confirmed}/{MIN_SAMPLES_FOR_ITERATION} 已审视样本（共 {total} 个），"
            f"还需 {need} 个样本经客户确认 OCR 结果正确后才能启动 3 轮迭代。"
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
        if latest_acc is not None and latest_acc >= best_eval_acc:
            logger.info(
                "final-confirmation: latest version acc=%.4f >= best_eval=%.4f → activate latest",
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


def _latest_round_version(db: Session, run_id: uuid.UUID) -> uuid.UUID | None:
    from ..models import OcrOptimizationRound
    r = (
        db.query(OcrOptimizationRound)
        .filter(OcrOptimizationRound.run_id == run_id)
        .order_by(OcrOptimizationRound.round_num.desc())
        .first()
    )
    return r.next_version_id if r and r.next_version_id else None


def _confirm_version_accuracy(db: Session, run, version_id: uuid.UUID) -> float | None:
    """Phase-4a「终轮确认评估」: OCR the run's confirmed samples with `version_id`'s
    composed_prompt and return its overall accuracy (fuzzy, same scoring path as
    the rounds). Lets the monotonic guard ALSO consider the last round's
    un-evaluated output, so a genuine final-round gain isn't discarded.

    Costs ONE OCR pass. Read-only (no version/round writes). Returns None on any
    failure (caller then keeps the best already-evaluated version → still
    monotonic). Builds on align_for_path (real accuracy).
    """
    from app.models.api_definition import ApiDefinition
    from . import ground_truth
    from .ocr_runner import run_ocr_on_samples
    from ..eval.harness import module_specs_from_orm, score_outputs

    version = db.get(OcrPromptVersion, version_id)
    if not version or not version.composed_prompt:
        return None
    api_def = db.get(ApiDefinition, run.api_definition_id)
    if not api_def:
        return None
    modules = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == version_id)
        .order_by(OcrModule.order_index)
        .all()
    )
    if not modules:
        return None

    # Confirmed samples + GT (same gate as the run).
    raw = run.sample_document_ids or []
    sample_ids: list[uuid.UUID] = []
    gts: dict[str, dict] = {}
    for sid in raw:
        try:
            suid = uuid.UUID(str(sid))
        except (ValueError, TypeError):
            continue
        gt = ground_truth.build(db, suid)
        if gt:
            sample_ids.append(suid)
            gts[str(suid)] = gt
    if not sample_ids:
        return None

    from app.processors.factory import ProcessorFactory as _PF
    _conf_proc, _conf_model = _PF.resolve_spec(
        api_def.processor_type, api_def.model_name
    )
    outputs = run_ocr_on_samples(
        db,
        sample_document_ids=sample_ids,
        composed_prompt=version.composed_prompt,
        composed_schema=version.composed_schema,
        processor_spec=_conf_proc,
        model_name=_conf_model,
    )
    report = score_outputs(module_specs_from_orm(modules), outputs, gts)
    return report.overall_accuracy


def _best_evaluated_version(db: Session, run_id: uuid.UUID) -> tuple[uuid.UUID | None, float]:
    """Monotonic guard (CLAUDE.md §④): pick the version with the HIGHEST
    *evaluated* accuracy across the run, so finalize never activates a version
    we haven't confirmed.

    Each round records `overall_accuracy` for the version it EVALUATED at entry
    (`round.prompt_version_id` = that round's INPUT version): round 1 → starting
    version, round N → v(N-1). So the map covers {starting … v(N-1)} — every
    version EXCEPT the last round's un-evaluated output. Returning the argmax
    guarantees the activated version's accuracy >= the starting version's
    (round-over-round non-decrease), at ZERO extra OCR cost.

    Tradeoff (intentional, safe): the very last round's output is un-evaluated,
    so it is NOT a candidate — we never activate an unconfirmed version. (To
    also capture a final-round gain, a one-pass confirmation eval could be
    added later.)
    """
    from ..models import OcrOptimizationRound
    rounds = (
        db.query(OcrOptimizationRound)
        .filter(OcrOptimizationRound.run_id == run_id)
        .all()
    )
    best_id: uuid.UUID | None = None
    best_acc = -1.0
    for r in rounds:
        acc = r.overall_accuracy if r.overall_accuracy is not None else -1.0
        if r.prompt_version_id and acc > best_acc:
            best_acc, best_id = acc, r.prompt_version_id
    return best_id, best_acc


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
    """Bump source ApiDef to a new OcrPromptVersion that reflects the
    customer's edits (diffs) + reflection-agent fix suggestions.

    Function name retained for backwards compatibility / call-site
    stability; semantics changed in Phase 19. There is NO fork in the
    repo-clone sense any more — this writes new rows on the SOURCE
    ApiDef and flips its active version pointer. See `Phase 19` notes
    below + the CLAUDE.md mental-model diagram.

    Previous design created a separate ApiDef with a `-c1` api_code, which
    forced the customer to navigate to a different /workspace/api/<id>
    URL to see the iteration results. Repeated UX feedback: that breaks
    "step-by-step in one workspace" — the customer's workflow should stay
    on the source URL forever.

    New design:
      - NO new ApiDefinition row
      - NEW OcrPromptVersion on the SAME source ApiDef (next int version)
      - NEW OcrModule rows on the new version
      - source.prompt_version_id flips to point at the new version
      - source.api_code is unchanged (caller integrations don't break)
      - Iteration runs on source's docs (no document rebinding)
      - Job.new_api_definition_id == source.id (we return src_api here)

    Audit trail is preserved via the OcrPromptVersion chain
    (parent_version_id), and the prior version row stays in DB with
    status='archived'.
    """
    # Mark the prior active version as archived so the new one becomes
    # the unambiguous active version. (Mirrors _deactivate_others.)
    db.query(OcrPromptVersion).filter(
        OcrPromptVersion.api_definition_id == src_api.id,
        OcrPromptVersion.status == PromptVersionStatus.active.value,
    ).update({"status": PromptVersionStatus.archived.value})

    # Compute next integer version number on source
    used_ints: set[int] = set()
    for (label,) in (
        db.query(OcrPromptVersion.version)
        .filter(OcrPromptVersion.api_definition_id == src_api.id)
        .all()
    ):
        if label is None:
            continue
        s = str(label)
        if "." in s:
            continue
        try:
            used_ints.add(int(s))
        except ValueError:
            continue
    next_version_num = 1
    while next_version_num in used_ints:
        next_version_num += 1

    new_version = OcrPromptVersion(
        id=uuid.uuid4(),
        api_definition_id=src_api.id,
        version=str(next_version_num),
        parent_version_id=src_version.id,
        status=PromptVersionStatus.active.value,
        origin=VersionOrigin.manual_edit.value,
        composed_prompt="",
        composed_schema=None,
        # Country-wide rules are version-level and DON'T change on customize.
        # They stay the same all the way through the 3 rounds.
        country_global_text=src_version.country_global_text,
        notes=f"customer customize: v{src_version.version} → v{next_version_num} ({len(diffs)} diffs)",
        activated_at=datetime.now(timezone.utc),
    )

    # `new_api` returned to caller is the SAME src_api now pointing at the
    # new active version. Keep the variable name to minimize call-site
    # churn — every downstream reference to `new_api.id` still works
    # because new_api.id == src_api.id.
    new_api = src_api

    # Build a quick lookup of source module_keys so we can detect
    # "orphan edit diffs" — edits whose module_key has no matching source
    # module. When such an orphan carries a rename (corrected_name ≠
    # original_name), it means the customer renamed a field that the
    # source template never actually defined (e.g. MY template has
    # billFromComposite but the user renamed billFromAddress → salerAddress;
    # the LLM hallucinated billFromAddress in OCR output, and the customer
    # treated it as if it existed).
    # Promote those orphans to add diffs so a real new module is created.
    src_module_keys = {m.module_key for m in src_modules}

    # Multiple diffs may share the same module_key — e.g. several array-cell
    # corrections on `detailOfGoodsOrServices[0..N].field` all route to module
    # `detail_of_goods_or_services`. We accumulate their prompt suffixes so no
    # correction gets dropped; description and schema_type take last-wins.
    #
    # add_specs entries are (diff, reflection_key) tuples. The reflection_key
    # is computed at append time to MIRROR the reflector's keying
    # (reflector.py line 75: `diff.get("module_key") or f"_new_{idx}"`)
    # — this avoids the fragile `diffs.index(d)` lookup later, which broke
    # for Phase-7 promoted dicts that aren't members of the original list.
    edits_by_key: dict[str, dict] = {}
    add_specs: list[tuple[dict, str | None]] = []

    # ── Phase 23.2 defense-in-depth: APPLY overlay.deleted_fields to
    # src_modules right here, in addition to the same filter in
    # _execute_pipeline. This makes _fork_api_definition robust when
    # invoked directly (tests, future codepaths) and guarantees deleted
    # fields never sneak into the new version.
    try:
        from app.services import pending_edits_service as _pes_del
        _fork_deleted = set(
            (_pes_del.get_overlay(db, src_api.id).get("deleted_fields") or [])
        )
    except Exception:  # noqa: BLE001
        _fork_deleted = set()
    if _fork_deleted:
        _fork_deleted_snake = {_snake(f) for f in _fork_deleted if f}
        _fork_deleted_all = _fork_deleted | _fork_deleted_snake
        src_modules = [
            m for m in src_modules
            if (m.module_key not in _fork_deleted_all)
            and ((m.json_path or "").split(".")[-1]
                 .replace("[*]", "").replace("[", "").replace("]", "").strip()
                 not in _fork_deleted_all)
        ]

    # ── Phase 23.2: SEED edits_by_key + add_specs from pending_edits ────
    # The overlay is the single source of truth for the customer's
    # intended field set (renames / adds / deletes). Diffs are still
    # consumed below for reflection inputs + value examples, but the
    # MODULE STRUCTURE is now driven entirely by the overlay — so a
    # rename committed via "保存到模板（立即生效）" lands in the new
    # version's modules even when the customize-submit codepath sent
    # a diff with corrected_name == original_name (or no diff at all).
    try:
        from app.services import pending_edits_service as _pes_fork
        _fork_overlay = _pes_fork.get_overlay(db, src_api.id)
    except Exception:  # noqa: BLE001
        _fork_overlay = {}
    _overlay_renames: dict[str, str] = dict(_fork_overlay.get("renames") or {})
    _overlay_added: list[dict] = list(_fork_overlay.get("added_fields") or [])

    # (a) Seed RENAMES: for each {oldName: newName} in overlay, find the
    # source module whose json_path leaf matches oldName and stash a
    # rename patch on its module_key.
    if _overlay_renames:
        for src_m in src_modules:
            jp = src_m.json_path or ""
            leaf = jp.split(".")[-1].replace("[*]", "").replace("[", "").replace("]", "").strip()
            if not leaf:
                continue
            new_name = _overlay_renames.get(leaf)
            if not new_name or new_name == leaf:
                continue
            existing = edits_by_key.get(src_m.module_key, {})
            existing.setdefault("__rename_old", leaf)
            existing.setdefault("__rename_new", new_name)
            edits_by_key[src_m.module_key] = existing
            logger.info(
                "Phase 23.2: overlay seeded rename %r → %r on module %s",
                leaf, new_name, src_m.module_key,
            )

    # (b) Seed ADDS: for each overlay.added_fields entry not already
    # represented as a real source module, synthesize an add spec.
    src_leafs = {
        (m.json_path or "").split(".")[-1]
        .replace("[*]", "").replace("[", "").replace("]", "").strip()
        for m in src_modules
    }
    # Also exclude renames' new-names (they'll exist after rename)
    src_leafs |= set(_overlay_renames.values())
    _added_already_in_specs: set[str] = set()
    for f in _overlay_added:
        name = (f or {}).get("field_name") or ""
        if not name or name in src_leafs:
            continue
        # Build a synth diff in the same shape genuine kind=add diffs use
        synth = {
            "kind": "add",
            "module_key": _snake(name),
            "original_name": name,
            "corrected_name": name,
            "corrected_format": (f.get("type") or "string"),
        }
        # Reflection for adds is keyed by module_key in the reflector
        add_specs.append((synth, synth["module_key"]))
        _added_already_in_specs.add(name)
        logger.info(
            "Phase 23.2: overlay seeded add %r (module_key=%s)",
            name, synth["module_key"],
        )

    for orig_idx, d in enumerate(diffs):
        if d.get("kind") == "edit":
            mk = d.get("module_key")
            if not mk:
                continue
            # Phase 7 fix: orphan edit diff → promote to add diff
            if mk not in src_module_keys:
                on = (d.get("original_name") or "").strip()
                cn = (d.get("corrected_name") or "").strip()
                # Synthesize an add diff using corrected_name as the new
                # field's identity. The corrected_value (if any) becomes
                # the customer's example value for LLM expansion.
                synth = dict(d)
                synth["kind"] = "add"
                synth["corrected_name"] = cn or on or mk
                if not synth.get("corrected_format"):
                    synth["corrected_format"] = "string"
                logger.info(
                    "Promoting orphan edit diff to add: module_key=%s "
                    "(original_name=%r → corrected_name=%r)",
                    mk, on, cn,
                )
                # Reflector saw this as kind=edit and keyed reflection by
                # original module_key — reuse it.
                add_specs.append((synth, mk))
                continue
            existing = edits_by_key.get(mk, {})
            r = reflections.get(mk)
            # Description: first non-empty reflection patch wins
            if r and r.description_patch and not existing.get("description"):
                existing["description"] = r.description_patch
            # Schema type: last non-empty wins
            if d.get("corrected_format") and d.get("corrected_format") != d.get("original_format"):
                existing["__schema_type"] = d["corrected_format"]
            # Rename: when corrected_name differs from original_name, propagate
            # to module_key + json_path so the fork's composed_schema emits the
            # new key. The diff's original_name may be a dotted path (e.g.
            # "detailOfGoodsOrServices[0].quantity") — only treat top-level
            # scalar renames here (no bracket / no dot).
            old_name = (d.get("original_name") or "").strip()
            new_name = (d.get("corrected_name") or "").strip()
            if (
                old_name and new_name and old_name != new_name
                and "[" not in old_name and "." not in old_name
                and "[" not in new_name and "." not in new_name
            ):
                existing["__rename_old"] = old_name
                existing["__rename_new"] = new_name
            # Suffix: accumulate per-cell hints + reflection fix_suggestions
            suffix_parts: list[str] = []
            if r:
                suffix_parts.extend(s for s in r.fix_suggestions if s)
            field_label = d.get("original_name") or d.get("corrected_name") or ""
            corrected_value = d.get("corrected_value")
            if corrected_value:
                if field_label and "[" in field_label:
                    suffix_parts.append(
                        f"客户在样本上修正 `{field_label}` 的值为：{corrected_value}"
                    )
                else:
                    suffix_parts.append(
                        f"客户在样本中提供的正确值示例：{corrected_value}"
                    )
            if suffix_parts:
                merged = "\n".join(suffix_parts)
                existing["__prompt_suffix"] = (
                    (existing.get("__prompt_suffix", "") + ("\n" if existing.get("__prompt_suffix") else "") + merged).strip()
                )
            if existing:
                edits_by_key[mk] = existing
        elif d.get("kind") == "add":
            # Genuine add — reflector keyed by module_key if present, else _new_{idx}
            rk = d.get("module_key") or f"_new_{orig_idx}"
            # Phase 23.2 dedup: if overlay already seeded this name as an add,
            # skip — overlay version wins (it has the customer's most recent
            # description / type).
            on = (d.get("original_name") or "").strip()
            cn = (d.get("corrected_name") or "").strip()
            if (on in _added_already_in_specs) or (cn in _added_already_in_specs):
                continue
            add_specs.append((d, rk))

    from . import reconciler as _reconciler

    new_modules: list[OcrModule] = []
    for m in src_modules:
        patch = edits_by_key.get(m.module_key, {})
        # Phase 8: attach the reflection result for this source module so
        # _clone_module can record it into ocr_suggestions["reflections"].
        r = reflections.get(m.module_key)
        if r is not None:
            patch = dict(patch)  # don't mutate the shared dict
            patch["__reflection"] = r
            # Phase 4 — cross-round contradiction reconciliation (req 5):
            # when this field's prompt ALREADY carries accumulated feedback,
            # a fresh suggestion can contradict an earlier one. Instead of
            # blindly appending (which stacks contradictions), collapse them
            # into ONE coherent prompt that prioritizes the latest intent.
            # Only fires on already-accumulated prompts (bounds LLM cost);
            # fail-open → _clone_module falls back to blind-append.
            new_sugs = list(getattr(r, "fix_suggestions", []) or [])
            if new_sugs and _reconciler.has_accumulated_feedback(m.ocr_prompt):
                from app.processors.factory import ProcessorFactory as _PF_rec
                _rec_proc, _rec_model = _PF_rec.resolve_spec(
                    src_api.processor_type, src_api.model_name
                )
                coherent = _reconciler.reconcile_module_prompt(
                    module_key=m.module_key,
                    display_name=m.display_name,
                    current_prompt=m.ocr_prompt or "",
                    new_suggestions=new_sugs,
                    field_rule=getattr(r, "field_rule", None),
                    latest_intent=getattr(r, "diff", None) or {},
                    processor_spec=_rec_proc,
                    model_name=_rec_model,
                )
                if coherent:
                    patch["__reconciled_prompt"] = coherent
        new_modules.append(_clone_module(m, new_version_id=new_version.id, patch=patch))

    order_start = max((m.order_index for m in new_modules), default=-1) + 1
    # Build a compact sibling-example block once, reuse across all add diffs.
    # Helps the LLM align style with the rest of the template.
    sibling_examples = "\n".join(
        f"- {m.module_key} ({m.display_name or ''}): {(m.description or '')[:80]}"
        for m in src_modules[:5]
    )
    from app.processors.factory import ProcessorFactory as _PF_add
    _add_proc, _add_model = _PF_add.resolve_spec(
        src_api.processor_type, src_api.model_name
    )
    for i, (d, reflection_key) in enumerate(add_specs):
        rout = reflections.get(reflection_key) if reflection_key else None
        new_modules.append(_module_from_add_diff(
            d, new_version_id=new_version.id, order_index=order_start + i,
            reflection_outputs=rout,
            sibling_examples=sibling_examples,
            processor_spec=_add_proc,
            model_name=_add_model,
        ))

    try:
        new_version.composed_schema = composer.assemble_schema(new_modules)
        new_version.composed_prompt = composer.assemble_prompt(
            new_modules,
            country_global=new_version.country_global_text,
        )
    except ValueError as exc:
        raise ValidationError(f"Compose failed for forked version: {exc}") from exc

    db.add(new_version)
    for m in new_modules:
        db.add(m)

    # Phase 19 — source ApiDef's active version now points to the new
    # customize version. status stays whatever it was (don't downgrade
    # an active ApiDef to draft just because we created a new prompt
    # version on it).
    new_api.prompt_version_id = new_version.id
    db.commit()

    # NOTE (design v3): inherited sample annotations are NOT auto-GT'd.
    # The customer must explicitly confirm each sample's OCR output as
    # ground truth via the confirm-gt endpoint. This avoids feeding the
    # 3-round optimizer self-referential "OCR = GT" signal.

    db.refresh(new_api)
    db.refresh(new_version)
    return new_api, new_version, new_modules


def _clone_module(src: OcrModule, *, new_version_id: uuid.UUID, patch: dict) -> OcrModule:
    new_description = patch.get("description") or src.description
    # Phase 4 — if the upstream reconciler produced a coherent (de-contradicted)
    # prompt, use it as-is (latest intent already won); otherwise fall back to
    # the §⑤.3 blind-append of this round's suggestions.
    reconciled = patch.get("__reconciled_prompt")
    if reconciled:
        new_prompt = reconciled
    else:
        new_prompt = src.ocr_prompt
        suffix = patch.get("__prompt_suffix")
        if suffix:
            new_prompt = (src.ocr_prompt or "").rstrip() + "\n\n# 客户反馈补充\n" + suffix.strip()

    # Phase 8 — copy source ocr_suggestions and append reflection entry
    # (when there was a customer edit on this module). Unchanged modules
    # keep the source suggestions verbatim; ocr_suggestions["reflections"]
    # only gets a non-empty list for fields the customer actually touched.
    new_suggestions = copy.deepcopy(src.ocr_suggestions or {})
    if not isinstance(new_suggestions, dict):
        new_suggestions = {}
    reflection = patch.get("__reflection")
    if reflection is not None:
        diff = getattr(reflection, "diff", None) or {}
        on = (diff.get("original_name") or "").strip()
        cn = (diff.get("corrected_name") or "").strip()
        ov = diff.get("original_value")
        cv = diff.get("corrected_value")
        entry = {
            "round": 0,
            "kind": getattr(reflection, "kind", diff.get("kind", "edit")),
            "rationale": getattr(reflection, "rationale_summary", "") or "",
            "fix_suggestions": list(getattr(reflection, "fix_suggestions", []) or []),
            "original_name": on,
            "corrected_name": cn or on,
            "renamed": bool(on and cn and on != cn),
            "original_value": ov,
            "corrected_value": cv,
            # Derived: rules in the original prompt that the reflection
            # implies should be REMOVED (e.g. "保留字母前缀" when the
            # customer renamed PO and the fix says "去除前缀"). Best-effort
            # heuristic — looks for "当前提示词" + "/" + "客户" phrasing in
            # the rationale to surface a recommendation.
            "removed_rules": _extract_removed_rules(
                rationale=getattr(reflection, "rationale_summary", "") or "",
                src_prompt=src.ocr_prompt or "",
            ),
        }
        reflections_log = list(new_suggestions.get("reflections") or [])
        reflections_log.append(entry)
        new_suggestions["reflections"] = reflections_log

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

    # Rename propagation (design v8 §3.9):
    # When the diff carried a top-level scalar rename (corrected_name ≠
    # original_name), rewrite module_key + json_path leaf so the fork's
    # assemble_schema emits the new key. The renamed_from_key is recorded
    # in the prompt suffix so the LLM still knows what semantic field to
    # extract on the page.
    new_module_key = src.module_key
    new_json_path = src.json_path
    # Phase 17 — keep optimizer's display label in sync with the
    # workspace field column. When the customer renames a field, the
    # frontend field column shows the NEW camelCase name (cascade
    # rename in Annotation.field_name). To match, the optimizer's
    # display_name becomes the new name verbatim — same format as the
    # ADD path (_module_from_add_diff sets display_name=new_name). The
    # original Chinese semantic label is preserved in `description`,
    # not lost.
    new_display_name = src.display_name
    new_description_value = new_description
    rename_old = patch.get("__rename_old")
    rename_new = patch.get("__rename_new")
    if rename_old and rename_new and rename_old != rename_new:
        new_module_key = _snake(rename_new)
        # Replace the leaf segment in json_path:
        #   $[*].billFromName  →  $[*].supplierName
        #   $.billFromName     →  $.supplierName
        if src.json_path and rename_old in src.json_path:
            new_json_path = src.json_path.replace(rename_old, rename_new)
        # Display the new field name — matches what the workspace
        # column shows + matches the ADD path's format.
        new_display_name = rename_new
        # Preserve the source's Chinese semantic anchor in description
        # so audit / future LLM passes still know what this field is.
        # Format: "<orig display> (重命名自 <old>)" prepended when not
        # already present in description.
        prefix = f"{src.display_name}（重命名自 {rename_old}）"
        if src.display_name and prefix not in (new_description or ""):
            new_description_value = (
                prefix + "。"
                + (new_description.lstrip() if new_description else "")
            ).rstrip()
        # Append rename hint to the prompt so the LLM gets explicit mapping
        rename_hint = (
            f"\n\n# 字段重命名（Part 3 §3.9）\n"
            f"该字段原命名为 `{rename_old}`，现已重命名为 `{rename_new}`。\n"
            f"请在票面上按 `{rename_old}` 的语义/位置/格式识别，但输出 JSON 时\n"
            f"key 必须使用新命名 `{rename_new}`，不要输出旧名 `{rename_old}`。"
        )
        new_prompt = (new_prompt or "").rstrip() + rename_hint

    return OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=new_version_id,
        module_key=new_module_key,
        display_name=new_display_name,
        description=new_description_value,
        json_path=new_json_path,
        schema_fragment=new_schema,
        ocr_suggestions=new_suggestions,  # Phase 8: includes reflections log
        ocr_prompt=new_prompt,
        skill_ids=list(src.skill_ids or []),
        order_index=src.order_index,
        status=src.status,
        module_accuracy=None,
    )


def _extract_removed_rules(*, rationale: str, src_prompt: str) -> list[str]:
    """Best-effort surfacer: when the reflection rationale points out that
    the SOURCE prompt has an explicit instruction that contradicts the
    customer's correction (typical phrasing: "当前提示词…指示…/要求…"），
    pluck the offending clauses so downstream tooling (or a future Part 3
    optimizer pass) can decide to delete them from the new prompt.

    Returns an empty list when no obvious "remove this rule" hint is found.
    """
    if not rationale or not src_prompt:
        return []
    out: list[str] = []
    # Look for quoted chunks in rationale ("…") that ALSO appear verbatim
    # in src_prompt — those are likely the offending rules.
    import re as _re
    for m in _re.finditer(r'["“]([^"“”\n]{6,160})["”]', rationale):
        chunk = m.group(1).strip()
        if chunk and (chunk in src_prompt or chunk[:30] in src_prompt):
            out.append(chunk)
    # Dedup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped[:4]  # cap to 4 to avoid bloat


def _snake(camel: str) -> str:
    """billFromName → bill_from_name. Keep snake-cased input unchanged."""
    import re as _re
    s1 = _re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", camel)
    return _re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


_NEW_FIELD_LLM_SYSTEM = (
    "你是一个 OCR prompt 设计专家。给定一个客户新增字段（仅有名称、期望"
    "类型、可能的样例值，以及同模板里已有字段作风格参考），请输出一份"
    "完整、可直接生效的字段提取指令。返回纯 JSON，键必须包含：description"
    "（2~3 句业务含义）、ocr_prompt（多段：语义/位置锚点/格式约束/歧义"
    "辨别/找不到时怎么办）、ocr_suggestions（对象，键 semantics/position/"
    "most_common_feature/extra_features）。不要 markdown 围栏。"
)


def _llm_expand_new_field(
    *,
    diff: dict,
    schema_type: str,
    sibling_examples: str,
    processor_spec: str,
    model_name: str | None,
) -> dict | None:
    """Call an LLM to flesh out a customer-added field's prompt material.

    The customer only gives us {name, value, format}; we want a much richer
    description so the very first round has a fighting chance instead of
    relying on the optimizer to backfill the field's meaning later.
    """
    from .llm_failover import llm_text_completion_failover as _llm

    user_prompt = (
        f"# 新增字段\n"
        f"- 名称: {diff.get('corrected_name') or 'new_field'}\n"
        f"- 期望类型: {schema_type}\n"
        f"- 客户样例值: {diff.get('corrected_value') or '(未提供)'}\n\n"
        f"# 模板里已有字段（仅供风格对齐）\n"
        f"{sibling_examples or '(无)'}\n\n"
        f"按 JSON 输出：description / ocr_prompt / ocr_suggestions"
    )
    try:
        result = _llm(
            processor_spec=processor_spec,
            model_name=model_name,
            system_instruction=_NEW_FIELD_LLM_SYSTEM,
            user_prompt=user_prompt,
            as_json=True,
        )
        if isinstance(result, dict):
            return result
    except Exception as exc:
        logger.warning("LLM expansion for new field %s failed: %s",
                       diff.get('corrected_name'), exc)
    return None


def _module_from_add_diff(
    diff: dict, *, new_version_id: uuid.UUID, order_index: int, reflection_outputs,
    sibling_examples: str = "", processor_spec: str = "gemini",
    model_name: str | None = None,
) -> OcrModule:
    """Build a new OcrModule for a customer-added field.

    Per design v4 ("分拆-局部验证-重组"): kick off an LLM call to flesh out
    description + ocr_prompt + ocr_suggestions BEFORE the first iteration
    round. Falls back to a static skeleton if the LLM is unreachable.
    """
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

    # Static skeleton (used as fallback)
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
    description = f"客户新增字段：{new_name}"
    ocr_suggestions = {
        "semantics": "客户新增 — 待优化器学习",
        "position": "客户新增 — 待优化器学习",
        "most_common_feature": "—",
        "extra_features": [],
    }

    # Try LLM expansion first
    expanded = _llm_expand_new_field(
        diff=diff,
        schema_type=schema_type,
        sibling_examples=sibling_examples,
        processor_spec=processor_spec,
        model_name=model_name,
    )
    if expanded:
        if isinstance(expanded.get("description"), str) and expanded["description"].strip():
            description = expanded["description"].strip()
        if isinstance(expanded.get("ocr_prompt"), str) and expanded["ocr_prompt"].strip():
            ocr_prompt = expanded["ocr_prompt"].strip()
        if isinstance(expanded.get("ocr_suggestions"), dict):
            ocr_suggestions = {**ocr_suggestions, **expanded["ocr_suggestions"]}

    # Reflection-skill outputs (new_field skill) take priority — they had
    # the most context (sibling examples + customer intent)
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
                if isinstance(out.get("description"), str) and out["description"].strip():
                    description = out["description"]

    return OcrModule(
        id=uuid.uuid4(),
        prompt_version_id=new_version_id,
        module_key=module_key,
        display_name=new_name,
        description=description,
        json_path=f"$[*].{new_name}",
        schema_fragment=schema_fragment,
        ocr_suggestions=ocr_suggestions,
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


# (C6 cleanup) — _next_customer_api_code removed.
# Phase 19 stopped generating a separate "-c1" api_code per customize
# (the customer's API URL stays the same and the prompt version bumps
# in place). The helper had no other callers.
