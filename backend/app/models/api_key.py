"""
ApiKey ORM model.

存储 API 密钥的安全信息（只存哈希，不存明文）。
密钥格式：sk- + 32字节随机 Base62 编码。
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class ApiKey(UUIDMixin, Base):
    __tablename__ = "api_keys"

    # ── ownership ──────────────────────────────────────────────────────────
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, comment="FK → organizations（原型阶段可为 None）"
    )

    # ── identity ───────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="用户自定义名称，如 'Production Key'"
    )

    # ── security ───────────────────────────────────────────────────────────
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="SHA-256 哈希后的密钥，用于认证校验",
    )
    key_prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="密钥前几位明文，用于展示（如 'sk-AbCd...'），不含完整密钥",
    )

    # ── permissions & limits ───────────────────────────────────────────────
    scopes: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        default=lambda: ["extract"],
        comment="权限范围列表，如 ['extract', 'templates:read']",
    )
    rate_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, comment="每分钟最大调用次数"
    )

    # ── lifecycle ──────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否有效；吊销时置为 False"
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="过期时间，None 表示永不过期"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="创建时间",
    )
