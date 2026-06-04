"""
角色与权限管理 — 登录（密码/验证码）、超级/系统/用户管理员 + 普通用户的
创建与 scope 校验、改密码。用 TestClient 走完整 HTTP 路径（含 require_roles 守卫）。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # conftest already forces the throwaway test DB + builds the schema.
    from app.main import app

    with TestClient(app) as c:  # lifespan → bootstrap_super_admin
        yield c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email, password) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


# ── service-level unit checks (db_session fixture) ────────────────────────────

def test_bootstrap_idempotent(db_session):
    from app.services.user_service import bootstrap_super_admin

    a = bootstrap_super_admin(db_session)
    b = bootstrap_super_admin(db_session)
    assert a.id == b.id
    assert a.role == "super_admin"


def test_password_and_code_login_paths(db_session):
    from app.core.exceptions import AuthenticationError
    from app.services import user_service as svc

    svc.bootstrap_super_admin(db_session)
    # super admin password login works
    sa = svc.authenticate_password(db_session, "admin", "666666")
    assert sa.role == "super_admin"
    # wrong password rejected
    with pytest.raises(AuthenticationError):
        svc.authenticate_password(db_session, "admin", "nope")

    ta = svc.create_tenant_admin(db_session, f"ta-{uuid.uuid4().hex[:6]}@x.com", "pass1234", f"T-{uuid.uuid4().hex[:6]}", None)
    nu_email = f"nu-{uuid.uuid4().hex[:6]}@x.com"
    svc.create_normal_user(db_session, ta.tenant_id, nu_email, None, None)

    # code login requires preexisting normal user + correct code
    assert svc.authenticate_code(db_session, nu_email, "666666").email == nu_email
    with pytest.raises(AuthenticationError):
        svc.authenticate_code(db_session, nu_email, "000000")  # bad code
    with pytest.raises(AuthenticationError):
        svc.authenticate_code(db_session, "ghost@x.com", "666666")  # unknown email
    # tenant admin must NOT log in via code path
    with pytest.raises(AuthenticationError):
        svc.authenticate_code(db_session, ta.email, "666666")


def test_scope_enforcement(db_session):
    from app.core.exceptions import AuthorizationError
    from app.services import user_service as svc

    svc.bootstrap_super_admin(db_session)
    sysadmin = svc.create_system_admin(db_session, f"s-{uuid.uuid4().hex[:6]}@x.com", "pass1234", None)
    ta = svc.create_tenant_admin(db_session, f"ta-{uuid.uuid4().hex[:6]}@x.com", "pass1234", f"T-{uuid.uuid4().hex[:6]}", None)
    nu = svc.create_normal_user(db_session, ta.tenant_id, f"nu-{uuid.uuid4().hex[:6]}@x.com", None, None)

    # tenant admin may manage own normal user
    svc.update_user(db_session, ta, nu.id, display_name="renamed")
    # tenant admin may NOT manage a system admin
    with pytest.raises(AuthorizationError):
        svc.update_user(db_session, ta, sysadmin.id, is_active=False)
    # system admin may NOT manage a normal user (only tenant admins)
    with pytest.raises(AuthorizationError):
        svc.update_user(db_session, sysadmin, nu.id, is_active=False)


# ── HTTP-level checks (TestClient + role guards) ──────────────────────────────

def test_http_full_flow(client):
    # super admin login
    j = _login(client, "admin", "666666")
    assert j["user"]["role"] == "super_admin"
    sh = _auth(j["access_token"])

    # me
    assert client.get("/api/v1/auth/me", headers=sh).status_code == 200
    # unauthenticated guarded endpoint → 401
    assert client.get("/api/v1/admin/system-admins").status_code == 401

    suffix = uuid.uuid4().hex[:6]
    # super creates a system admin
    r = client.post(
        "/api/v1/admin/system-admins",
        headers=sh,
        json={"email": f"sys-{suffix}@x.com", "password": "pass1234"},
    )
    assert r.status_code == 201, r.text

    # super creates a tenant admin (+tenant)
    r = client.post(
        "/api/v1/admin/tenant-admins",
        headers=sh,
        json={"email": f"ta-{suffix}@x.com", "password": "pass1234", "tenant_name": f"Acme-{suffix}"},
    )
    assert r.status_code == 201, r.text

    # tenant admin logs in, has a tenant, creates a normal user
    tj = _login(client, f"ta-{suffix}@x.com", "pass1234")
    assert tj["tenant"] and tj["tenant"]["name"] == f"Acme-{suffix}"
    th = _auth(tj["access_token"])
    r = client.post("/api/v1/tenant/users", headers=th, json={"email": f"nu-{suffix}@x.com"})
    assert r.status_code == 201, r.text
    assert len(client.get("/api/v1/tenant/users", headers=th).json()) == 1

    # normal user code login
    assert client.post(
        "/api/v1/auth/login/code", json={"email": f"nu-{suffix}@x.com", "code": "666666"}
    ).status_code == 200
    assert client.post(
        "/api/v1/auth/login/code", json={"email": f"nu-{suffix}@x.com", "code": "bad"}
    ).status_code == 401

    # system admin cannot create system admins (403) but can create tenant admins (201)
    sj = _login(client, f"sys-{suffix}@x.com", "pass1234")
    ssh = _auth(sj["access_token"])
    assert client.post(
        "/api/v1/admin/system-admins", headers=ssh,
        json={"email": f"sys2-{suffix}@x.com", "password": "pass1234"},
    ).status_code == 403
    assert client.post(
        "/api/v1/admin/tenant-admins", headers=ssh,
        json={"email": f"ta2-{suffix}@x.com", "password": "pass1234", "tenant_name": f"Beta-{suffix}"},
    ).status_code == 201

    # change own password
    assert client.post(
        "/api/v1/auth/change-password", headers=th,
        json={"old_password": "pass1234", "new_password": "newpass1"},
    ).status_code == 204
    assert _login(client, f"ta-{suffix}@x.com", "newpass1")["user"]["role"] == "tenant_admin"
