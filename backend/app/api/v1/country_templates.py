"""
Country-template endpoints (§6.4).

GET  /api/v1/country-templates            List which country chips are available
POST /api/v1/api-definitions/from-country-template
                                          Create placeholder ApiDef + v1 + modules
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.ocr_optimizer.service import preset_init, template_loader

router = APIRouter(tags=["Country Templates"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CountryTemplateInfo(BaseModel):
    country: str
    available: bool


class FromCountryTemplateRequest(BaseModel):
    country: str = Field(..., description="Country code, e.g. 'MY'")


class FromCountryTemplateResponse(BaseModel):
    api_definition_id: str
    version_id: str
    redirect_url: str
    module_count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/country-templates",
    response_model=list[CountryTemplateInfo],
    summary="可用的国家模板列表",
)
def list_country_templates() -> list[CountryTemplateInfo]:
    """Return the hardcoded chip list ([MY, CN, US, EU, GLOBAL]) annotated
    with whether each country's yaml is present at the repo root."""
    return [CountryTemplateInfo(**item) for item in template_loader.list_available_countries()]


@router.post(
    "/api-definitions/from-country-template",
    response_model=FromCountryTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="从国家预设模板创建占位 ApiDefinition + v1 + 30 modules（§6.4）",
)
def create_from_country_template(
    body: FromCountryTemplateRequest,
    db: Session = Depends(get_db),
) -> FromCountryTemplateResponse:
    result = preset_init.init_from_country_template(db, body.country)
    return FromCountryTemplateResponse(**result)
