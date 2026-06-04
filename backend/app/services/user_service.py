"""
User / tenant / auth service.

职责
----
- bootstrap_super_admin   : 安装/启动时确保超级管理员存在（默认 admin / 666666）
- authenticate_password   : 密码登录（super/system/tenant admin）
- authenticate_code       : 验证码登录（普通用户，邮箱须预先存在）
- change_password         : 改自己密码
- 系统管理员 CRUD          : 仅 super_admin
- 用户管理员(+租户) CRUD    : super_admin / system_admin
- 普通用户 CRUD            : tenant_admin（限本租户）

权限校验放在路由层（require_roles）；本服务再补一层「越权访问目标」的硬校验
（如租户管理员只能动本租户的用户）。所有失败抛 AppError 子类（自动转 4xx）。
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.security import hash_password, verify_password
from app.models.user import TENANT_ROLES, Tenant, User, UserRole


# ── normalization ─────────────────────────────────────────────────────────────

def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


# ── bootstrap ─────────────────────────────────────────────────────────────────

def bootstrap_super_admin(db: Session) -> User:
    """
    Ensure a super_admin exists. Idempotent — safe to call on every boot.
    Uses SUPER_ADMIN_USERNAME / SUPER_ADMIN_PASSWORD from settings.
    Does NOT reset the password if the account already exists.
    """
    s = get_settings()
    login = _norm_email(s.SUPER_ADMIN_USERNAME)

    existing = (
        db.query(User)
        .filter(User.role == UserRole.super_admin.value)
        .first()
    )
    if existing is not None:
        return existing

    # also guard against email collision (e.g. someone took "admin")
    if db.query(User).filter(User.email == login).first() is not None:
        raise ConflictError(f"无法创建超级管理员：登录名 {login!r} 已被占用")

    admin = User(
        email=login,
        display_name="超级管理员",
        password_hash=hash_password(s.SUPER_ADMIN_PASSWORD),
        role=UserRole.super_admin.value,
        tenant_id=None,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


# ── authentication ────────────────────────────────────────────────────────────

def authenticate_password(db: Session, email: str, password: str) -> User:
    """Password login for super/system/tenant admin. Raises on failure."""
    login = _norm_email(email)
    user = db.query(User).filter(User.email == login).first()
    if user is None or not user.is_active:
        raise AuthenticationError("账号不存在或已停用")
    if user.role == UserRole.normal_user.value:
        # 普通用户不走密码入口
        raise AuthenticationError("该账号请使用「邮箱+验证码」登录")
    if not verify_password(password, user.password_hash):
        raise AuthenticationError("邮箱或密码错误")
    return user


def authenticate_code(db: Session, email: str, code: str) -> User:
    """
    Verification-code login for normal users. The email MUST already exist as a
    normal_user (created by a tenant admin). Code must equal the configured
    NORMAL_USER_LOGIN_CODE.
    """
    s = get_settings()
    if code.strip() != s.NORMAL_USER_LOGIN_CODE:
        raise AuthenticationError("验证码错误")

    login = _norm_email(email)
    user = db.query(User).filter(User.email == login).first()
    if user is None or user.role != UserRole.normal_user.value:
        raise AuthenticationError("账号不存在，请联系您的管理员开通")
    if not user.is_active:
        raise AuthenticationError("账号已停用")
    return user


def change_own_password(db: Session, user: User, old_password: str, new_password: str) -> None:
    if not verify_password(old_password, user.password_hash):
        raise AuthenticationError("原密码错误")
    user.password_hash = hash_password(new_password)
    db.commit()


# ── helpers ───────────────────────────────────────────────────────────────────

def _ensure_email_free(db: Session, email: str) -> None:
    if db.query(User).filter(User.email == email).first() is not None:
        raise ConflictError(f"邮箱 {email!r} 已被占用")


def _get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise NotFoundError("用户不存在")
    return user


def tenant_brief(db: Session, tenant_id: uuid.UUID | None) -> Tenant | None:
    if tenant_id is None:
        return None
    return db.query(Tenant).filter(Tenant.id == tenant_id).first()


# ── system admins (super_admin only) ─────────────────────────────────────────

def list_users_by_role(db: Session, role: UserRole) -> list[User]:
    return (
        db.query(User)
        .filter(User.role == role.value)
        .order_by(User.created_at.desc())
        .all()
    )


def create_system_admin(db: Session, email: str, password: str, display_name: str | None) -> User:
    email = _norm_email(email)
    _ensure_email_free(db, email)
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        role=UserRole.system_admin.value,
        tenant_id=None,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ── tenant admins + tenants (super_admin / system_admin) ─────────────────────

def list_tenants(db: Session) -> list[dict]:
    """Tenants with admin/user counts (for the management table)."""
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    out: list[dict] = []
    for t in tenants:
        admin_count = (
            db.query(User)
            .filter(User.tenant_id == t.id, User.role == UserRole.tenant_admin.value)
            .count()
        )
        user_count = (
            db.query(User)
            .filter(User.tenant_id == t.id, User.role == UserRole.normal_user.value)
            .count()
        )
        out.append(
            {
                "id": t.id,
                "name": t.name,
                "is_active": t.is_active,
                "created_at": t.created_at,
                "admin_count": admin_count,
                "user_count": user_count,
            }
        )
    return out


def create_tenant_admin(
    db: Session, email: str, password: str, tenant_name: str, display_name: str | None
) -> User:
    """Create a tenant admin, creating (or reusing) the named tenant."""
    email = _norm_email(email)
    tenant_name = tenant_name.strip()
    if not tenant_name:
        raise ValidationError("租户名称不能为空")
    _ensure_email_free(db, email)

    tenant = db.query(Tenant).filter(Tenant.name == tenant_name).first()
    if tenant is None:
        tenant = Tenant(name=tenant_name, is_active=True)
        db.add(tenant)
        db.flush()  # get tenant.id

    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        role=UserRole.tenant_admin.value,
        tenant_id=tenant.id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ── normal users (tenant_admin, scoped to own tenant) ────────────────────────

def list_tenant_users(db: Session, tenant_id: uuid.UUID) -> list[User]:
    return (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.role == UserRole.normal_user.value)
        .order_by(User.created_at.desc())
        .all()
    )


def create_normal_user(
    db: Session,
    tenant_id: uuid.UUID,
    email: str,
    password: str | None,
    display_name: str | None,
) -> User:
    if tenant_id is None:
        raise ValidationError("当前管理员未关联租户，无法创建普通用户")
    email = _norm_email(email)
    _ensure_email_free(db, email)
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password) if password else None,
        role=UserRole.normal_user.value,
        tenant_id=tenant_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ── generic update / deactivate, with scope enforcement ──────────────────────

def update_user(
    db: Session,
    actor: User,
    target_id: uuid.UUID,
    *,
    password: str | None = None,
    display_name: str | None = None,
    is_active: bool | None = None,
) -> User:
    """
    Update a user. Scope rules:
      - super_admin     : may update system_admin / tenant_admin (not other supers)
      - system_admin    : may update tenant_admin
      - tenant_admin    : may update normal_user IN OWN tenant
    """
    target = _get_user_or_404(db, target_id)
    _authorize_manage(actor, target)

    if password is not None:
        target.password_hash = hash_password(password)
    if display_name is not None:
        target.display_name = display_name
    if is_active is not None:
        target.is_active = is_active
    db.commit()
    db.refresh(target)
    return target


def deactivate_user(db: Session, actor: User, target_id: uuid.UUID) -> None:
    """Soft-delete (is_active=False) — never physical delete (审计资产)."""
    target = _get_user_or_404(db, target_id)
    _authorize_manage(actor, target)
    target.is_active = False
    db.commit()


def _authorize_manage(actor: User, target: User) -> None:
    actor_role = actor.role_enum
    target_role = target.role_enum

    if actor.id == target.id:
        raise ValidationError("请使用「修改密码」管理自己的账号")

    if actor_role == UserRole.super_admin:
        if target_role == UserRole.super_admin:
            raise AuthorizationError("不能操作其他超级管理员")
        return
    if actor_role == UserRole.system_admin:
        if target_role != UserRole.tenant_admin:
            raise AuthorizationError("系统管理员仅能维护用户管理员")
        return
    if actor_role == UserRole.tenant_admin:
        if target_role != UserRole.normal_user or target.tenant_id != actor.tenant_id:
            raise AuthorizationError("只能管理本租户的普通用户")
        return
    raise AuthorizationError("无权限")
