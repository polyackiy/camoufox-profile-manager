"""Account administration commits safe activity and rejects viewers."""

import httpx
import pytest
from fastapi import Depends, FastAPI

from camoufox_pm.api import dependencies
from camoufox_pm.api.routes import access
from camoufox_pm.core import auth


@pytest.mark.asyncio
async def test_admin_mutations_audit_and_viewer_denial(storage, monkeypatch):
    monkeypatch.setattr(dependencies, "_storage_manager", storage)
    await storage.create_user("a", "admin", auth.hash_password("admin-secret"))
    await storage.create_user("v", "viewer", auth.hash_password("viewer-secret"), "viewer")
    admin_token = await auth.create_session(storage, "a")
    viewer_token = await auth.create_session(storage, "v")
    app = FastAPI()
    app.include_router(access.router, dependencies=[Depends(dependencies.require_auth)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(auth.SESSION_COOKIE, viewer_token)
        assert (await client.get("/access/users")).status_code == 403
        client.cookies.set(auth.SESSION_COOKIE, admin_token)
        response = await client.post(
            "/access/users",
            json={"username": "new", "password": "never-log-this-password", "role": "operator"},
        )
        assert response.status_code == 201
        assert (await storage.get_user_by_username("new"))["role"] == "operator"
        assert (await client.delete("/access/users/admin")).status_code == 409
        response = await client.get("/access/audit/export")
        assert response.status_code == 200
        assert "never-log-this-password" not in response.text
        assert "admin-secret" not in response.text
        assert "password_hash" not in response.text
        assert response.json()["events"][0]["action"] == "user.create"
        page = await client.get("/access/audit", params={"limit": 1})
        assert page.json()["total"] == 1


@pytest.mark.asyncio
async def test_first_account_requires_administrator_and_password_minimum(client):
    for role in ("operator", "viewer"):
        response = await client.post(
            "/api/v1/access/users",
            json={"username": "first", "password": "valid-password", "role": role},
        )
        assert response.status_code == 409
    response = await client.post(
        "/api/v1/access/users", json={"username": "first", "password": "x", "role": "administrator"}
    )
    assert response.status_code == 422
    response = await client.post(
        "/api/v1/access/users",
        json={"username": "first", "password": "valid-password", "role": "administrator"},
    )
    assert response.status_code == 201
