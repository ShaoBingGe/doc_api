"""
Public document extraction endpoint.
POST /api/v1/extract/{api_code}   — authenticated via X-API-Key header
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.deps import get_api_key_auth, get_db
from app.models.api_definition import ApiDefinition
from app.models.api_key import ApiKey
from app.schemas.extract import ExtractJsonRequest, ExtractResponse
from app.services import extract_service as svc
from app.services.extract_service import record_usage

router = APIRouter(prefix="/extract", tags=["Extract (Public API)"])


@router.post(
    "/{api_code}",
    response_model=ExtractResponse,
    summary="提取文档数据（公有 API）",
    description=(
        "通过 API Key 调用。支持三种输入：\n"
        "- `multipart/form-data` 上传 `file` 字段\n"
        "- JSON `{\"file_url\": \"https://...\"}`\n"
        "- JSON `{\"file_base64\": \"data:...;base64,...\"}`"
    ),
)
async def extract_document(
    api_code: str,
    request: Request,
    api_key: Annotated[ApiKey, Depends(get_api_key_auth)],
    db: Session = Depends(get_db),
) -> ExtractResponse:
    content_type = request.headers.get("content-type", "")
    file_bytes: bytes | None = None
    filename: str | None = None
    file_url: str | None = None
    file_base64: str | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload: UploadFile | None = form.get("file")  # type: ignore[assignment]
        if upload:
            file_bytes = await upload.read()
            filename = upload.filename or "upload"
    else:
        body_bytes = await request.body()
        if body_bytes:
            import json
            try:
                body = json.loads(body_bytes)
                file_url = body.get("file_url")
                file_base64 = body.get("file_base64")
            except (json.JSONDecodeError, AttributeError):
                pass

    client_ip = request.client.host if request.client else "unknown"

    start_ms = int(time.time() * 1000)
    status_code = 200
    try:
        result = svc.extract_document(
            db,
            api_code=api_code,
            api_key=api_key,
            file_bytes=file_bytes,
            filename=filename,
            file_url=file_url,
            file_base64=file_base64,
            request_ip=client_ip,
        )
    except Exception:
        status_code = 500
        raise
    finally:
        elapsed = int(time.time() * 1000) - start_ms
        # Write usage record (non-fatal)
        api_def = db.query(ApiDefinition).filter(ApiDefinition.api_code == api_code).first()
        if api_def:
            try:
                record_usage(
                    db,
                    api_def=api_def,
                    api_key=api_key,
                    request_id=result.request_id if status_code == 200 else __import__("uuid").uuid4(),
                    status_code=status_code,
                    latency_ms=elapsed,
                    tokens_used=0,
                    request_ip=client_ip,
                )
            except Exception:
                pass

    return result
