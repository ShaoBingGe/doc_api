"""
Public API templates — browse and subscribe.

GET  /api/v1/templates                  List available templates
GET  /api/v1/templates/{template_id}    Template detail
POST /api/v1/templates/{template_id}/subscribe   Subscribe (creates ApiDefinition)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.exceptions import NotFoundError
from app.schemas.api_definition import ApiDefinitionResponse
from app.services import template_service as svc

router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("")
def list_templates(
    country: str | None = Query(default=None, description="Filter by country code (CN, US, EU, GLOBAL)"),
    language: str | None = Query(default=None, description="Filter by language (zh, en, multi)"),
) -> list[dict]:
    return svc.list_templates(country=country, language=language)


@router.get("/{template_id}")
def get_template(template_id: str) -> dict:
    t = svc.get_template(template_id)
    if not t:
        raise NotFoundError(f"Template '{template_id}' not found")
    return t


@router.post("/{template_id}/subscribe", response_model=ApiDefinitionResponse, status_code=201)
def subscribe_template(
    template_id: str,
    name: str | None = Query(default=None, description="Custom name override"),
    db: Session = Depends(get_db),
) -> ApiDefinitionResponse:
    return svc.subscribe_template(db, template_id, custom_name=name)
