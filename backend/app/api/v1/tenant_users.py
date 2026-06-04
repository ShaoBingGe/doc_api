"""
用户管理员（tenant_admin）维护本租户普通用户的端点。

  GET  /tenant/users   本租户普通用户列表
  POST /tenant/users   新增普通用户（邮箱+可选密码）

更新 / 停用复用 /admin/users/{id}（service 层已按角色 + 租户做 scope 校验）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_roles
from app.models.user import UserRole
from app.schemas.auth import CreateNormalUserRequest, UserResponse
from app.services import user_service as svc

router = APIRouter(prefix="/tenant", tags=["Tenant · 普通用户"])

_tenant_admin = require_roles(UserRole.tenant_admin)


@router.get("/users", response_model=list[UserResponse], summary="本租户普通用户列表")
def list_users(db: Session = Depends(get_db), actor=Depends(_tenant_admin)):
    return svc.list_tenant_users(db, actor.tenant_id)


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新增普通用户",
)
def create_user(
    body: CreateNormalUserRequest,
    db: Session = Depends(get_db),
    actor=Depends(_tenant_admin),
):
    return svc.create_normal_user(
        db, actor.tenant_id, body.email, body.password, body.display_name
    )
