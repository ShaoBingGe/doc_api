"""
HTTP routes for the OCR optimizer subsystem.

All endpoints live under /api/v1/api-definitions/{api_def_id}/ocr-optimizer/...

**v2 changes (paused-for-review state machine)**:
  - POST /optimize now runs only Round 1 and returns paused_for_review
  - NEW POST /runs/{run_id}/advance
  - NEW POST /runs/{run_id}/finalize
  - NEW POST /runs/{run_id}/abort
  - NEW POST /versions/{version_id}/manual-patch
  - Skill endpoints (501 Not Implemented placeholders)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import assert_can_access, get_current_user, get_db

from .models import OcrPromptVersion
from .schemas import (
    ActivateResponse,
    AdvanceRequest,
    FinalizeRequest,
    InitVersionRequest,
    IterationResponse,
    ManualPatchRequest,
    OcrModuleResponse,
    OcrPromptVersionDetail,
    OcrPromptVersionSummary,
    OcrSkillCreateRequest,
    OcrSkillResponse,
    OptimizeRequest,
    OptimizeTriggerResponse,
    RoundDetail,
    RoundSummary,
    RunDetail,
    RunSummary,
)
from .service import persistence
from .service.module_initializer import init_version
from .service.run_orchestrator import (
    abort_run,
    advance_round,
    finalize_run,
    manual_patch,
    start_optimization,
)

def verify_api_def_access(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
) -> None:
    """Router-level guard: every OCR-optimizer route is nested under
    /{api_def_id}/...; require a valid JWT AND tenant access to that ApiDef.
    Platform admins bypass the tenant check (see core.deps.assert_can_access).
    """
    from app.models.api_definition import ApiDefinition

    api_def = db.get(ApiDefinition, api_def_id)
    if api_def is None:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")
    assert_can_access(api_def, user)


# All routes require auth + tenant access to the parent ApiDefinition.
router = APIRouter(
    prefix="/api-definitions",
    tags=["OCR Optimizer"],
    dependencies=[Depends(verify_api_def_access)],
)


# ── Init ──────────────────────────────────────────────────────────────────────

@router.post(
    "/{api_def_id}/ocr-optimizer/init",
    response_model=OcrPromptVersionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="自动拆分 response_schema 为初始模块组合",
)
def post_init(
    api_def_id: uuid.UUID,
    body: InitVersionRequest,
    db: Session = Depends(get_db),
) -> OcrPromptVersionDetail:
    version = init_version(
        db,
        api_def_id,
        sample_document_ids=body.sample_document_ids or None,
        activate=body.activate,
        use_llm_for_modules=body.use_llm_for_modules,
    )
    return _version_to_detail(version)


# ── Versions ──────────────────────────────────────────────────────────────────

@router.get(
    "/{api_def_id}/ocr-optimizer/versions",
    response_model=list[OcrPromptVersionSummary],
    summary="列出该 API 的所有 prompt 版本",
)
def list_versions(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[OcrPromptVersionSummary]:
    versions = persistence.list_versions(db, api_def_id)
    return [_version_to_summary(v) for v in versions]


@router.get(
    "/{api_def_id}/ocr-optimizer/versions/{version_id}",
    response_model=OcrPromptVersionDetail,
    summary="获取某个 prompt 版本的完整详情（含所有模块）",
)
def get_version(
    api_def_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> OcrPromptVersionDetail:
    v = persistence.get_version_or_404(db, version_id)
    return _version_to_detail(v)


@router.get(
    "/{api_def_id}/ocr-optimizer/skill-insights",
    summary="技能/优化洞察（P5，只读）：每字段轨迹 + 守护 + 已挂技能",
)
def get_skill_insights(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """P5 显性化：最近一次 run 的每字段跨轮准确率轨迹（P1 留出分）、slow-update 守护状态
    （P3）、active 版本各字段已挂技能名（P2/P4）。纯读。"""
    from app.ocr_optimizer.service import skill_insights

    return skill_insights.build_insights(db, api_def_id)


@router.patch(
    "/{api_def_id}/ocr-optimizer/versions/{version_id}/activate",
    response_model=ActivateResponse,
    summary="激活指定版本（取消其他 active）",
)
def activate(
    api_def_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ActivateResponse:
    v = persistence.activate_version(db, api_def_id, version_id)
    return ActivateResponse(
        id=v.id, version=v.version, status=v.status, activated_at=v.activated_at
    )


# ── Optimize (Run) ────────────────────────────────────────────────────────────

@router.post(
    "/{api_def_id}/ocr-optimizer/optimize",
    response_model=OptimizeTriggerResponse,
    summary="启动 Run + 跑第一轮，然后挂起（paused_for_review）",
)
def trigger_optimize(
    api_def_id: uuid.UUID,
    body: OptimizeRequest,
    db: Session = Depends(get_db),
) -> OptimizeTriggerResponse:
    """Run is now per-round; client must POST /advance to continue."""
    run = start_optimization(
        db,
        api_def_id,
        max_rounds=body.max_rounds,
        target_accuracy=body.target_accuracy,
        sample_document_ids_override=body.sample_document_ids,
        llm_provider_override=body.llm_provider,
    )
    return OptimizeTriggerResponse(
        run_id=run.id,
        status=run.status,
        rounds_completed=run.rounds_completed,
        current_round_num=run.current_round_num,
        starting_version_id=run.starting_version_id,
        resulting_version_id=run.resulting_version_id,
        overall_accuracy=_run_best_accuracy(db, run),
        error_message=run.error_message,
    )


@router.post(
    "/{api_def_id}/ocr-optimizer/runs/{run_id}/advance",
    response_model=OptimizeTriggerResponse,
    summary="推进 Run 到下一轮（仅在 paused_for_review 状态下可用）",
)
def trigger_advance(
    api_def_id: uuid.UUID,
    run_id: uuid.UUID,
    body: AdvanceRequest,
    db: Session = Depends(get_db),
) -> OptimizeTriggerResponse:
    run = advance_round(db, run_id, use_version_id=body.use_version_id)
    return OptimizeTriggerResponse(
        run_id=run.id,
        status=run.status,
        rounds_completed=run.rounds_completed,
        current_round_num=run.current_round_num,
        starting_version_id=run.starting_version_id,
        resulting_version_id=run.resulting_version_id,
        overall_accuracy=_run_best_accuracy(db, run),
        error_message=run.error_message,
    )


@router.post(
    "/{api_def_id}/ocr-optimizer/runs/{run_id}/finalize",
    response_model=RunSummary,
    summary="结束 Run 并激活用户选定的版本",
)
def trigger_finalize(
    api_def_id: uuid.UUID,
    run_id: uuid.UUID,
    body: FinalizeRequest,
    db: Session = Depends(get_db),
) -> RunSummary:
    run = finalize_run(db, run_id, body.version_id)
    return RunSummary.model_validate(run)


@router.post(
    "/{api_def_id}/ocr-optimizer/runs/{run_id}/abort",
    response_model=RunSummary,
    summary="放弃此次 Run（不激活任何版本）",
)
def trigger_abort(
    api_def_id: uuid.UUID,
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RunSummary:
    run = abort_run(db, run_id)
    return RunSummary.model_validate(run)


@router.post(
    "/{api_def_id}/ocr-optimizer/versions/{version_id}/manual-patch",
    response_model=OcrPromptVersionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="基于版本派生 manual_edit 派生版本（draft, 不激活）",
)
def post_manual_patch(
    api_def_id: uuid.UUID,
    version_id: uuid.UUID,
    body: ManualPatchRequest,
    db: Session = Depends(get_db),
) -> OcrPromptVersionDetail:
    """
    Apply user edits (description/ocr_suggestions only) to derive a new
    draft version with origin='manual_edit'. NOT activated automatically;
    must be selected via /finalize or used as use_version_id in /advance.
    """
    new_version = manual_patch(
        db,
        api_definition_id=api_def_id,
        source_version_id=version_id,
        edits=[e.model_dump() for e in body.edits],
    )
    return _version_to_detail(new_version)


# ── Skill endpoints (TODO — return 501) ───────────────────────────────────────

_SKILL_TODO_MSG = (
    "Skills are coming soon. This endpoint is a placeholder; the skill "
    "subsystem is not yet implemented (see docs/ocr-optimizer-design.md §17)."
)


@router.get(
    "/{api_def_id}/ocr-optimizer/skills",
    response_model=list[OcrSkillResponse],
    summary="列出该 API 可用的 skills（私有 + 全局库）",
)
def list_skills(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[OcrSkillResponse]:
    from app.ocr_optimizer.service import skill_service
    return [OcrSkillResponse.model_validate(s) for s in skill_service.list_skills(db, api_def_id)]


@router.post(
    "/{api_def_id}/ocr-optimizer/skills",
    response_model=OcrSkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建一个 skill（api_definition_id=null 即全局库）",
)
def create_skill(
    api_def_id: uuid.UUID,
    body: OcrSkillCreateRequest,
    db: Session = Depends(get_db),
) -> OcrSkillResponse:
    from app.ocr_optimizer.service import skill_service
    sk = skill_service.create_skill(
        db, name=body.name, content=body.content, description=body.description,
        api_def_id=body.api_definition_id,
    )
    return OcrSkillResponse.model_validate(sk)


@router.delete(
    "/{api_def_id}/ocr-optimizer/skills/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="停用一个 skill（软删除）",
)
def delete_skill(
    api_def_id: uuid.UUID,
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    from app.ocr_optimizer.service import skill_service
    skill_service.delete_skill(db, skill_id)


@router.post(
    "/{api_def_id}/ocr-optimizer/versions/{version_id}/modules/{module_key}/skills/{skill_id}",
    summary="把已存在的 skill 挂到某版本的模块上",
)
def attach_skill(
    api_def_id: uuid.UUID,
    version_id: uuid.UUID,
    module_key: str,
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    from app.ocr_optimizer.service import skill_service
    mod = skill_service.attach_skill_to_module(db, version_id, module_key, skill_id)
    return {"module_key": mod.module_key, "skill_ids": list(mod.skill_ids or [])}


@router.get(
    "/{api_def_id}/ocr-optimizer/runs",
    response_model=list[RunSummary],
    summary="列出该 API 的所有 optimize Run",
)
def list_runs(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[RunSummary]:
    return [RunSummary.model_validate(r) for r in persistence.list_runs(db, api_def_id)]


@router.get(
    "/{api_def_id}/ocr-optimizer/runs/{run_id}",
    response_model=RunDetail,
    summary="Run 详情（含 rounds 摘要）",
)
def get_run(
    api_def_id: uuid.UUID,
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RunDetail:
    run = persistence.get_run_or_404(db, run_id)
    detail = RunDetail.model_validate(run)
    detail.rounds = [RoundSummary.model_validate(r) for r in run.rounds]
    return detail


@router.get(
    "/{api_def_id}/ocr-optimizer/runs/{run_id}/rounds/{round_num}",
    response_model=RoundDetail,
    summary="单轮详情（含所有模块 iteration）",
)
def get_round(
    api_def_id: uuid.UUID,
    run_id: uuid.UUID,
    round_num: int,
    db: Session = Depends(get_db),
) -> RoundDetail:
    rnd = persistence.get_round_or_404(db, run_id, round_num)
    detail = RoundDetail.model_validate(rnd)
    detail.iterations = [IterationResponse.model_validate(i) for i in rnd.iterations]
    return detail


@router.get(
    "/{api_def_id}/ocr-optimizer/runs/{run_id}/iterations",
    response_model=list[IterationResponse],
    summary="Flat 列出本 Run 所有迭代",
)
def list_iterations(
    api_def_id: uuid.UUID,
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[IterationResponse]:
    iters = persistence.list_iterations_for_run(db, run_id)
    return [IterationResponse.model_validate(i) for i in iters]


@router.get(
    "/{api_def_id}/ocr-optimizer/runs/{run_id}/field-accuracy",
    summary="字段级准确率收敛时间线（每轮 × 每字段）",
)
def get_field_accuracy(
    api_def_id: uuid.UUID,
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    return persistence.field_accuracy_timeline(db, run_id)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _version_to_summary(v: OcrPromptVersion) -> OcrPromptVersionSummary:
    summary = OcrPromptVersionSummary.model_validate(v)
    summary.module_count = len(v.modules)
    preview = (v.composed_prompt or "")[:200]
    summary.composed_prompt_preview = preview + ("..." if len(v.composed_prompt or "") > 200 else "")
    return summary


def _version_to_detail(v: OcrPromptVersion) -> OcrPromptVersionDetail:
    detail = OcrPromptVersionDetail.model_validate(v)
    detail.modules = [OcrModuleResponse.model_validate(m) for m in v.modules]
    return detail


def _run_best_accuracy(db: Session, run) -> float | None:
    """Best accuracy achieved across the Run's rounds (None if no rounds)."""
    if not run.rounds:
        return None
    accs = [r.overall_accuracy for r in run.rounds if r.overall_accuracy is not None]
    return max(accs) if accs else None
