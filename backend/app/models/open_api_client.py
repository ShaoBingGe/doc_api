"""OpenApiClient / OpenApiToken ORM models — 对外开放平台（piaozone 兼容）凭证。

与 `ApiKey`（单密钥 `sk-xxx` + `X-API-Key` 头）不同，开放平台走的是
**client_id + client_secret 换发 access_token** 的两段式：

    POST /base/oauth/token   {client_id, timestamp, sign}  → access_token
    POST /ai/knowledge/nlpService/document/analyze?access_token=xxx

sign = MD5(client_id + client_secret + timestamp)，小写十六进制。
两套凭证并存：工作区/前端继续用 X-API-Key，外部客户走开放平台。

client_secret 只存哈希（与 ApiKey 一致，明文仅创建时返回一次）。
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class OpenApiClient(UUIDMixin, TimestampMixin, Base):
    """开放平台接入方（一个客户 = 一个 client_id）。"""

    __tablename__ = "open_api_clients"

    # ── identity ───────────────────────────────────────────────────────────
    client_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
        comment="对外的接入标识，如 'TN_RACzZVvvVh7MHg2xT'；业务请求的 clientId 与之相同",
    )
    client_secret_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="SHA-256(client_secret)。签名校验需要明文密钥，故另存 secret_enc",
    )
    client_secret: Mapped[str] = mapped_column(
        String(128), nullable=False, default="",
        comment=(
            "client_secret 明文。签名 MD5(client_id+secret+timestamp) 必须在服务端"
            "复算，无法只凭哈希验证；生产应改为 KMS/密钥库托管。"
        ),
    )
    name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="客户名称，如 'Chinkin'",
    )

    # ── ownership ──────────────────────────────────────────────────────────
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, index=True,
        comment="归属租户 FK → tenants.id；决定该 client 能访问哪些 API",
    )

    # ── lifecycle ──────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="停用置 False，不物理删除（审计资产）",
    )


class OpenApiToken(UUIDMixin, Base):
    """已签发的 access_token（DB 存储，进程重启不失效）。"""

    __tablename__ = "open_api_tokens"

    access_token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
        comment="32 位小写十六进制，与生产格式一致",
    )
    client_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="签发给哪个 client",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时刻（签发 + expires_in）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(),
    )


Index("ix_open_api_tokens_client_expires", OpenApiToken.client_id, OpenApiToken.expires_at)
