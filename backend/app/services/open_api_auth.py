"""开放平台鉴权（piaozone 兼容）。

两段式：
  1. `POST /base/oauth/token`  body {client_id, timestamp, sign}
     sign = MD5(client_id + client_secret + timestamp)，小写十六进制
     → {errcode:"0000", description:"操作成功", access_token, token_type:"bearer", expires_in}
  2. 业务接口用 `?access_token=xxx` 传递。

设计要点：
  * token 存 DB（`open_api_tokens`），进程重启不失效——线上 expires_in=36 小时，
    调用方会缓存复用，内存态 token 一重启就全废。
  * 时间戳窗口校验：默认 ±15 分钟（`OPEN_API_TIMESTAMP_SKEW_SEC`），防重放。
    线上未见明确窗口，取一个宽松但有界的值；调不通时优先查客户端时钟。
  * 签名比较用 `hmac.compare_digest`，避免按字符短路的时序侧信道。
  * 错误码沿用线上外壳（errcode 非 "0000" 即失败），不抛 HTTP 4xx——
    生产调用方按 errcode 分支，HTTP 层始终 200。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.open_api_client import OpenApiClient, OpenApiToken

# ── 错误码（对齐线上外壳；"0000" = 成功）─────────────────────────────────────
ERR_OK = "0000"
ERR_INVALID_CLIENT = "4001"
ERR_INVALID_SIGN = "4002"
ERR_TIMESTAMP_SKEW = "4003"
ERR_INVALID_TOKEN = "4004"
ERR_TOKEN_EXPIRED = "4005"
ERR_TEMPLATE_NOT_FOUND = "4006"
ERR_TEMPLATE_FORBIDDEN = "4007"
ERR_NO_FILE = "4008"
ERR_PROCESS_FAILED = "5000"

TOKEN_TTL_SEC = 129600  # 36 小时，与线上 expires_in 一致
TIMESTAMP_SKEW_SEC = 900  # ±15 分钟


class OpenApiAuthError(Exception):
    """带 errcode 的鉴权失败（由路由转成线上格式的 200 响应）。"""

    def __init__(self, errcode: str, description: str):
        super().__init__(description)
        self.errcode = errcode
        self.description = description


def compute_sign(client_id: str, client_secret: str, timestamp: str) -> str:
    """sign = MD5(client_id + client_secret + timestamp)，小写十六进制。"""
    raw = f"{client_id}{client_secret}{timestamp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _check_timestamp(timestamp: str) -> None:
    try:
        ts = int(str(timestamp).strip())
    except (TypeError, ValueError):
        raise OpenApiAuthError(ERR_TIMESTAMP_SKEW, "timestamp must be a unix second") from None
    # 容忍毫秒级时间戳（13 位）——调用方常见误用，换算后再比
    if ts > 10_000_000_000:
        ts //= 1000
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - ts) > TIMESTAMP_SKEW_SEC:
        raise OpenApiAuthError(
            ERR_TIMESTAMP_SKEW,
            f"timestamp out of window (±{TIMESTAMP_SKEW_SEC}s); check client clock",
        )


def issue_token(db: Session, *, client_id: str, timestamp: str, sign: str) -> dict:
    """校验签名并签发 access_token。返回线上格式的响应体。"""
    client = (
        db.query(OpenApiClient)
        .filter(OpenApiClient.client_id == client_id, OpenApiClient.is_active.is_(True))
        .first()
    )
    if client is None:
        raise OpenApiAuthError(ERR_INVALID_CLIENT, "invalid or inactive client_id")

    _check_timestamp(timestamp)

    expected = compute_sign(client_id, client.client_secret, str(timestamp))
    if not hmac.compare_digest(expected, str(sign or "").lower()):
        raise OpenApiAuthError(ERR_INVALID_SIGN, "sign mismatch")

    token = secrets.token_hex(16)  # 32 位小写十六进制，与线上格式一致
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SEC)
    db.add(OpenApiToken(
        id=uuid.uuid4(),
        access_token=token,
        client_id=client_id,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    return {
        "errcode": ERR_OK,
        "description": "操作成功",
        "access_token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SEC,
    }


def resolve_token(db: Session, access_token: str | None) -> OpenApiClient:
    """access_token → OpenApiClient。失败抛 OpenApiAuthError。"""
    if not access_token:
        raise OpenApiAuthError(ERR_INVALID_TOKEN, "access_token is required")

    row = (
        db.query(OpenApiToken)
        .filter(OpenApiToken.access_token == access_token)
        .first()
    )
    if row is None:
        raise OpenApiAuthError(ERR_INVALID_TOKEN, "invalid access_token")

    expires_at = row.expires_at
    if expires_at.tzinfo is None:  # SQLite 取回的 naive datetime 视为 UTC
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise OpenApiAuthError(ERR_TOKEN_EXPIRED, "access_token has expired")

    client = (
        db.query(OpenApiClient)
        .filter(OpenApiClient.client_id == row.client_id,
                OpenApiClient.is_active.is_(True))
        .first()
    )
    if client is None:
        raise OpenApiAuthError(ERR_INVALID_CLIENT, "client is inactive")
    return client


def purge_expired_tokens(db: Session) -> int:
    """清理过期 token（可选维护动作）。返回删除条数。"""
    now = datetime.now(timezone.utc)
    n = db.query(OpenApiToken).filter(OpenApiToken.expires_at < now).delete()
    db.commit()
    return n
