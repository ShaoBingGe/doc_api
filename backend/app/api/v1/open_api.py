"""开放平台端点（piaozone 兼容）。

路径与线上生产完全一致，**不挂在 /api/v1 前缀下**：

    POST /base/oauth/token
    POST /ai/knowledge/nlpService/document/analyze?access_token={token}

与既有的 `/api/v1/extract/{api_code}`（X-API-Key）并存：前者给外部客户，
后者给工作区/前端。两条路径共用同一套提取管线（extract_service）。

契约要点（照抄线上，勿改）：
  * 业务响应 HTTP 恒为 200，成败看 `errcode`（"0000" 成功）；
  * 请求头 `client-platform: common`；Content-Type `multipart/form-data`；
  * 表单字段：templateId / fileHash / file / clientId；
  * `templateId` 是数字模板号，映射到 ApiDefinition.external_template_id。
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.open_api_client import OpenApiClient
from app.services import extract_service as svc
from app.services import open_api_auth as auth
from app.services import open_api_mapper as mapper

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Open API (piaozone-compatible)"])


def _trace_id() -> str:
    """16 位小写十六进制，与线上 traceId 形态一致。"""
    return secrets.token_hex(8)


# ── 1. 取 access_token ────────────────────────────────────────────────────────

@router.post(
    "/base/oauth/token",
    summary="获取 access_token（开放平台）",
    description=(
        "sign = MD5(client_id + client_secret + timestamp)，小写十六进制。\n"
        "timestamp 为 unix 秒。成功返回 errcode=0000 与 access_token（36 小时有效）。"
    ),
)
def oauth_token(
    body: Annotated[dict, Body(...)],
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        payload = auth.issue_token(
            db,
            client_id=str(body.get("client_id") or ""),
            timestamp=str(body.get("timestamp") or ""),
            sign=str(body.get("sign") or ""),
        )
    except auth.OpenApiAuthError as exc:
        # 线上失败也走 200 + errcode，调用方不做 HTTP 分支
        return JSONResponse(
            status_code=200,
            content={"errcode": exc.errcode, "description": exc.description},
        )
    return JSONResponse(status_code=200, content=payload)


# ── 2. 文档解析 ───────────────────────────────────────────────────────────────

def _resolve_api_def(
    db: Session, *, template_id: str, client: OpenApiClient
) -> ApiDefinition:
    """templateId（数字）→ ApiDefinition，并校验该 client 有权访问。"""
    try:
        tid = int(str(template_id).strip())
    except (TypeError, ValueError):
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_NOT_FOUND, f"invalid templateId: {template_id!r}"
        ) from None

    api_def = (
        db.query(ApiDefinition)
        .filter(ApiDefinition.external_template_id == tid)
        .first()
    )
    if api_def is None:
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_NOT_FOUND, f"templateId {tid} not found"
        )
    if api_def.status == ApiDefinitionStatus.deprecated:
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_FORBIDDEN, f"templateId {tid} has been deprecated"
        )

    # 租户隔离：模板挂了租户时，只有同租户的 client 能调用。
    # 平台桶模板（tenant_id 为空）对所有已鉴权 client 开放（公共国家模板）。
    if api_def.tenant_id is not None and api_def.tenant_id != client.tenant_id:
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_FORBIDDEN,
            f"client {client.client_id} is not allowed to use templateId {tid}",
        )
    return api_def


@router.post(
    "/ai/knowledge/nlpService/document/analyze",
    summary="文档结构化解析（开放平台）",
    description=(
        "multipart/form-data：templateId / fileHash / file / clientId。\n"
        "HTTP 恒为 200，成败看 errcode（'0000' 成功）。"
    ),
)
async def analyze_document(
    request: Request,
    access_token: Annotated[str | None, Query()] = None,
    client_platform: Annotated[str | None, Header(alias="client-platform")] = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    trace_id = _trace_id()
    started = time.time()

    # 表单解析：字段名与线上一致，file 可缺省（由 errcode 兜底）
    template_id = file_hash = client_id = ""
    file_bytes: bytes | None = None
    filename: str | None = None
    try:
        form = await request.form()
        template_id = str(form.get("templateId") or "")
        file_hash = str(form.get("fileHash") or "")
        client_id = str(form.get("clientId") or "")
        upload = form.get("file")
        # 用 duck typing 而非 isinstance：starlette 的表单解析返回的是
        # starlette.datastructures.UploadFile，而 fastapi.UploadFile 是它的
        # **子类** —— isinstance(父类实例, 子类) 为 False，会把上传的文件误判成缺失。
        if upload is not None and hasattr(upload, "read"):
            file_bytes = await upload.read()
            filename = getattr(upload, "filename", None) or "upload"
    except Exception:  # noqa: BLE001 — 表单畸形不该 500
        logger.warning("analyze: malformed multipart body", exc_info=True)

    try:
        client = auth.resolve_token(db, access_token)
        # clientId 若同时出现在表单里，必须与 token 所属 client 一致，
        # 防止拿 A 的 token 冒充 B 提交
        if client_id and client_id != client.client_id:
            raise auth.OpenApiAuthError(
                auth.ERR_TEMPLATE_FORBIDDEN,
                "clientId does not match the authenticated access_token",
            )
        api_def = _resolve_api_def(db, template_id=template_id, client=client)
        if not file_bytes:
            raise auth.OpenApiAuthError(auth.ERR_NO_FILE, "file is required")
    except auth.OpenApiAuthError as exc:
        return JSONResponse(
            status_code=200,
            content=mapper.build_error(exc.errcode, exc.description, trace_id=trace_id),
        )

    # 复用既有提取管线（prompt 解析 / processor 兜底 / 审计一并沿用）
    try:
        result = svc.extract_document(
            db,
            api_code=api_def.api_code,
            api_key=None,  # 开放平台无 ApiKey，用量记录里 api_key_id 为 None
            file_bytes=file_bytes,
            filename=filename,
            request_ip=request.client.host if request.client else "unknown",
        )
        # 取 entities（全量票据）；data 只有首条，多票据文档会漏
        structured: Any = result.entities if result.entities else result.data
    except Exception as exc:  # noqa: BLE001 — 失败也走 errcode，不抛 500
        logger.exception("analyze failed: template=%s client=%s", template_id, client_id)
        return JSONResponse(
            status_code=200,
            content=mapper.build_error(
                auth.ERR_PROCESS_FAILED, f"analyze failed: {exc}", trace_id=trace_id
            ),
        )

    # docPages 报**原文档实际页数**（不是送模型的页数），调用方据此判断是否被截断
    doc_pages = _count_pages(file_bytes, filename)
    page_limit = _page_limit(api_def.processor_type)
    payload = mapper.build_response(
        structured,
        trace_id=trace_id,
        doc_pages=doc_pages,
        source_file_hash=file_hash,
        description=(
            mapper.TRUNCATED_DESC.format(limit=page_limit)
            if page_limit and doc_pages > page_limit
            else mapper.SUCCESS_DESC
        ),
    )
    if page_limit and doc_pages > page_limit:
        logger.info(
            "analyze: 文档 %d 页超过上限 %d，仅识别前 %d 页（template=%s trace=%s）",
            doc_pages, page_limit, page_limit, template_id, trace_id,
        )

    # 用量记录（非致命）
    try:
        svc.record_usage(
            db,
            api_def=api_def,
            api_key=None,
            request_id=uuid.uuid4(),
            status_code=200,
            latency_ms=int((time.time() - started) * 1000),
            tokens_used=result.metadata.tokens_used,
            request_ip=request.client.host if request.client else "unknown",
        )
    except Exception:  # noqa: BLE001
        logger.debug("record_usage failed (non-fatal)", exc_info=True)

    return JSONResponse(status_code=200, content=payload)


def _page_limit(processor_type: str | None) -> int | None:
    """该 API 实际生效的单次页数上限。

    取执行层的真实上限而非硬编码，避免 processor 改了上限、这里的提示语还停在旧值。
    解析不出（如 mock / gemini 无此限制）时返回 None，表示不提示截断。
    """
    from app.processors.factory import ProcessorFactory

    try:
        proc_used, _ = ProcessorFactory.resolve_spec(processor_type, None)
    except Exception:  # noqa: BLE001
        proc_used = processor_type
    if (proc_used or "").lower() == "qwen":
        from app.processors.qwen_processor import MAX_PAGES

        return MAX_PAGES
    return None


def _count_pages(file_bytes: bytes | None, filename: str | None) -> int:
    """docPages：PDF 数页数，图片恒为 1。解析失败回退 1，不影响主流程。"""
    if not file_bytes:
        return 0
    name = (filename or "").lower()
    if not name.endswith(".pdf"):
        return 1
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return len(doc)
    except Exception:  # noqa: BLE001
        return 1
