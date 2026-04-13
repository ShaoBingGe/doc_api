"""
Prompt optimization API endpoints.

POST  /api/v1/api-definitions/{id}/optimize                         Trigger optimization
GET   /api/v1/api-definitions/{id}/prompt-versions                  List prompt versions
PATCH /api/v1/api-definitions/{id}/prompt-versions/{vid}/activate   Activate a version
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.services import prompt_optimizer as svc

router = APIRouter(prefix="/api-definitions", tags=["Prompt Optimization"])


@router.post("/{api_def_id}/optimize")
def trigger_optimization(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Trigger iterative prompt optimization based on user corrections."""
    return svc.optimize(db, api_def_id)


@router.get("/{api_def_id}/prompt-versions")
def list_prompt_versions(
    api_def_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[dict]:
    """List all prompt versions for an API definition."""
    return svc.list_versions(db, api_def_id)


@router.patch("/{api_def_id}/prompt-versions/{version_id}/activate")
def activate_prompt_version(
    api_def_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Activate a specific prompt version."""
    return svc.activate_version(db, api_def_id, version_id)
