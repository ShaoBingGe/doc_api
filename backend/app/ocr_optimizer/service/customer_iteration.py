"""
Customer-driven iteration pipeline.

Sequence triggered by `POST /api-definitions/{id}/customize`:
  1. Receive field diffs from the workspace (edits + adds)
  2. Run the reflection layer over each diff → ReflectionResult by module_key
  3. Fork the source ApiDefinition into a new one with its own api_code
     (so the customer's customized template is independent)
  4. Clone all OcrModule rows over to the new ApiDefinition, applying
     description / ocr_suggestions patches derived from the reflection
  5. Inside the new ApiDefinition: start_optimization → advance_round × 2
     (3 rounds total) → finalize_run on the best version
  6. Track job state in an in-memory store (good enough for MVP — survives
     until process restart)

The whole thing runs in a FastAPI BackgroundTask. Frontend polls `GET
/customize-jobs/{job_id}` for progress.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.core.database import SessionLocal
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus

from ..models import (
    OcrModule,
    OcrPromptVersion,
    PromptVersionStatus,
    RunStatus,
    VersionOrigin,
)
from ..reflection import reflect_on_diffs
from . import composer, persistence, run_orchestrator

logger = logging.getLogger(__name__)


# ── Job state (in-memory MVP) ────────────────────────────────────────────────


@dataclass
class CustomizeJob:
    id: str
    source_api_definition_id: str
    new_api_definition_id: str | None = None
    new_api_code: str | None = None
    status: str = "queued"   # queued | reflecting | forking | optimizing | completed | failed
    phase_detail: str = ""
    rounds_done: int = 0
    rounds_total: int = 3
    overall_accuracy: float | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    # Reflection outputs (truncated to bound payload size)
    reflection_summary: list[dict] = field(default_factory=list)


_JOBS: dict[str, CustomizeJob] = {}
_JOBS_LOCK = threading.Lock()


def _put_job(job: CustomizeJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.id] = job


def get_job(job_id: str) -> CustomizeJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _update(job: CustomizeJob, **kwargs) -> None:
    with _JOBS_LOCK:
        for k, v in kwargs.items():
            setattr(job, k, v)


# ── Entry point ──────────────────────────────────────────────────────────────


def submit_customize_job(
    *,
    source_api_definition_id: uuid.UUID,
    diffs: list[dict],
    user_id: uuid.UUID | None = None,
) -> CustomizeJob:
    """Create a job record and return immediately. Caller schedules
    `run_customize_job(job.id)` as a background task."""
    if not diffs:
        raise ValidationError("No field corrections provided")

    job = CustomizeJob(
        id=str(uuid.uuid4()),
        source_api_definition_id=str(source_api_definition_id),
    )
    _put_job(job)
    logger.info("Queued customize job %s for API %s with %d diff(s)",
                job.id, source_api_definition_id, len(diffs))
    # Stash inputs on the job so the background runner can read them without
    # re-receiving them via params.
    job._diffs = diffs  # type: ignore[attr-defined]
    job._user_id = user_id  # type: ignore[attr-defined]
    return job


def run_customize_job(job_id: str) -> None:
    """Run the full pipeline. Designed to be called via BackgroundTasks.

    Owns its own DB session (don't reuse request-scoped one in a background
    thread — SQLite + thread safety).
    """
    job = get_job(job_id)
    if not job:
        logger.error("run_customize_job: no such job id=%s", job_id)
        return
    diffs: list[dict] = getattr(job, "_diffs", [])
    user_id: uuid.UUID | None = getattr(job, "_user_id", None)

    db: Session = SessionLocal()
    try:
        _execute_pipeline(db, job, diffs, user_id)
    except Exception as exc:
        logger.exception("Customize job %s failed: %s", job.id, exc)
        _update(
            job,
            status="failed",
            error_message=str(exc)[:1024],
            completed_at=datetime.now(timezone.utc),
        )
        db.rollback()
    finally:
        db.close()


# ── Pipeline ─────────────────────────────────────────────────────────────────


def _execute_pipeline(db: Session, job: CustomizeJob, diffs: list[dict], user_id: uuid.UUID | None) -> None:
    src_id = uuid.UUID(job.source_api_definition_id)
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
    _update(job, status="reflecting", phase_detail="正在为每个字段调用反思 agent")
    reflections = reflect_on_diffs(
        diffs,
        modules_by_key=modules_by_key,
        processor_spec=src_api.processor_type or "gemini",
        model_name=src_api.model_name,
    )
    job.reflection_summary = [
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
    _update(job, status="forking", phase_detail="复制为客户专属模板，分配新 api_code")
    new_api, new_version, new_modules = _fork_api_definition(
        db, src_api=src_api, src_version=src_version, src_modules=src_modules,
        diffs=diffs, reflections=reflections, user_id=user_id,
    )
    _update(job, new_api_definition_id=str(new_api.id), new_api_code=new_api.api_code)

    # ── Phase 3: 3-round optimization ─────────────────────────────────────
    _update(job, status="optimizing", phase_detail="迭代优化 · 第 1 轮", rounds_done=0)
    try:
        run = run_orchestrator.start_optimization(
            db,
            new_api.id,
            max_rounds=3,
        )
    except ValidationError as exc:
        # Most common reason: no GT samples on the new ApiDefinition. We still
        # fork-and-deliver the new api_code so the customer can upload more
        # samples + retry, but mark the job partially completed.
        logger.warning("3-round skipped (no GT yet) for job %s: %s", job.id, exc)
        _update(
            job,
            status="completed",
            phase_detail=f"已生成新模板（{new_api.api_code}），但样本不足无法迭代：{exc}",
            completed_at=datetime.now(timezone.utc),
        )
        return

    _update(job, rounds_done=run.rounds_completed)

    # Run rounds 2 and 3 if we're still under target & under cap
    best_version_id = _latest_round_version(db, run.id) or src_version.id
    for _ in range(2):
        run = _refresh(db, run)
        if run.status != RunStatus.paused_for_review.value:
            break
        if run.current_round_num >= run.max_rounds:
            break
        try:
            run = run_orchestrator.advance_round(db, run.id)
            _update(job, rounds_done=run.rounds_completed,
                    phase_detail=f"迭代优化 · 第 {run.rounds_completed} 轮")
        except Exception as exc:
            logger.exception("advance_round failed for job %s: %s", job.id, exc)
            break

    best_version_id = _latest_round_version(db, run.id) or best_version_id

    # Finalize → activate the best version
    try:
        run = run_orchestrator.finalize_run(db, run.id, best_version_id)
        final_v = db.get(OcrPromptVersion, best_version_id)
        if final_v:
            job.overall_accuracy = final_v.overall_accuracy
    except Exception as exc:
        logger.exception("finalize_run failed for job %s: %s", job.id, exc)
        _update(job, error_message=str(exc)[:512])

    _update(
        job,
        status="completed",
        phase_detail=f"已完成 3 轮迭代，新模板 api_code = {new_api.api_code}",
        completed_at=datetime.now(timezone.utc),
    )


def _refresh(db: Session, run):
    db.refresh(run)
    return run


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
    """Clone src_api → new ApiDefinition with derived api_code + new active version.

    Applies reflection-driven patches (description, ocr_suggestions, ocr_prompt)
    AND adds new modules for `add`-kind diffs.

    Also copies sample_document_ids so the 3-round optimizer has GT samples to
    work with on the new ApiDefinition.
    """
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
    db.flush()  # we need new_api.id for the version

    # New prompt version: snapshot of source v1 with patches.
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

    # Build patched modules
    edits_by_key: dict[str, dict] = {}
    add_specs: list[dict] = []
    for d in diffs:
        if d.get("kind") == "edit":
            mk = d.get("module_key")
            if not mk:
                continue
            patch: dict[str, Any] = {}
            # description: prefer the LLM-suggested patch; fall back to a generic note.
            r = reflections.get(mk)
            if r and r.description_patch:
                patch["description"] = r.description_patch
            # Append the LLM fix_suggestions to ocr_prompt rather than replacing it.
            fix_text = "\n\n".join(r.fix_suggestions) if r else ""
            if fix_text:
                patch["__prompt_suffix"] = fix_text
            # Always note the customer's actual corrected value as a hint
            if d.get("corrected_value"):
                hint = f"客户在样本中提供的正确值示例：{d['corrected_value']}"
                patch["__prompt_suffix"] = (patch.get("__prompt_suffix", "") + "\n" + hint).strip()
            # Format change → schema patch
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

    # Append "add" modules
    order_start = max((m.order_index for m in new_modules), default=-1) + 1
    for i, d in enumerate(add_specs):
        new_modules.append(_module_from_add_diff(
            d, new_version_id=new_version.id, order_index=order_start + i,
            reflection_outputs=reflections.get(f"_new_{diffs.index(d)}"),
        ))

    # Compose new prompt + schema
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
        # Map UI format strings (string/number/date/...) to schema types.
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
    """Create a brand-new module for an 'add'-kind diff."""
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

    # Pull ocr_prompt from the LLM reflection (new_field skill) if available.
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
    """Generate a unique api_code derived from the source.

    Strategy: `<src_code>-c<n>` where n increments. If the source code already
    matches this pattern we strip the suffix and bump.
    """
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
            # Fall back to uuid suffix
            return f"{base}-c{uuid.uuid4().hex[:8]}"
