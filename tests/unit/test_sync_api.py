"""Ceremony routes share live cookie authority, strict parsing and drained workers."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from camoufox_pm.api.errors import install_error_handlers
from camoufox_pm.api.routes import sync


@pytest.fixture
async def api(monkeypatch):
    calls = []

    async def owner(request, expected_id=None):
        if request.headers.get("cookie") != "test=owner":
            raise sync._error(401, "Invalid session")
        return "owner"

    class Ceremonies:
        def __getattr__(self, method):
            def run(user_id, *args, authorize):
                authorize()
                calls.append((method, user_id, args))
                return {"success": True}

            return run

    monkeypatch.setattr(sync, "_live_owner", owner)
    monkeypatch.setattr(sync, "_service", object())
    monkeypatch.setattr(sync, "_operation_in_progress", False)
    monkeypatch.setattr(sync, "_ceremonies", lambda service: Ceremonies())
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(sync.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,body,method",
    [
        ("/pairing/owner-code", None, "export_owner_code"),
        ("/devices", None, "list_devices"),
        (
            "/relay/connect",
            {"registration_token": "TRANSIENT", "password": "SECRET"},
            "connect_relay",
        ),
        ("/devices/revoke", {"device_id": "a" * 36}, "revoke_device"),
        ("/pairing/abandon", {}, "abandon_pairing"),
        ("/membership/resume", {}, "resume_membership"),
    ],
)
async def test_ceremony_routes(api, path, body, method):
    client, calls = api
    kwargs = {} if body is None else {"json": body}
    verb = "GET" if body is None else "POST"
    denied = await client.request(verb, "/sync" + path, **kwargs)
    assert denied.status_code == 401
    assert not calls
    response = await client.request(
        verb, "/sync" + path, headers={"cookie": "test=owner"}, **kwargs
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert calls[-1][:2] == (method, "owner")
    assert "SECRET" not in response.text and "TRANSIENT" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        '{"registration_token":"SECRET","registration_token":"OTHER"}',
        '{"registration_token":"SECRET","checkpoints":[]}',
        '{"registration_token":NaN}',
        '{"registration_token":' + "[" * 17 + "0" + "]" * 17 + "}",
    ],
)
async def test_strict_bounded_parser_no_secret_echo(api, raw):
    client, calls = api
    response = await client.post(
        "/sync/relay/connect",
        content=raw,
        headers={"cookie": "test=owner", "content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "SECRET" not in response.text
    assert not calls


@pytest.mark.asyncio
async def test_cross_origin_and_busy_rejected(api, monkeypatch):
    client, calls = api
    response = await client.post(
        "/sync/relay/connect",
        json={"registration_token": "secret"},
        headers={"cookie": "test=owner", "origin": "https://evil.example"},
    )
    assert response.status_code == 403
    monkeypatch.setattr(sync, "_operation_in_progress", True)
    response = await client.get("/sync/devices", headers={"cookie": "test=owner"})
    assert response.status_code == 409
    assert not calls


def test_checkpoint_default_fails_closed_and_injected_reader_is_trusted(monkeypatch):
    from camoufox_pm.sync.ceremonies import CeremonyError

    monkeypatch.setattr(sync, "_checkpoint_provider", None)
    service = SimpleNamespace(control=SimpleNamespace(get=lambda key: None))
    ceremony = sync._ceremonies(service)
    with pytest.raises(CeremonyError):
        ceremony._checkpoints()
    monkeypatch.setattr(sync, "_checkpoint_provider", lambda: ("protected",))
    assert ceremony._checkpoints() == ("protected",)


def test_public_pending_owner_bound():
    target = SimpleNamespace(
        service=SimpleNamespace(_read=lambda user: None),
        control=SimpleNamespace(
            get=lambda key: SimpleNamespace(
                payload={"local_user_id": "other", "kind": "pairing", "phase": "pending"}
            )
        ),
    )
    with pytest.raises(ValueError, match="Wrong ceremony owner"):
        sync._ceremony_state(target, "owner", "membership_status", (), lambda: None)


@pytest.mark.asyncio
async def test_real_pairing_draft_reload_resume_and_abandon(tmp_path, monkeypatch):
    from camoufox_pm.sync.ceremonies import DeviceCeremonies
    from camoufox_pm.sync.identity import bootstrap
    from camoufox_pm.sync.service import SyncService

    values = {}
    vault = SimpleNamespace(
        get=values.get,
        put=lambda key, value: values.__setitem__(key, value),
        delete=lambda key: values.pop(key, None),
    )
    monkeypatch.setattr(DeviceCeremonies, "_draft_vault", lambda *args: vault)
    monkeypatch.setattr(sync, "_service", SyncService(tmp_path.resolve() / "peer"))
    monkeypatch.setattr(sync, "_operation_in_progress", False)

    async def owner(request, expected_id=None):
        return request.headers.get("x-test-user", "owner")

    monkeypatch.setattr(sync, "_live_owner", owner)
    root, _, _ = bootstrap("https://relay.example")
    app = FastAPI()
    app.include_router(sync.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/sync/pairing/prepare",
            json={
                "root": root.model_dump(mode="json"),
                "device_label": "Laptop",
                "vault_kind": "native",
            },
        )
        assert response.status_code == 200
        prepared = response.json()
        original_keys = dict(values)
        draft = await client.get("/sync/pairing/draft")
        assert draft.json()["request"] == prepared["request"]
        assert draft.json()["fingerprint"] == prepared["fingerprint"]
        assert draft.json()["vault_kind"] == "native"
        wrong_owner = await client.get("/sync/pairing/draft", headers={"x-test-user": "other"})
        assert wrong_owner.status_code == 409
        resumed = await client.post("/sync/pairing/resume", json={})
        assert resumed.json() == prepared
        assert values == original_keys
        abandoned = await client.post("/sync/pairing/abandon", json={})
        assert abandoned.status_code == 200
        assert not values
        assert (await client.get("/sync/pairing/draft")).json()["phase"] == "abandoned"
        assert (await client.post("/sync/pairing/resume", json={})).status_code == 409


@pytest.fixture
async def runtime_api(api, monkeypatch, tmp_path):
    client, _ = api
    calls = []

    class Runtime:
        def __getattr__(self, method):
            async def run(user_id, *args, authorize, **kwargs):
                await authorize()
                calls.append((method, user_id, args, kwargs))
                return {"profiles": []} if method == "list_profiles" else {"local_id": "abcd1234"}

            return run

    monkeypatch.setattr(sync, "_runtime_factory", Runtime)
    monkeypatch.setattr(
        sync, "_service", SimpleNamespace(control=SimpleNamespace(directory=tmp_path))
    )
    yield client, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["enable", "handoff", "acquire", "retry", "recover"])
async def test_runtime_actions_require_live_cookie(runtime_api, action):
    client, calls = runtime_api
    url = f"/sync/profiles/abcd1234/{action}"
    assert (await client.post(url, json={})).status_code == 401
    response = await client.post(url, json={"password": "UNLOCK"}, headers={"cookie": "test=owner"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert calls == [(action, "owner", ("abcd1234",), {"password": "UNLOCK"})]
    assert "UNLOCK" not in response.text


@pytest.mark.asyncio
async def test_installed_diagnostic_probe_reaches_real_profile_lookup(
    runtime_api, monkeypatch, tmp_path
):
    from scripts.installed_sync_acceptance import DIAGNOSTIC_PROFILE_ID

    from camoufox_pm.core.profile_operations import profile_operation

    client, _ = runtime_api
    lookups = []

    async def missing_profile(profile_id, *, include_deleted=False):
        lookups.append(profile_id)
        return None

    manager = SimpleNamespace(
        storage=SimpleNamespace(get_profile=missing_profile), data_dir=tmp_path
    )

    class Runtime:
        async def acquire(self, user_id, local_id, *, authorize, password=None):
            await authorize()
            async with profile_operation(manager, local_id, sync_operation=True):
                pytest.fail("A missing profile must not start a transfer")

    monkeypatch.setattr(sync, "_runtime_factory", Runtime)
    response = await client.post(
        f"/sync/profiles/{DIAGNOSTIC_PROFILE_ID}/acquire",
        json={},
        headers={"cookie": "test=owner"},
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    evidence = response.json()["error"]["details"][0]["diagnostic"]
    assert evidence["stage"] == "execute"
    assert evidence["category"] == "invalid_state"
    assert evidence["site"] == "core.profile_operations"
    assert lookups == [DIAGNOSTIC_PROFILE_ID]
    assert DIAGNOSTIC_PROFILE_ID not in response.text
    assert not sync._operation_in_progress


@pytest.mark.asyncio
async def test_runtime_import_listing_and_invalid_operations(runtime_api):
    client, calls = runtime_api
    headers = {"cookie": "test=owner"}
    assert (await client.get("/sync/profiles", headers=headers)).json() == {"profiles": []}
    remote_id = "12345678-1234-1234-1234-123456789abc"
    response = await client.post(
        "/sync/profiles/import", json={"profile_id": remote_id}, headers=headers
    )
    assert response.status_code == 200
    assert calls[-1] == ("bind_paired", "owner", (remote_id,), {"password": None})
    for path, body in [
        ("/import", {"profile_id": "../private"}),
        ("/bad/enable", {}),
        ("/abcd1234/_session", {}),
        ("/abcd1234/enable", {"checkpoints": []}),
    ]:
        response = await client.post("/sync/profiles" + path, json=body, headers=headers)
        assert response.status_code in {404, 422}
    assert len(calls) == 2


@pytest.mark.parametrize("stage", ["runtime_setup", "service_setup", "execute"])
async def test_runtime_failures_return_only_closed_diagnostics_after_authorization(
    runtime_api, monkeypatch, stage
):
    import errno

    client, _calls = runtime_api
    executions = []

    class Runtime:
        async def acquire(self, *args, **kwargs):
            executions.append(True)
            raise OSError(errno.EACCES, "private-profile-password-and-host")

    def factory():
        if stage == "runtime_setup":
            raise OSError(errno.EACCES, "private-profile-password-and-host")
        return Runtime()

    def service():
        raise OSError(errno.EACCES, "private-profile-password-and-host")

    monkeypatch.setattr(sync, "_runtime_factory", factory)
    if stage == "service_setup":
        monkeypatch.setattr(sync, "_resolve_service", service)
    path = "/sync/profiles/abcd1234/acquire"
    denied = await client.post(path, json={"password": "private-unlock"})
    assert denied.status_code == 401 and denied.json()["error"]["details"] is None
    response = await client.post(
        path, json={"password": "private-unlock"}, headers={"cookie": "test=owner"}
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert "private-" not in response.text
    assert (
        response.json()["detail"] == "Synchronization operation failed; inspect its state and retry"
    )
    diagnostic = response.json()["error"]["details"][0]["diagnostic"]
    assert diagnostic["version"] == 1 and diagnostic["area"] == "sync"
    assert diagnostic["stage"] == stage and diagnostic["category"] == "permission_denied"
    expected_site = "api.sync" if stage == "execute" else "core.profile_operations"
    assert diagnostic["site"] == expected_site and 1 <= diagnostic["line"] <= 10000
    assert executions == ([True] if stage == "execute" else [])
    assert not sync._operation_in_progress


@pytest.mark.parametrize("revoked", [False, True])
async def test_post_operation_authorization_failure_keeps_existing_security_contract(
    runtime_api, monkeypatch, revoked
):
    import errno

    client, executions = runtime_api
    checks = []

    async def owner(request, expected_id=None):
        checks.append(True)
        if len(checks) == 4:
            if revoked:
                raise sync._error(401, "Invalid session")
            raise OSError(errno.ENOSPC, "private-database-location")
        return "owner"

    monkeypatch.setattr(sync, "_live_owner", owner)
    response = await client.post(
        "/sync/profiles/abcd1234/acquire",
        json={"password": "private-unlock"},
        headers={"cookie": "test=owner"},
    )
    assert len(executions) == 1 and len(checks) == (4 if revoked else 5)
    assert not sync._operation_in_progress
    assert "private-" not in response.text
    assert response.headers["cache-control"] == "no-store"
    if revoked:
        assert response.status_code == 401 and response.json()["error"]["details"] is None
    else:
        assert response.status_code == 409
        diagnostic = response.json()["error"]["details"][0]["diagnostic"]
        assert diagnostic["stage"] == "final_authorization"
        assert diagnostic["category"] == "disk_full"


@pytest.mark.parametrize("revoked", [False, True])
async def test_failed_runtime_requires_live_authority_before_exporting_metadata(
    runtime_api, monkeypatch, revoked
):
    client, _calls = runtime_api
    checks, attempts = [], []

    async def owner(request, expected_id=None):
        checks.append(True)
        if len(checks) == 3:
            if revoked:
                raise sync._error(403, "A live administrator owner session is required")
            raise OSError("private-authority-store")
        return "owner"

    def fail():
        attempts.append(True)
        raise ValueError("private-operation-data")

    monkeypatch.setattr(sync, "_live_owner", owner)
    monkeypatch.setattr(sync, "_runtime_factory", fail)
    response = await client.post(
        "/sync/profiles/abcd1234/acquire", json={}, headers={"cookie": "test=owner"}
    )
    assert response.status_code == (403 if revoked else 409)
    assert response.json()["error"]["details"] is None
    assert response.headers["cache-control"] == "no-store"
    assert "private-" not in response.text
    assert attempts == [True] and len(checks) == 3 and not sync._operation_in_progress


@pytest.mark.asyncio
async def test_runtime_cancellation_keeps_admission_until_worker_drained(monkeypatch, tmp_path):
    import asyncio

    started, release = asyncio.Event(), asyncio.Event()

    async def owner(request, expected_id=None):
        return "owner"

    class Runtime:
        async def enable(self, user_id, local_id, *, authorize):
            await authorize()
            started.set()
            await release.wait()
            await authorize()
            return {"success": True}

    monkeypatch.setattr(sync, "_live_owner", owner)
    monkeypatch.setattr(sync, "_runtime_factory", Runtime)
    monkeypatch.setattr(
        sync, "_service", SimpleNamespace(control=SimpleNamespace(directory=tmp_path))
    )
    monkeypatch.setattr(sync, "_operation_in_progress", False)
    task = asyncio.create_task(sync._runtime_call(None, "owner", "enable", "abcd1234"))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert sync._operation_in_progress
    with pytest.raises(sync.HTTPException) as error:
        await sync._runtime_call(None, "owner", "enable", "abcd1234")
    assert error.value.status_code == 409
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not sync._operation_in_progress


async def test_preserve_copy_requires_explicit_acknowledgement_and_cookie(runtime_api):
    client, calls = runtime_api
    url = "/sync/profiles/abcd1234/preserve-copy"
    assert (await client.post(url, json={"acknowledge_fork": True})).status_code == 401
    for body in ({}, {"acknowledge_fork": False}, {"acknowledge_fork": "true"}):
        assert (
            await client.post(url, json=body, headers={"cookie": "test=owner"})
        ).status_code == 422
    response = await client.post(
        url, json={"acknowledge_fork": True}, headers={"cookie": "test=owner"}
    )
    assert response.status_code == 200
    assert calls[-1] == (
        "preserve_local_copy",
        "owner",
        ("abcd1234",),
        {"acknowledge_fork": True, "name": None},
    )


async def test_takeover_requires_strict_acknowledgement_and_live_cookie(runtime_api):
    client, calls = runtime_api
    url = "/sync/profiles/abcd1234/takeover"
    assert (await client.post(url, json={"acknowledge_lost_device": True})).status_code == 401
    for body in (
        {},
        {"acknowledge_lost_device": False},
        {"acknowledge_lost_device": "true"},
        {"acknowledge_lost_device": 1},
    ):
        assert (
            await client.post(url, json=body, headers={"cookie": "test=owner"})
        ).status_code == 422
    result = await client.post(
        url, json={"acknowledge_lost_device": True}, headers={"cookie": "test=owner"}
    )
    assert result.status_code == 200
    assert calls[-1] == (
        "takeover",
        "owner",
        ("abcd1234",),
        {"acknowledge_lost_device": True, "password": None},
    )


@pytest.mark.parametrize(
    "action,field",
    [
        ("trash", "acknowledge_deletion"),
        ("apply-trash", "acknowledge_deletion"),
        ("restore", "acknowledge_restore"),
    ],
)
async def test_lifecycle_requires_explicit_boolean_and_cookie(runtime_api, action, field):
    client, calls = runtime_api
    url = f"/sync/profiles/abcd1234/{action}"
    assert (await client.post(url, json={field: True})).status_code == 401
    for value in (None, False, 1, "true"):
        response = await client.post(url, json={field: value}, headers={"cookie": "test=owner"})
        assert response.status_code == 422
    response = await client.post(url, json={field: True}, headers={"cookie": "test=owner"})
    assert response.status_code == 200
    assert calls[-1] == (
        action.replace("-", "_"),
        "owner",
        ("abcd1234",),
        {field: True, "password": None},
    )


@pytest.mark.asyncio
async def test_status_polling_does_not_displace_active_writer(api, monkeypatch):
    client, calls = api

    def status(user_id, *, authorize):
        authorize()
        return {"phase": "ready", "vault_kind": "native"}

    monkeypatch.setattr(sync, "_service", SimpleNamespace(status=status))
    monkeypatch.setattr(sync, "_operation_in_progress", True)
    assert (await client.get("/sync/status")).status_code == 401
    response = await client.get("/sync/status", headers={"cookie": "test=owner"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["vault_kind"] == "native"
    assert sync._operation_in_progress
    blocked = await client.post(
        "/sync/relay/connect",
        json={"registration_token": "synthetic"},
        headers={"cookie": "test=owner"},
    )
    assert blocked.status_code == 409 and not calls


@pytest.mark.asyncio
async def test_profile_polling_does_not_displace_active_writer(runtime_api, monkeypatch):
    client, calls = runtime_api
    monkeypatch.setattr(sync, "_operation_in_progress", True)
    assert (await client.get("/sync/profiles")).status_code == 401
    response = await client.get("/sync/profiles", headers={"cookie": "test=owner"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls == [("list_profiles", "owner", (), {})]
    assert sync._operation_in_progress
    blocked = await client.post(
        "/sync/profiles/abcd1234/handoff", json={}, headers={"cookie": "test=owner"}
    )
    assert blocked.status_code == 409 and len(calls) == 1


@pytest.mark.asyncio
async def test_failed_status_read_keeps_writer_admission(api, monkeypatch):
    client, _ = api

    def status(user_id, *, authorize):
        authorize()
        raise ValueError("synthetic private failure")

    monkeypatch.setattr(sync, "_service", SimpleNamespace(status=status))
    monkeypatch.setattr(sync, "_operation_in_progress", True)
    response = await client.get("/sync/status", headers={"cookie": "test=owner"})
    assert response.status_code == 409 and sync._operation_in_progress
    assert "private failure" not in response.text
