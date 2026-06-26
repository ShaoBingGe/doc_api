"""
平台管理员 · 国家模板 / 黄金种子审阅端点（Part 3）。仅 super/system admin。

  GET  /api/v1/platform/country-templates                列出国家 + 模板种类
  GET  /api/v1/platform/golden/{country}/seeds           黄金种子 + 人工 GT
  GET  /api/v1/platform/golden/{country}/seeds/{id}/file 下载该种子 PDF
  POST /api/v1/platform/golden/{country}/evaluate        按需用当前模板跑 OCR + 缓存
  GET  /api/v1/platform/golden/{country}/evaluation      读取缓存评测（无则空）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_roles
from app.core.exceptions import NotFoundError
from app.models.user import UserRole
from app.ocr_optimizer.eval import golden_review

# Platform-only: country templates / golden seeds / optimization platform.
router = APIRouter(
    prefix="/platform",
    tags=["Platform · 国家模板"],
    dependencies=[Depends(require_roles(UserRole.super_admin, UserRole.system_admin))],
)


@router.get("/country-templates", summary="国家模板列表（含种类）")
def list_country_templates() -> list[dict]:
    return golden_review.list_country_kinds()


@router.get(
    "/skill-promotion/candidates",
    summary="技能晋升候选（采收反思 skill_feedback，只读）",
)
def list_skill_promotion_candidates(
    country: str | None = Query(None, description="按国家过滤（如 JP / MY）；省略=全部"),
    db: Session = Depends(get_db),
) -> dict:
    """P4 步骤①：只读采收。把反思每轮在 `skill_feedback` 产出的「该字段应有什么技能」建议，
    按 `(国家, 字段)` 聚成晋升候选 + 跨租户计数。`recommended`=跨租户>5（自动推荐信号，
    **非硬门**——管理员可越级晋升低于阈值者）。**绝不写技能**；真正晋升是另一步管理员确认动作。
    """
    from app.ocr_optimizer.service import skill_promotion

    cands = skill_promotion.find_promotion_candidates(db, country=country)
    return {
        "country": country,
        "total": len(cands),
        "recommended": sum(1 for c in cands if c.recommended),
        "candidates": [c.to_dict() for c in cands],
    }


class _DraftReq(BaseModel):
    country: str
    field: str
    sample_feedback: list[str] = []


class _PromoteReq(BaseModel):
    country: str
    field: str
    name: str
    content: str
    description: str | None = None


@router.post("/skill-promotion/draft", summary="LLM 起草晋升技能正文（不写库）")
def draft_promotion_skill(body: _DraftReq) -> dict:
    """步骤③-起草：用境内合规文本模型从「字段 + 反思原文」起草一条技能草稿，**不写库**。
    管理员在前端编辑后再调 /promote 确认入库。"""
    from app.ocr_optimizer.service import skill_promotion

    return skill_promotion.draft_skill(body.country, body.field, body.sample_feedback)


@router.post("/skill-promotion/promote", summary="管理员确认晋升 → 写入全局技能库")
def promote_skill(
    body: _PromoteReq,
    db: Session = Depends(get_db),
    user=Depends(require_roles(UserRole.super_admin, UserRole.system_admin)),
) -> dict:
    """步骤③-确认：管理员编辑确认后写入**全局** `OcrSkill`（api_definition_id=NULL）。
    优化器被硬禁写技能，本写入由管理员显式发起。写后需 attach 到 module 才生效（步骤④）。"""
    from app.ocr_optimizer.service import skill_service

    sk = skill_service.create_skill(
        db,
        name=body.name,
        content=body.content,
        description=body.description or f"P4 晋升自 {body.country}/{body.field}",
        api_def_id=None,
        created_by=getattr(user, "id", None),
    )
    return {
        "id": str(sk.id),
        "name": sk.name,
        "scope": "global",
        "status": sk.status,
        "country": body.country,
        "field": body.field,
    }


@router.get("/golden/{country}/seeds", summary="黄金种子 + 人工 GT")
def list_golden_seeds(country: str) -> dict:
    return {"country": country.upper(), "seeds": golden_review.load_seeds(country)}


@router.get("/golden/{country}/seeds/{seed_id}/file", summary="下载黄金种子 PDF")
def get_golden_seed_file(country: str, seed_id: str) -> FileResponse:
    path = golden_review.golden_pdf_path(country, seed_id)
    if not path:
        raise NotFoundError("golden seed file not found")
    return FileResponse(path, media_type="application/pdf")


@router.post("/golden/{country}/evaluate", summary="后台触发：用当前国家模板对黄金集跑 OCR")
def evaluate_golden(
    country: str,
    processor: str | None = Query(default=None, description="OCR processor spec；留空用 DEFAULT_PROCESSOR"),
    limit: int = Query(default=0, ge=0, description="0 = 全部种子"),
) -> dict:
    # Runs in a background thread (OCR is minutes long); returns immediately.
    # Frontend polls GET /evaluation until `running` clears. This is what keeps
    # the single-worker site responsive during evaluation.
    return golden_review.start_evaluate_async(country, processor_spec=processor or None, limit=limit)


@router.get("/golden/{country}/evaluation", summary="读取缓存的最新评测结果")
def get_golden_evaluation(country: str) -> dict:
    cached = golden_review.load_cached_eval(country)
    return cached or {"country": country.upper(), "per_seed": {}, "cached": False}
