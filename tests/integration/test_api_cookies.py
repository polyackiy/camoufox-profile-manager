"""Cookie API exposes preview counts and attachment values only deliberately."""

import json
import sqlite3

import pytest

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.core import auth
from camoufox_pm.core import cookie_transfer as ct

SECRET = "synthetic-http-cookie-private"
COOKIE = {
    "name": "token",
    "value": SECRET,
    "domain": "localhost",
    "path": "/",
    "expires": 2_000_000_000,
}


@pytest.fixture
async def cookie_client(client, monkeypatch):
    monkeypatch.setattr(ct, "ensure_browser_ready", lambda: None)

    async def stage(profile, directory, source, batch, mode):
        previous = []
        if source.exists():
            with sqlite3.connect(source) as connection:
                previous = json.loads(connection.execute("SELECT data FROM proof").fetchone()[0])
        cookies = previous if batch is None else batch.cookies
        candidate = directory / "cookies.sqlite"
        with sqlite3.connect(candidate) as connection:
            connection.execute("CREATE TABLE proof (data TEXT)")
            connection.execute("INSERT INTO proof VALUES (?)", (json.dumps(cookies),))
        return cookies, candidate

    monkeypatch.setattr(ct, "_stage_store", stage)
    return client


async def profile(client):
    created = await client.post(
        "/api/v1/profiles", json={"name": "Cookie proof", "generate_fingerprint": False}
    )
    assert created.status_code == 201
    return created.json()["id"]


@pytest.mark.asyncio
async def test_preview_and_import_metadata_never_return_values(cookie_client):
    client = cookie_client
    identifier = await profile(client)
    body = {"format": "json", "text": json.dumps([COOKIE])}
    preview = await client.post(f"/api/v1/profiles/{identifier}/cookies/preview", json=body)
    assert preview.status_code == 200 and SECRET not in preview.text
    assert preview.json()["data"] == {
        "total": 1,
        "importable": 1,
        "session_count": 0,
        "skipped": [],
    }
    imported = await client.post(
        f"/api/v1/profiles/{identifier}/cookies/import",
        json={**body, "mode": "merge", "row_version": 0},
    )
    assert imported.status_code == 200 and SECRET not in imported.text
    assert imported.json()["data"]["row_version"] == 1
    exported = await client.get(
        f"/api/v1/profiles/{identifier}/cookies/export?format=json&row_version=1"
    )
    assert exported.status_code == 200 and SECRET in exported.text
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.headers["cache-control"] == "no-store"
    assert exported.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_errors_never_echo_cookie_input_and_do_not_write(cookie_client):
    client = cookie_client
    identifier = await profile(client)
    for body in (
        {"format": "json", "text": json.dumps([{**COOKIE, "path": SECRET}])},
        {"format": "json", "text": {"secret": SECRET}},
        {"format": SECRET, "text": SECRET},
        {"format": "json", "text": SECRET},
    ):
        response = await client.post(f"/api/v1/profiles/{identifier}/cookies/preview", json=body)
        assert response.status_code == 422 and SECRET not in response.text
        assert all(
            "input" not in item and "ctx" not in item
            for item in response.json()["error"]["details"] or []
        )
    assert (await get_profile_manager().get_profile(identifier)).row_version == 0


@pytest.mark.asyncio
async def test_running_stale_trashed_missing_and_replace_confirmation(cookie_client):
    client = cookie_client
    identifier = await profile(client)
    root = f"/api/v1/profiles/{identifier}/cookies"
    body = {"format": "json", "text": "[]", "mode": "replace", "row_version": 0}
    assert (await client.post(root + "/import", json=body)).status_code == 422
    assert (
        await client.post(root + "/import", json={**body, "mode": "merge", "row_version": 99})
    ).status_code == 409
    manager = get_profile_manager()
    await manager.storage.acquire_lease(identifier, "remote", 300)
    assert (await client.post(root + "/import", json={**body, "mode": "merge"})).status_code == 409
    assert (await client.get(root + "/export?row_version=0")).status_code == 409
    await manager.storage.release_lease(identifier, "remote")
    replaced = await client.post(root + "/import", json={**body, "confirm_replace": True})
    assert replaced.status_code == 200 and replaced.json()["data"]["backup_id"]
    await manager.delete_profile(identifier)
    assert (await client.get(root + "/export?row_version=1")).status_code == 404
    assert (
        await client.post(
            "/api/v1/profiles/missing/cookies/preview", json={"format": "json", "text": "[]"}
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_all_cookie_routes_are_authenticated(cookie_client):
    client = cookie_client
    identifier = await profile(client)
    manager = get_profile_manager()
    await manager.storage.create_user(
        auth.new_user_id(), "cookie-user", auth.hash_password("local-test-password")
    )
    root = f"/api/v1/profiles/{identifier}/cookies"
    for method, path, body in (
        ("POST", root + "/preview", {"format": "json", "text": json.dumps([COOKIE])}),
        (
            "POST",
            root + "/import",
            {"format": "json", "text": json.dumps([COOKIE]), "row_version": 0},
        ),
        ("GET", root + "/export?row_version=0", None),
    ):
        response = await client.request(method, path, json=body)
        assert response.status_code == 401 and SECRET not in response.text
