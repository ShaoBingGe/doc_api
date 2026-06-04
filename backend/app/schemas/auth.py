"""
Pydantic schemas for authentication + user/tenant management.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Auth: login / token / me ──────────────────────────────────────────────────

class PasswordLoginRequest(BaseModel):
    """密码登录 — 用于 super_admin / system_admin / tenant_admin。"""
    email: str = Field(..., min_length=1, max_length=320, description="登录标识/邮箱")
    password: str = Field(..., min_length=1, max_length=128)


class CodeLoginRequest(BaseModel):
    """验证码登录 — 普通用户（邮箱必须预先存在）。"""
    email: str = Field(..., min_length=3, max_length=320)
    code: str = Field(..., min_length=1, max_length=16)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=4, max_length=128)


class TenantBrief(BaseModel):
    id: uuid.UUID
    name: str
    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None = None
    role: str
    tenant_id: uuid.UUID | None = None
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    tenant: TenantBrief | None = None


# ── User management: create / update ──────────────────────────────────────────

class CreateSystemAdminRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=4, max_length=128)
    display_name: str | None = Field(default=None, max_length=256)


class CreateTenantAdminRequest(BaseModel):
    """创建用户管理员；同时创建（或复用）其租户。"""
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=4, max_length=128)
    tenant_name: str = Field(..., min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)


class CreateNormalUserRequest(BaseModel):
    """用户管理员在本租户内创建普通用户。"""
    email: str = Field(..., min_length=3, max_length=320)
    password: str | None = Field(
        default=None, max_length=128,
        description="可选；普通用户当前以验证码登录，密码留作未来用",
    )
    display_name: str | None = Field(default=None, max_length=256)


class UpdateUserRequest(BaseModel):
    """通用更新：改密码 / 改显示名 / 停用启用。"""
    password: str | None = Field(default=None, min_length=4, max_length=128)
    display_name: str | None = Field(default=None, max_length=256)
    is_active: bool | None = None


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime
    admin_count: int = 0
    user_count: int = 0
    model_config = ConfigDict(from_attributes=True)
