"""
平台管理员的用户管理端点（super_admin / system_admin）。

  /admin/system-admins   超级管理员维护系统管理员
  /admin/tenant-admins   超级 + 系统管理员维护用户管理员（含其租户）
  /admin/tenants         租户列表（含计数）
  /admin/users/{id}      通用更新 / 停用（按角色 scope 校验，在 service 层）
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_roles
from app.models.user import UserRole
from app.schemas.auth import (
    CreateSystemAdminRequest,
    CreateTenantAdminRequest,
    TenantResponse,
    UpdateUserRequest,
    UserResponse,
)
from app.services import user_service as svc

router = APIRouter(prefix="/admin", tags=["Admin · 用户管理"])

# role guards
_super_only = require_roles(UserRole.super_admin)
_platform_admin = require_roles(UserRole.super_admin, UserRole.system_admin)


# ── system admins (super only) ───────────────────────────────────────────────

@router.get("/system-admins", response_model=list[UserResponse], summary="系统管理员列表")
def list_system_admins(db: Session = Depends(get_db), _=Depends(_super_only)):
    return svc.list_users_by_role(db, UserRole.system_admin)


@router.post(
    "/system-admins",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建系统管理员（仅超级管理员）",
)
def create_system_admin(
    body: CreateSystemAdminRequest,
    db: Session = Depends(get_db),
    _=Depends(_super_only),
):
    return svc.create_system_admin(db, body.email, body.password, body.display_name)


# ── tenant admins + tenants (super + system) ─────────────────────────────────

@router.get("/tenants", response_model=list[TenantResponse], summary="租户列表（含计数）")
def list_tenants(db: Session = Depends(get_db), _=Depends(_platform_admin)):
    return svc.list_tenants(db)


@router.get("/tenant-admins", response_model=list[UserResponse], summary="用户管理员列表")
def list_tenant_admins(db: Session = Depends(get_db), _=Depends(_platform_admin)):
    return svc.list_users_by_role(db, UserRole.tenant_admin)


@router.post(
    "/tenant-admins",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建用户管理员（含其租户）",
)
def create_tenant_admin(
    body: CreateTenantAdminRequest,
    db: Session = Depends(get_db),
    _=Depends(_platform_admin),
):
    return svc.create_tenant_admin(
        db, body.email, body.password, body.tenant_name, body.display_name
    )


# ── generic update / deactivate (scope enforced in service) ──────────────────

@router.patch("/users/{user_id}", response_model=UserResponse, summary="更新用户（改密码/停用等）")
def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    db: Session = Depends(get_db),
    actor=Depends(get_current_user),
):
    return svc.update_user(
        db,
        actor,
        user_id,
        password=body.password,
        display_name=body.display_name,
        is_active=body.is_active,
    )


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="停用用户（软删除，保留审计）",
)
def deactivate_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(get_current_user),
):
    svc.deactivate_user(db, actor, user_id)
