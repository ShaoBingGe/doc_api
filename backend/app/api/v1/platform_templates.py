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


@router.get("/golden/{country}/seeds", summary="黄金种子 + 人工 GT")
def list_golden_seeds(country: str) -> dict:
    return {"country": country.upper(), "seeds": golden_review.load_seeds(country)}


@router.get("/golden/{country}/seeds/{seed_id}/file", summary="下载黄金种子 PDF")
def get_golden_seed_file(country: str, seed_id: str) -> FileResponse:
    path = golden_review.golden_pdf_path(country, seed_id)
    if not path:
        raise NotFoundError("golden seed file not found")
    return FileResponse(path, media_type="application/pdf")


@router.post("/golden/{country}/evaluate", summary="用当前国家模板对黄金集跑 OCR（按需）")
def evaluate_golden(
    country: str,
    processor: str | None = Query(default=None, description="OCR processor spec；留空用 DEFAULT_PROCESSOR"),
    limit: int = Query(default=0, ge=0, description="0 = 全部种子"),
    db: Session = Depends(get_db),
) -> dict:
    return golden_review.evaluate(db, country, processor_spec=processor or None, limit=limit)


@router.get("/golden/{country}/evaluation", summary="读取缓存的最新评测结果")
def get_golden_evaluation(country: str) -> dict:
    cached = golden_review.load_cached_eval(country)
    return cached or {"country": country.upper(), "per_seed": {}, "cached": False}
