"""
User / Tenant ORM models — 角色与权限管理（多租户）。

角色层级
--------
super_admin   超级管理员：安装时创建（默认 admin / 666666）。可创建系统管理员、
              维护用户管理员（租户管理员），可进入国家模板 / 黄金种子优化平台。
system_admin  系统管理员：由超级管理员创建。可维护用户管理员，可进入模板优化平台。
tenant_admin  用户管理员（租户管理员）：由 super/system admin 核发邮箱+密码。
              可在本租户内增删改普通用户、改自己密码。隶属一个 Tenant。
normal_user   普通用户：由本租户的用户管理员创建。用「邮箱+验证码」登录。隶属一个 Tenant。

设计说明
--------
- 单 `users` 表 + `role` 枚举 + 可空 `tenant_id`，避免多张身份表。
- `email` 列同时承担「登录标识」职责：super_admin 存登录名（如 "admin"），
  其余角色存真实邮箱。全表唯一。
- `password_hash` 对普通用户可为空（其登录走验证码）。
- 软删除/停用统一用 `is_active`，不物理删除（审计资产；与项目其余表一致）。
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class UserRole(str, Enum):
    super_admin = "super_admin"      # 超级管理员（安装创建，全局唯一入口）
    system_admin = "system_admin"    # 系统管理员（平台侧，模板/黄金种子）
    tenant_admin = "tenant_admin"    # 用户管理员（租户内管理普通用户）
    normal_user = "normal_user"      # 普通用户（验证码登录）


# 可进入「国家模板 / 黄金种子 / 优化迭代」平台的角色
PLATFORM_ROLES = frozenset({UserRole.super_admin, UserRole.system_admin})
# 属于某个租户的角色（必须有 tenant_id）
TENANT_ROLES = frozenset({UserRole.tenant_admin, UserRole.normal_user})


class Tenant(UUIDMixin, TimestampMixin, Base):
    """租户 —— 一个客户组织。下挂一个或多个用户管理员 + 多个普通用户。"""

    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("name", name="uq_tenants_name"),
    )

    name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="租户名称（唯一）"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="停用时置 False"
    )


class User(UUIDMixin, TimestampMixin, Base):
    """统一用户表 —— 用 role 区分四种身份。"""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
    )

    # ── identity / login ───────────────────────────────────────────────────
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        comment="登录标识（唯一）。super_admin 存登录名如 'admin'，其余存邮箱",
    )
    display_name: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True, comment="显示名"
    )
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        comment="bcrypt 哈希。普通用户走验证码登录时可为空",
    )

    # ── role & tenant ──────────────────────────────────────────────────────
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=UserRole.normal_user.value,
        comment="super_admin|system_admin|tenant_admin|normal_user",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=True,
        comment="tenant_admin / normal_user 必填；平台管理员为空",
    )

    # ── lifecycle ──────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="停用时置 False（不物理删除）"
    )

    # ── helpers ────────────────────────────────────────────────────────────
    @property
    def role_enum(self) -> UserRole:
        return UserRole(self.role)

    @property
    def is_platform_admin(self) -> bool:
        return self.role_enum in PLATFORM_ROLES
