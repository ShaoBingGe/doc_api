"""
Authentication endpoints — login (password / code), me, change-password.

两个登录入口（前端区分），后端用两个端点：
  POST /auth/login        密码登录（super/system/tenant admin）
  POST /auth/login/code   验证码登录（普通用户）
  GET  /auth/me           当前登录身份
  POST /auth/change-password  改自己密码
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.security import create_access_token
from app.schemas.auth import (
    ChangePasswordRequest,
    CodeLoginRequest,
    LoginResponse,
    PasswordLoginRequest,
    UserResponse,
)
from app.services import user_service as svc

router = APIRouter(prefix="/auth", tags=["Auth"])


def _login_response(db: Session, user) -> LoginResponse:
    token = create_access_token(
        subject=str(user.id),
        role=user.role,
        tenant_id=str(user.tenant_id) if user.tenant_id else None,
    )
    tenant = svc.tenant_brief(db, user.tenant_id)
    return LoginResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
        tenant=tenant,  # pydantic from_attributes handles None / ORM
    )


@router.post("/login", response_model=LoginResponse, summary="密码登录（管理员/用户管理员）")
def login_password(body: PasswordLoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = svc.authenticate_password(db, body.email, body.password)
    return _login_response(db, user)


@router.post("/login/code", response_model=LoginResponse, summary="验证码登录（普通用户）")
def login_code(body: CodeLoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = svc.authenticate_code(db, body.email, body.code)
    return _login_response(db, user)


@router.get("/me", response_model=LoginResponse, summary="当前登录身份")
def me(db: Session = Depends(get_db), user=Depends(get_current_user)) -> LoginResponse:
    # reuse the login response shape (no new token issued)
    tenant = svc.tenant_brief(db, user.tenant_id)
    return LoginResponse(
        access_token="",  # client already holds its token
        user=UserResponse.model_validate(user),
        tenant=tenant,
    )


@router.post("/change-password", status_code=204, summary="修改自己的密码")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
) -> None:
    svc.change_own_password(db, user, body.old_password, body.new_password)
