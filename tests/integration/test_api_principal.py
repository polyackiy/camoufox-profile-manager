"""Real ASGI authentication produces identity without retaining credentials."""

from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from camoufox_pm.api import dependencies
from camoufox_pm.api.principal import Principal
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth


@pytest.fixture
async def principal_client(storage, monkeypatch):
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(dependencies, "_storage_manager", storage)
    app = FastAPI()
    observed = []

    @app.middleware("http")
    async def observe_identity(request, call_next):
        response = await call_next(request)
        observed.append(getattr(request.state, "principal", None))
        return response

    async def probe(request: Request, principal: Principal = Depends(dependencies.require_auth)):
        assert principal is request.state.principal
        with pytest.raises(FrozenInstanceError):
            principal.username = "changed"
        return asdict(principal)

    app.add_api_route("/probe", probe)
    for prefix in ("/api", "/api/v1"):
        for suffix in ("", "/cancel", "/apply", "/resume", "/rollback", "/download"):
            app.add_api_route(f"{prefix}/system/update-recovery{suffix}", probe)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, observed
    finally:
        get_settings.cache_clear()


async def add_session(storage, *, expired=False):
    user_id = auth.new_user_id()
    await storage.create_user(user_id, "alice", "unused-password-hash")
    token = auth.new_session_token()
    await storage.create_session(
        auth.hash_token(token),
        user_id,
        datetime.now() + timedelta(hours=-1 if expired else 1),
    )
    return token


@pytest.mark.asyncio
async def test_anonymous_identity_preserves_open_local_default(principal_client):
    client, observed = principal_client
    response = await client.get("/probe")
    assert response.status_code == 200
    assert response.json() == {"kind": "anonymous_local", "username": None, "role": "administrator"}
    assert observed == [Principal("anonymous_local")]


@pytest.mark.asyncio
async def test_legacy_key_identity_has_no_secret_or_session_username(
    principal_client, storage, monkeypatch
):
    client, observed = principal_client
    token = await add_session(storage)
    monkeypatch.setenv("CPM_API_KEY", "private-key-value")
    get_settings.cache_clear()
    response = await client.get(
        "/probe", headers={"X-API-Key": "private-key-value", "Cookie": f"cpm_session={token}"}
    )
    assert response.json() == {"kind": "legacy_api_key", "username": None, "role": "administrator"}
    assert observed == [Principal("legacy_api_key")]
    assert {field.name for field in fields(observed[0])} == {"kind", "username", "role"}
    assert token not in repr(observed[0]) and "private-key-value" not in repr(observed[0])


@pytest.mark.asyncio
async def test_valid_session_identity_uses_authenticated_username(principal_client, storage):
    client, observed = principal_client
    token = await add_session(storage)
    response = await client.get("/probe", headers={"Cookie": f"cpm_session={token}"})
    assert response.status_code == 200
    assert response.json() == {"kind": "session", "username": "alice", "role": "administrator"}
    assert observed == [Principal("session", username="alice")]
    assert token not in repr(observed[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"Cookie": "cpm_session=forged"}])
async def test_rejected_request_does_not_acquire_identity(principal_client, storage, headers):
    client, observed = principal_client
    await add_session(storage)
    response = await client.get("/probe", headers=headers)
    assert response.status_code == 401
    assert observed == [None]


@pytest.mark.asyncio
async def test_expired_session_rejected_and_removed_on_ordinary_request(principal_client, storage):
    client, observed = principal_client
    token = await add_session(storage, expired=True)
    response = await client.get("/probe", headers={"Cookie": f"cpm_session={token}"})
    assert response.status_code == 401
    assert observed == [None]
    assert await storage.get_session(auth.hash_token(token)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
@pytest.mark.parametrize("suffix", ["", "/cancel", "/apply", "/resume", "/rollback"])
async def test_recovery_auth_remains_read_only_for_expired_session(
    principal_client, storage, prefix, suffix
):
    client, observed = principal_client
    token = await add_session(storage, expired=True)
    response = await client.get(
        f"{prefix}/system/update-recovery{suffix}", headers={"Cookie": f"cpm_session={token}"}
    )
    assert response.status_code == 401
    assert observed == [None]
    assert await storage.get_session(auth.hash_token(token)) is not None


@pytest.mark.asyncio
async def test_download_is_not_a_read_only_recovery_auth_exception(principal_client, storage):
    client, observed = principal_client
    token = await add_session(storage, expired=True)
    response = await client.get(
        "/api/v1/system/update-recovery/download", headers={"Cookie": f"cpm_session={token}"}
    )
    assert response.status_code == 401
    assert observed == [None]
    assert await storage.get_session(auth.hash_token(token)) is None
