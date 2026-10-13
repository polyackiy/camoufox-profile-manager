"""Revocation must affect downloads and scheduled work, not just UI buttons."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI

from camoufox_pm.api import dependencies
from camoufox_pm.api.access import authorized_bytes_response, authorized_file_response
from camoufox_pm.api.middleware.access import AccessMiddleware
from camoufox_pm.core import auth
from camoufox_pm.core.models import Schedule
from camoufox_pm.core.scheduler import TaskScheduler


async def account(storage, username="operator", role="operator"):
    user_id = auth.new_user_id()
    await storage.create_user(user_id, username, auth.hash_password("test-password-123"), role)
    token = await auth.create_session(storage, user_id)
    return user_id, token


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["file", "bytes"])
@pytest.mark.parametrize("revocation", [None, "grant", "session", "role"])
async def test_download_rechecks_authority_and_audits_actual_completion(
    profile_manager, monkeypatch, tmp_path, format, revocation
):
    storage = profile_manager.storage
    monkeypatch.setattr(dependencies, "_storage_manager", storage)
    profile = await profile_manager.create_profile("Download", generate_fingerprint=False)
    _, token = await account(storage)
    await storage.set_profile_grant("operator", profile.id, True)
    data = b"x" * (3 * 64 * 1024)
    path = tmp_path / "payload.bin"
    path.write_bytes(data)
    app = FastAPI()
    app.add_middleware(AccessMiddleware)

    @app.get(
        "/api/v1/profiles/{profile_id}/export", dependencies=[Depends(dependencies.require_auth)]
    )
    async def export_test(profile_id: str):
        if format == "file":
            return await authorized_file_response(
                path, [profile_id], "payload.bin", "application/octet-stream"
            )
        return await authorized_bytes_response(data, [profile_id], "application/octet-stream")

    chunks = []

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            if len(chunks) == 1:
                if revocation == "grant":
                    await storage.delete_profile_grant("operator", profile.id)
                elif revocation == "session":
                    await storage.delete_session(auth.hash_token(token))
                elif revocation == "role":
                    await storage.update_user_access("operator", role="viewer")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/api/v1/profiles/{profile.id}/export",
        "query_string": b"",
        "headers": [(b"cookie", f"{auth.SESSION_COOKIE}={token}".encode())],
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
    }
    await app(scope, receive, send)
    assert len(b"".join(chunks)) == (len(data) if revocation is None else 64 * 1024)
    events = await storage.list_audit(action="export_test")
    assert len(events) == 1
    assert events[0]["result"] == ("success" if revocation is None else "interrupted")
    assert token not in str(events) and auth.hash_token(token) not in str(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["grant", "role", "delete_recreate"])
async def test_scheduled_execution_rechecks_durable_actor(profile_manager, monkeypatch, revocation):
    storage = profile_manager.storage
    profile = await profile_manager.create_profile("Scheduled", generate_fingerprint=False)
    user_id, _ = await account(storage)
    await storage.set_profile_grant("operator", profile.id)
    schedule = Schedule(
        profile_id=profile.id,
        action="launch",
        kind="interval",
        interval_minutes=5,
        actor_kind="session",
        actor_username="operator",
        actor_user_id=user_id,
    )
    await storage.save_schedule(schedule)
    launch = AsyncMock()
    monkeypatch.setattr(profile_manager, "launch_browser", launch)
    scheduler = TaskScheduler(storage, profile_manager)
    assert (await scheduler.execute(schedule)).outcome == "ok"
    launch.reset_mock()
    if revocation == "grant":
        await storage.delete_profile_grant("operator", profile.id)
    elif revocation == "role":
        await storage.update_user_access("operator", role="viewer")
    else:
        await storage.delete_user("operator")
        await account(storage, role="administrator")
    assert (await scheduler.execute(await storage.get_schedule(schedule.id))).outcome == "error"
    launch.assert_not_awaited()
    assert not (await storage.get_schedule(schedule.id)).enabled
    assert (await storage.list_audit(action="schedule_execute"))[0]["result"] == "denied"


@pytest.mark.asyncio
async def test_scheduled_close_rechecks_grant(profile_manager, monkeypatch):
    storage = profile_manager.storage
    profile = await profile_manager.create_profile("Timed", generate_fingerprint=False)
    user_id, _ = await account(storage)
    await storage.set_profile_grant("operator", profile.id)
    schedule = Schedule(
        profile_id=profile.id,
        action="launch",
        kind="interval",
        interval_minutes=5,
        run_minutes=1,
        actor_kind="session",
        actor_username="operator",
        actor_user_id=user_id,
    )
    monkeypatch.setattr(profile_manager, "launch_browser", AsyncMock())
    close = AsyncMock()
    monkeypatch.setattr(profile_manager, "close_browser", close)
    now = datetime.now()
    scheduler = TaskScheduler(storage, profile_manager, now=lambda: now)
    await scheduler.execute(schedule)
    await storage.delete_profile_grant("operator", profile.id)
    now += timedelta(minutes=2)
    await scheduler.tick()
    close.assert_not_awaited()
    assert (await storage.list_audit(action="schedule_close"))[0]["result"] == "denied"


@pytest.mark.asyncio
async def test_team_health_hides_global_inventory_and_admin_controls(client):
    manager = dependencies.get_profile_manager()
    profile = await manager.create_profile("Hidden", generate_fingerprint=False)
    _, token = await account(manager.storage, role="viewer")
    assert (await client.get("/health")).json()["profiles_count"] is None
    client.cookies.set(auth.SESSION_COOKIE, token)
    for prefix in ("/api", "/api/v1"):
        for path in (
            "/system/config",
            "/system/browser",
            "/system/update-recovery",
            "/access/users",
            "/access/audit",
        ):
            assert (await client.get(prefix + path)).status_code == 403
        listed = await client.get(prefix + "/profiles")
        assert listed.json()["total"] == 0
        assert profile.id not in listed.text


@pytest.mark.asyncio
async def test_schedule_api_stamps_actor_and_hides_ungranted_schedule(client):
    manager = dependencies.get_profile_manager()
    profile = await manager.create_profile("Own", generate_fingerprint=False)
    hidden = await manager.create_profile("Hidden", generate_fingerprint=False)
    hidden_schedule = Schedule(
        profile_id=hidden.id, action="launch", kind="interval", interval_minutes=5
    )
    await manager.storage.save_schedule(hidden_schedule)
    user_id, token = await account(manager.storage)
    await manager.storage.set_profile_grant("operator", profile.id)
    client.cookies.set(auth.SESSION_COOKIE, token)
    made = await client.post(
        "/api/v1/schedules",
        json={
            "profile_id": profile.id,
            "action": "launch",
            "kind": "interval",
            "interval_minutes": 5,
            "actor_kind": "system",
        },
    )
    assert made.status_code == 201
    saved = await manager.storage.get_schedule(made.json()["id"])
    assert saved.actor_kind == "session" and saved.actor_user_id == user_id
    for prefix in ("/api", "/api/v1"):
        known = await client.get(prefix + "/schedules/" + hidden_schedule.id)
        missing = await client.get(prefix + "/schedules/missing")
        assert known.status_code == missing.status_code == 404
        assert known.json()["error"]["message"] == missing.json()["error"]["message"]
        assert (await client.get(prefix + "/schedules")).json()["total"] == 1


@pytest.mark.asyncio
async def test_nonadmin_server_errors_and_password_validation_do_not_echo_secrets(
    client, monkeypatch
):
    manager = dependencies.get_profile_manager()
    profile = await manager.create_profile("Failing", generate_fingerprint=False)
    _, token = await account(manager.storage)
    await manager.storage.set_profile_grant("operator", profile.id)
    client.cookies.set(auth.SESSION_COOKIE, token)
    monkeypatch.setattr(
        manager,
        "launch_browser",
        AsyncMock(side_effect=RuntimeError("proxy-secret at /private/browser")),
    )
    response = await client.post(f"/api/v1/profiles/{profile.id}/launch", json={})
    assert response.status_code == 500
    assert "proxy-secret" not in response.text and "/private/browser" not in response.text
    events = await manager.storage.list_audit(action="launch_profile")
    assert events[0]["result"] == "failure"
    assert "proxy-secret" not in str(events)
    # Validation responses must not echo the password even when a required field
    # is missing and pydantic's input would otherwise contain the whole object.
    await manager.storage.update_user_access("operator", role="administrator")
    token = await auth.create_session(
        manager.storage, (await manager.storage.get_user_by_username("operator"))["id"]
    )
    client.cookies.set(auth.SESSION_COOKIE, token)
    response = await client.post(
        "/api/v1/access/users", json={"username": "new", "password": "secret-not-for-response"}
    )
    assert response.status_code == 422
    assert "secret-not-for-response" not in response.text


@pytest.mark.asyncio
async def test_nonadmin_conflict_handler_hides_lease_holder(client):
    from httpx import ASGITransport, AsyncClient

    from camoufox_pm.api.errors import install_error_handlers
    from camoufox_pm.core.leases import ProfileLocked

    manager = dependencies.get_profile_manager()
    _, token = await account(manager.storage)
    isolated_app = FastAPI()
    install_error_handlers(isolated_app)

    @isolated_app.get("/conflict", dependencies=[Depends(dependencies.require_auth)])
    async def conflict():
        raise ProfileLocked("test-profile", "private-host:1234:private-uuid")

    async with AsyncClient(
        transport=ASGITransport(app=isolated_app), base_url="http://test"
    ) as isolated_client:
        isolated_client.cookies.set(auth.SESSION_COOKIE, token)
        response = await isolated_client.get("/conflict")
    assert response.status_code == 409
    assert "private-host" not in response.text
    assert "private-uuid" not in response.text
