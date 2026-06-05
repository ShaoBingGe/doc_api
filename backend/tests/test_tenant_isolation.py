"""
多租户数据隔离 + token 强制（TestClient，走完整 HTTP + 路由守卫）。

覆盖：
- 未带 token 访问数据端点 → 401
- 租户 A 建的 ApiDefinition：租户 B list 看不到 / get 404 / OCR versions 404
- 超级管理员跨租户可见全部
- 文档 list 按租户隔离；admin 看全部
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:  # lifespan → create_tables + ensure_tenant_columns + bootstrap
        yield c


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _login(client, email, password) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _make_tenant_admin(client, admin_h, suffix) -> dict:
    """super admin creates a tenant admin, returns that admin's login json."""
    email = f"ta-{suffix}@x.com"
    r = client.post(
        "/api/v1/admin/tenant-admins",
        headers=admin_h,
        json={"email": email, "password": "pass1234", "tenant_name": f"T-{suffix}"},
    )
    assert r.status_code == 201, r.text
    return _login(client, email, "pass1234")


def _create_api(client, headers, code) -> str:
    r = client.post(
        "/api/v1/api-definitions",
        headers=headers,
        json={
            "name": f"api-{code}",
            "api_code": code,
            "description": "",
            "response_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
            "processor_type": "mock",
            "model_name": "mock",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_unauthenticated_data_endpoints_401(client):
    assert client.get("/api/v1/api-definitions").status_code == 401
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/api-keys").status_code == 401


def test_api_definition_tenant_isolation(client):
    suffix = uuid.uuid4().hex[:6]
    admin = _login(client, "admin", "666666")
    admin_h = _auth(admin["access_token"])

    a = _make_tenant_admin(client, admin_h, f"a{suffix}")
    b = _make_tenant_admin(client, admin_h, f"b{suffix}")
    a_h = _auth(a["access_token"])
    b_h = _auth(b["access_token"])

    code = f"iso-{suffix}"
    api_id = _create_api(client, a_h, code)

    # A sees its own api in list + detail
    a_list = client.get("/api/v1/api-definitions", headers=a_h).json()["items"]
    assert any(x["id"] == api_id for x in a_list)
    assert client.get(f"/api/v1/api-definitions/{api_id}", headers=a_h).status_code == 200

    # B cannot see it (not in list, get → 404, OCR versions → 404)
    b_list = client.get("/api/v1/api-definitions", headers=b_h).json()["items"]
    assert all(x["id"] != api_id for x in b_list)
    assert client.get(f"/api/v1/api-definitions/{api_id}", headers=b_h).status_code == 404
    assert client.get(
        f"/api/v1/api-definitions/{api_id}/ocr-optimizer/versions", headers=b_h
    ).status_code == 404
    # B cannot delete or mutate A's api
    assert client.delete(f"/api/v1/api-definitions/{api_id}", headers=b_h).status_code == 404

    # Super admin sees it across tenants
    assert client.get(f"/api/v1/api-definitions/{api_id}", headers=admin_h).status_code == 200
    admin_list = client.get(
        "/api/v1/api-definitions?page_size=100", headers=admin_h
    ).json()["items"]
    assert any(x["id"] == api_id for x in admin_list)


def test_documents_tenant_isolation(client):
    suffix = uuid.uuid4().hex[:6]
    admin = _login(client, "admin", "666666")
    admin_h = _auth(admin["access_token"])
    a = _make_tenant_admin(client, admin_h, f"da{suffix}")
    b = _make_tenant_admin(client, admin_h, f"db{suffix}")
    a_h = _auth(a["access_token"])
    b_h = _auth(b["access_token"])

    # A uploads a document
    files = {"file": ("t.pdf", b"%PDF-1.4 minimal", "application/pdf")}
    r = client.post("/api/v1/documents/upload", headers=a_h, files=files)
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]

    # A sees it; B doesn't
    a_docs = client.get("/api/v1/documents", headers=a_h).json()["items"]
    assert any(d["id"] == doc_id for d in a_docs)
    b_docs = client.get("/api/v1/documents", headers=b_h).json()["items"]
    assert all(d["id"] != doc_id for d in b_docs)
    assert client.get(f"/api/v1/documents/{doc_id}", headers=b_h).status_code == 404

    # admin sees it
    assert client.get(f"/api/v1/documents/{doc_id}", headers=admin_h).status_code == 200


def test_api_key_tenant_isolation(client):
    suffix = uuid.uuid4().hex[:6]
    admin = _login(client, "admin", "666666")
    admin_h = _auth(admin["access_token"])
    a = _make_tenant_admin(client, admin_h, f"ka{suffix}")
    b = _make_tenant_admin(client, admin_h, f"kb{suffix}")
    a_h = _auth(a["access_token"])
    b_h = _auth(b["access_token"])

    r = client.post("/api/v1/api-keys", headers=a_h, json={"name": f"key-{suffix}"})
    assert r.status_code == 201, r.text
    key_id = r.json()["id"]

    a_keys = client.get("/api/v1/api-keys", headers=a_h).json()
    assert any(k["id"] == key_id for k in a_keys)
    b_keys = client.get("/api/v1/api-keys", headers=b_h).json()
    assert all(k["id"] != key_id for k in b_keys)
    # B cannot revoke A's key
    assert client.delete(f"/api/v1/api-keys/{key_id}", headers=b_h).status_code == 404
