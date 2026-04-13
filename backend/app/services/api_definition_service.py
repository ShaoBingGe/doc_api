"""
ApiDefinitionService — API 定义 CRUD、状态管理、统计查询。
"""

from __future__ import annotations

import math
import uuid

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.schemas.api_definition import (
    ApiDefinitionResponse,
    ApiDocsResponse,
    ApiStatsResponse,
    CreateApiDefinitionRequest,
    UpdateApiDefinitionRequest,
    UpdateApiStatusRequest,
)
from app.schemas.common import PaginatedResponse

settings = get_settings()

_VALID_ACTIONS = {"activate", "deprecate"}
_STATUS_MAP = {
    "activate": ApiDefinitionStatus.active,
    "deprecate": ApiDefinitionStatus.deprecated,
}


def _build_endpoint_url(api_code: str) -> str:
    """Construct the public extraction endpoint URL."""
    # In production, replace with the actual domain from settings
    return f"/api/v1/extract/{api_code}"


def _to_response(api_def: ApiDefinition) -> ApiDefinitionResponse:
    data = ApiDefinitionResponse.model_validate(api_def)
    data.endpoint_url = _build_endpoint_url(api_def.api_code)
    cfg = api_def.config or {}
    data.sample_document_id = cfg.get("sample_document_id")
    data.source_type = "template" if cfg.get("source_template_id") else "custom"
    return data


# ── Create ────────────────────────────────────────────────────────────────────

def create_api_definition(
    db: Session,
    body: CreateApiDefinitionRequest,
    user_id: uuid.UUID | None = None,
) -> ApiDefinitionResponse:
    # Enforce unique api_code
    existing = db.query(ApiDefinition).filter(ApiDefinition.api_code == body.api_code).first()
    if existing:
        raise ConflictError(f"api_code '{body.api_code}' is already in use")

    response_schema = body.response_schema
    # If no schema provided and a conversation_id was given, inherit from latest result
    if response_schema is None and body.conversation_id:
        response_schema = _schema_from_conversation(db, body.conversation_id)

    # Merge sample_document_id into config dict
    config = dict(body.config or {})
    if body.sample_document_id:
        config["sample_document_id"] = str(body.sample_document_id)

    api_def = ApiDefinition(
        user_id=user_id,
        name=body.name,
        api_code=body.api_code,
        description=body.description,
        status=ApiDefinitionStatus.draft,
        version=1,
        response_schema=response_schema,
        processor_type=body.processor_type,
        model_name=body.model_name,
        config=config or None,
        source_conversation_id=body.conversation_id,
    )
    db.add(api_def)
    db.commit()
    db.refresh(api_def)
    return _to_response(api_def)


def _schema_from_conversation(db: Session, conversation_id: uuid.UUID) -> dict | None:
    """Pull current_schema from a Conversation (stub path)."""
    try:
        from app.models.conversation import Conversation
        conv = db.get(Conversation, conversation_id)
        if conv:
            return conv.current_schema
    except Exception:
        pass
    return None


# ── List ──────────────────────────────────────────────────────────────────────

def list_api_definitions(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    search: str | None = None,
) -> PaginatedResponse[ApiDefinitionResponse]:
    q = db.query(ApiDefinition)
    if status_filter:
        q = q.filter(ApiDefinition.status == status_filter)
    if search:
        pattern = f"%{search}%"
        q = q.filter(
            (ApiDefinition.name.ilike(pattern)) | (ApiDefinition.api_code.ilike(pattern))
        )
    q = q.order_by(desc(ApiDefinition.created_at))
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedResponse(
        items=[_to_response(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, math.ceil(total / page_size)),
    )


# ── Get ───────────────────────────────────────────────────────────────────────

def get_api_definition(db: Session, api_def_id: uuid.UUID) -> ApiDefinitionResponse:
    api_def = _get_or_404(db, api_def_id)
    return _to_response(api_def)


def _get_or_404(db: Session, api_def_id: uuid.UUID) -> ApiDefinition:
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")
    return api_def


def get_api_def_by_code(db: Session, api_code: str) -> ApiDefinition:
    api_def = db.query(ApiDefinition).filter(ApiDefinition.api_code == api_code).first()
    if not api_def:
        raise NotFoundError(f"api_code '{api_code}' not found")
    return api_def


# ── Update ────────────────────────────────────────────────────────────────────

def update_api_definition(
    db: Session,
    api_def_id: uuid.UUID,
    body: UpdateApiDefinitionRequest,
) -> ApiDefinitionResponse:
    api_def = _get_or_404(db, api_def_id)

    if body.name is not None:
        api_def.name = body.name
    if body.description is not None:
        api_def.description = body.description
    if body.processor_type is not None:
        api_def.processor_type = body.processor_type
    if body.model_name is not None:
        api_def.model_name = body.model_name
    if body.config is not None:
        api_def.config = body.config
    if body.response_schema is not None:
        api_def.response_schema = body.response_schema
        api_def.version += 1  # Schema change bumps version

    db.commit()
    db.refresh(api_def)
    return _to_response(api_def)


def update_api_status(
    db: Session,
    api_def_id: uuid.UUID,
    body: UpdateApiStatusRequest,
) -> ApiDefinitionResponse:
    if body.action not in _VALID_ACTIONS:
        raise ValidationError(f"action must be one of {_VALID_ACTIONS}")
    api_def = _get_or_404(db, api_def_id)
    api_def.status = _STATUS_MAP[body.action]
    db.commit()
    db.refresh(api_def)
    return _to_response(api_def)


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_api_definition(db: Session, api_def_id: uuid.UUID) -> None:
    api_def = _get_or_404(db, api_def_id)
    db.delete(api_def)
    db.commit()


# ── Stats & Docs ──────────────────────────────────────────────────────────────

def get_stats(db: Session, api_def_id: uuid.UUID) -> ApiStatsResponse:
    """
    Usage statistics from UsageRecord table.
    Prototype returns zeros until UsageRecord is populated.
    """
    _get_or_404(db, api_def_id)
    # TODO: aggregate from UsageRecord when that model is added
    return ApiStatsResponse()


def get_api_docs(db: Session, api_def_id: uuid.UUID) -> ApiDocsResponse:
    api_def = _get_or_404(db, api_def_id)
    return ApiDocsResponse(
        api_code=api_def.api_code,
        name=api_def.name,
        description=api_def.description,
        version=api_def.version,
        endpoint=_build_endpoint_url(api_def.api_code),
        response_schema=api_def.response_schema,
        error_codes=[
            {"http": 401, "code": "invalid_api_key", "description": "API Key 无效或已吊销"},
            {"http": 404, "code": "api_not_found", "description": "api_code 不存在"},
            {"http": 410, "code": "api_deprecated", "description": "API 已废弃"},
            {"http": 413, "code": "file_too_large", "description": "文件超过 20MB"},
            {"http": 422, "code": "processing_error", "description": "AI 处理失败"},
            {"http": 429, "code": "rate_limit_exceeded", "description": "超过调用频率限制"},
        ],
    )
