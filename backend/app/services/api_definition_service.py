"""
ApiDefinitionService — API 定义 CRUD、状态管理、统计查询。
"""

from __future__ import annotations

import logging
import math
import uuid

logger = logging.getLogger(__name__)

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

_VALID_ACTIONS = {"submit_review", "activate", "deprecate", "deactivate"}
_STATUS_MAP = {
    "submit_review": ApiDefinitionStatus.pending_review,  # 保存并生成 → 待验证
    "activate": ApiDefinitionStatus.active,               # 激活发布 → 已发布
    "deprecate": ApiDefinitionStatus.deprecated,          # 停用 → 已停用（保留旧动作名）
    "deactivate": ApiDefinitionStatus.deprecated,         # 停用 → 已停用
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
    user=None,
) -> ApiDefinitionResponse:
    from app.core.deps import owner_tenant_id

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
        tenant_id=owner_tenant_id(user) if user is not None else None,
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

_PLACEHOLDER_TTL_DAYS = 7


def _gc_stale_placeholders(db: Session) -> None:
    """Lazy GC: delete pending_first_doc rows older than _PLACEHOLDER_TTL_DAYS.

    Called at the head of list_api_definitions. Failures are swallowed so a
    GC hiccup never blocks the list response.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=_PLACEHOLDER_TTL_DAYS)
    try:
        stale = (
            db.query(ApiDefinition)
            .filter(
                ApiDefinition.status == ApiDefinitionStatus.pending_first_doc.value,
                ApiDefinition.updated_at < cutoff,
            )
            .all()
        )
        for row in stale:
            db.delete(row)
        if stale:
            db.commit()
    except Exception:
        db.rollback()


def list_api_definitions(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    status_filter: str | None = None,
    search: str | None = None,
    include_pending: bool = False,
    user=None,
) -> PaginatedResponse[ApiDefinitionResponse]:
    from app.core.deps import scope_filter

    _gc_stale_placeholders(db)

    q = db.query(ApiDefinition)
    q = scope_filter(q, ApiDefinition, user)
    if status_filter:
        q = q.filter(ApiDefinition.status == status_filter)
    elif not include_pending:
        # Hide placeholder APIs from the default list view (see design §16.3)
        q = q.filter(ApiDefinition.status != ApiDefinitionStatus.pending_first_doc.value)
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

def get_api_definition(db: Session, api_def_id: uuid.UUID, user=None) -> ApiDefinitionResponse:
    api_def = _get_or_404(db, api_def_id, user)
    return _to_response(api_def)


def _get_or_404(db: Session, api_def_id: uuid.UUID, user=None) -> ApiDefinition:
    api_def = db.get(ApiDefinition, api_def_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_def_id} not found")
    if user is not None:
        from app.core.deps import assert_can_access
        assert_can_access(api_def, user)
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
    user=None,
) -> ApiDefinitionResponse:
    api_def = _get_or_404(db, api_def_id, user)

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
    user=None,
) -> ApiDefinitionResponse:
    if body.action not in _VALID_ACTIONS:
        raise ValidationError(f"action must be one of {_VALID_ACTIONS}")
    api_def = _get_or_404(db, api_def_id, user)
    api_def.status = _STATUS_MAP[body.action]
    db.commit()
    db.refresh(api_def)
    return _to_response(api_def)


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_api_definition(db: Session, api_def_id: uuid.UUID, user=None) -> None:
    api_def = _get_or_404(db, api_def_id, user)
    db.delete(api_def)
    db.commit()


# ── Sample documents (batch optimization) ─────────────────────────────────────
#
# An ApiDefinition can own a *sample set* of Documents used by the
# `ocr_optimizer` subsystem as Ground Truth carriers. The set is persisted
# as `config["sample_document_ids"]: list[str]`. These three helpers keep
# the list in sync and surface a typed view to the API layer.

def list_sample_documents(db: Session, api_def_id: uuid.UUID, user=None) -> list:
    """Resolve config.sample_document_ids → list of Document ORM rows.
    Silently drops IDs whose Document was deleted."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.document import Document

    api_def = _get_or_404(db, api_def_id, user)
    cfg = dict(api_def.config or {})
    ids: list[str] = list(cfg.get("sample_document_ids") or [])
    if not ids:
        return []

    docs = (
        db.query(Document)
        .filter(Document.id.in_([uuid.UUID(x) for x in ids]))
        .all()
    )
    docs_by_id = {str(d.id): d for d in docs}

    # Prune missing IDs back into config (cheap self-heal)
    alive_ids = [x for x in ids if x in docs_by_id]
    if alive_ids != ids:
        cfg["sample_document_ids"] = alive_ids
        api_def.config = cfg
        flag_modified(api_def, "config")
        db.commit()

    # Return in the order the user originally inserted them
    return [docs_by_id[x] for x in alive_ids]


def add_sample_document(
    db: Session,
    *,
    api_def_id: uuid.UUID,
    filename: str,
    file_data: bytes,
    content_type: str | None,
    user=None,
):
    """Upload a file, bind it to the API, run OCR using the API's active prompt,
    and append the new Document.id to config.sample_document_ids.

    The OCR step may fail (most commonly: Gemini connectivity). When that
    happens `bind_to_api_and_extract` catches the error and marks the
    Document as failed — the upload itself still succeeds so the customer
    can retry OCR later via the reprocess endpoint.

    Customer-supplied GT: per design v3 the resulting annotations are NOT
    auto-marked as ground truth. The customer must explicitly confirm each
    sample's extraction via `POST .../samples/{doc_id}/confirm-gt` (or
    correct individual fields through the edit panel) before the 3-round
    iteration will consider this sample.

    Also auto-triggers any parked CustomizeJob in `waiting_for_samples`
    once the count of *confirmed* samples reaches MIN_SAMPLES_FOR_ITERATION.
    """
    from threading import Thread

    from app.ocr_optimizer.service import customer_iteration
    from app.services.document_service import (
        bind_to_api_and_extract,
        upload_document,
    )

    _get_or_404(db, api_def_id, user)  # 404 / access guard
    doc = upload_document(
        db,
        filename=filename,
        file_data=file_data,
        content_type=content_type,
        api_definition_id=api_def_id,
        user=user,
    )
    bind_to_api_and_extract(db, doc, api_def_id)
    db.refresh(doc)

    # ── Auto-resume any waiting customize job for this ApiDef ───────────
    #
    # Just-uploaded samples don't count toward the gate yet (they aren't
    # confirmed GT). Auto-resume only triggers when the COUNT of CONFIRMED
    # samples hits the threshold — which happens via the confirm-gt
    # endpoint, not here. We still call into the gate function so a
    # backlog of already-confirmed samples gets re-checked on each upload
    # (cheap, idempotent).
    try:
        customer_iteration.maybe_auto_resume_for_api(api_def_id)
    except Exception:
        logger.exception("Failed to evaluate customize job auto-resume")

    return doc


def remove_sample_document(
    db: Session,
    *,
    api_def_id: uuid.UUID,
    document_id: uuid.UUID,
    user=None,
) -> None:
    """Drop a Document from the sample set (does NOT delete the Document itself)."""
    from sqlalchemy.orm.attributes import flag_modified

    api_def = _get_or_404(db, api_def_id, user)
    cfg = dict(api_def.config or {})
    ids: list[str] = list(cfg.get("sample_document_ids") or [])
    sid = str(document_id)
    if sid not in ids:
        raise NotFoundError(
            f"document '{sid}' is not in API '{api_def_id}' sample set",
            {"code": "sample_not_found"},
        )
    cfg["sample_document_ids"] = [x for x in ids if x != sid]
    api_def.config = cfg
    flag_modified(api_def, "config")
    db.commit()


# ── Stats & Docs ──────────────────────────────────────────────────────────────

def get_stats(db: Session, api_def_id: uuid.UUID, user=None) -> ApiStatsResponse:
    """
    Usage statistics from UsageRecord table.
    Prototype returns zeros until UsageRecord is populated.
    """
    _get_or_404(db, api_def_id, user)
    # TODO: aggregate from UsageRecord when that model is added
    return ApiStatsResponse()


def get_api_docs(db: Session, api_def_id: uuid.UUID, user=None) -> ApiDocsResponse:
    api_def = _get_or_404(db, api_def_id, user)
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
