"""ASGI enrollment boundaries, including auth changes while expensive work runs."""

import asyncio
import base64
import threading
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import Depends, FastAPI

from camoufox_pm.api import dependencies
from camoufox_pm.api.routes import sync
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth


class FakeService:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.resume = threading.Event()
        self.pause = False
        self.fail = False

    def _action(self, method, user_id, args, authorize):
        if self.pause:
            self.started.set()
            assert self.resume.wait(5)
        authorize()
        if self.fail:
            raise ValueError("PRIVATE-KEY-MUST-NOT-LEAK")
        self.calls.append((method, user_id, args))
        if method == "recovery_package":
            return b"encrypted-recovery", "S" * 43
        return {"phase": "ready" if method == "confirm_recovery" else "recovery_required"}

    def status(self, user_id, *, authorize):
        return self._action("status", user_id, (), authorize)

    def begin_enrollment(self, user_id, *args, authorize):
        return self._action("begin_enrollment", user_id, args, authorize)

    def recovery_package(self, user_id, *args, authorize):
        return self._action("recovery_package", user_id, args, authorize)

    def confirm_recovery(self, user_id, *args, authorize):
        return self._action("confirm_recovery", user_id, args, authorize)

    def abandon_enrollment(self, user_id, *args, authorize):
        return self._action("abandon_enrollment", user_id, args, authorize)


@pytest.fixture
async def enrollment_api(storage, monkeypatch):
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(dependencies, "_storage_manager", storage)
    fake = FakeService()
    monkeypatch.setattr(sync, "_service", fake)
    monkeypatch.setattr(sync, "_operation_in_progress", False)
    app = FastAPI()
    for prefix in ("/api", "/api/v1"):
        app.include_router(
            sync.router, prefix=prefix, dependencies=[Depends(dependencies.require_auth)]
        )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, fake
    finally:
        fake.resume.set()
        get_settings.cache_clear()


async def session(storage, role="administrator", username="alice", expired=False):
    user_id = auth.new_user_id()
    await storage.create_user(user_id, username, "unused", role)
    token = auth.new_session_token()
    await storage.create_session(
        auth.hash_token(token), user_id, datetime.now() + timedelta(hours=-1 if expired else 1)
    )
    return {"Cookie": f"cpm_session={token}"}, user_id, token


BODY = {
    "relay_url": "https://relay.example",
    "device_label": "Laptop",
    "vault_kind": "encrypted_file",
    "password": "unlock-password",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
async def test_cookie_only_enrollment_and_recovery_flow(enrollment_api, storage, prefix):
    client, fake = enrollment_api
    headers, user_id, _ = await session(storage)
    response = await client.post(prefix + "/sync/enrollment", json=BODY, headers=headers)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert fake.calls[-1] == (
        "begin_enrollment",
        user_id,
        ("https://relay.example", "Laptop", "encrypted_file", "unlock-password"),
    )
    result = await client.post(
        prefix + "/sync/recovery-package", json={"password": "unlock-password"}, headers=headers
    )
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    body = result.json()
    assert (
        base64.urlsafe_b64decode(body["package"] + "=" * (-len(body["package"]) % 4))
        == b"encrypted-recovery"
    )
    response = await client.post(
        prefix + "/sync/confirm-recovery",
        json={**body, "password": "unlock-password"},
        headers=headers,
    )
    assert response.status_code == 200
    assert fake.calls[-1] == (
        "confirm_recovery",
        user_id,
        (b"encrypted-recovery", "S" * 43, "unlock-password"),
    )
    assert (await client.get(prefix + "/sync/status", headers=headers)).status_code == 200
    assert (
        await client.post(prefix + "/sync/abandon", json={}, headers=headers)
    ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["anonymous", "api_key", "operator", "viewer", "expired"])
async def test_no_anonymous_key_or_nonadmin_enrollment(enrollment_api, storage, monkeypatch, kind):
    client, fake = enrollment_api
    headers = {}
    if kind == "api_key":
        monkeypatch.setenv("CPM_API_KEY", "valid-legacy-key")
        get_settings.cache_clear()
        headers = {"X-API-Key": "valid-legacy-key"}
    elif kind in {"operator", "viewer", "expired"}:
        headers, _, _ = await session(
            storage, role=kind if kind != "expired" else "administrator", expired=kind == "expired"
        )
    for route in ("enrollment", "recovery-package", "confirm-recovery", "abandon"):
        response = await client.post("/api/sync/" + route, json=BODY, headers=headers)
        assert response.status_code in {401, 403}
    assert (await client.get("/api/sync/status", headers=headers)).status_code in {401, 403}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_valid_key_does_not_override_operator_cookie(enrollment_api, storage, monkeypatch):
    client, fake = enrollment_api
    headers, _, _ = await session(storage, role="operator")
    monkeypatch.setenv("CPM_API_KEY", "valid-legacy-key")
    get_settings.cache_clear()
    response = await client.post(
        "/api/sync/enrollment", json=BODY, headers={**headers, "X-API-Key": "valid-legacy-key"}
    )
    assert response.status_code == 403
    assert fake.calls == []


@pytest.mark.asyncio
async def test_cookie_admin_works_even_with_matching_key(enrollment_api, storage, monkeypatch):
    client, fake = enrollment_api
    headers, user_id, _ = await session(storage)
    monkeypatch.setenv("CPM_API_KEY", "valid-legacy-key")
    get_settings.cache_clear()
    response = await client.post(
        "/api/sync/enrollment", json=BODY, headers={**headers, "X-API-Key": "valid-legacy-key"}
    )
    assert response.status_code == 201
    assert fake.calls[-1][1] == user_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.invalid",
        "null",
        "http://test@attacker.invalid",
        "http://test:81",
        "http://test/extra",
        "http://test?query",
        "http://test#fragment",
    ],
)
async def test_cross_origin_mutations_rejected(enrollment_api, storage, origin):
    client, fake = enrollment_api
    headers, _, _ = await session(storage)
    response = await client.post(
        "/api/sync/enrollment", json=BODY, headers={**headers, "Origin": origin}
    )
    assert response.status_code == 403
    assert fake.calls == []


@pytest.mark.asyncio
async def test_same_origin_allowed_and_plain_form_refused(enrollment_api, storage):
    client, fake = enrollment_api
    headers, _, _ = await session(storage)
    assert (
        await client.post(
            "/api/sync/enrollment", json=BODY, headers={**headers, "Origin": "http://test"}
        )
    ).status_code == 201
    fake.calls.clear()
    assert (
        await client.post("/api/sync/enrollment", data=BODY, headers=headers)
    ).status_code == 415
    assert (
        await client.post(
            "/api/sync/abandon", json={}, headers={**headers, "Sec-Fetch-Site": "cross-site"}
        )
    ).status_code == 403
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {**BODY, "password": {"secret": "PRIVATE-SECRET"}},
        {**BODY, "private": "PRIVATE-SECRET"},
        {**BODY, "device_label": "PRIVATE-SECRET" * 100},
        {**BODY, "vault_kind": "PRIVATE-SECRET"},
    ],
)
async def test_invalid_fields_never_echo_secret(enrollment_api, storage, payload):
    client, fake = enrollment_api
    headers, _, _ = await session(storage)
    response = await client.post("/api/sync/enrollment", json=payload, headers=headers)
    assert response.status_code == 422
    assert "PRIVATE-SECRET" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_body_limit_duplicate_keys_and_malformed_base64(enrollment_api, storage):
    client, fake = enrollment_api
    headers, _, _ = await session(storage)
    headers = {**headers, "Content-Type": "application/json"}
    response = await client.post(
        "/api/sync/enrollment", content=b"x" * (sync._MAX_BODY + 1), headers=headers
    )
    assert response.status_code == 413
    response = await client.post(
        "/api/sync/recovery-package",
        content=b'{"password":"one","password":"two"}',
        headers=headers,
    )
    assert response.status_code == 422
    for encoded in ("%%%%", "Zg==", "Zh"):
        response = await client.post(
            "/api/sync/confirm-recovery",
            json={"package": encoded, "secret": "s" * 43},
            headers=headers,
        )
        assert response.status_code == 422
    assert fake.calls == []


@pytest.mark.asyncio
async def test_service_failure_is_redacted(enrollment_api, storage):
    client, fake = enrollment_api
    fake.fail = True
    headers, _, _ = await session(storage)
    response = await client.post("/api/sync/enrollment", json=BODY, headers=headers)
    assert response.status_code == 409
    assert "PRIVATE-KEY" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["logout", "demote", "replace"])
async def test_recheck_blocks_auth_change_during_worker(enrollment_api, storage, change):
    client, fake = enrollment_api
    headers, _, token = await session(storage)
    await session(storage, username="remaining-admin")
    fake.pause = True
    request = asyncio.create_task(client.post("/api/sync/enrollment", json=BODY, headers=headers))
    assert await asyncio.to_thread(fake.started.wait, 2)
    if change == "logout":
        await storage.delete_session(auth.hash_token(token))
    elif change == "demote":
        await storage.update_user_access("alice", role="operator")
    else:
        await storage.delete_user("alice")
        await storage.create_user(auth.new_user_id(), "alice", "unused", "administrator")
    fake.resume.set()
    response = await request
    assert response.status_code in {401, 403}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_busy_guard_prevents_parallel_kdf(enrollment_api, storage):
    client, fake = enrollment_api
    headers, _, _ = await session(storage)
    fake.pause = True
    request = asyncio.create_task(client.post("/api/sync/enrollment", json=BODY, headers=headers))
    assert await asyncio.to_thread(fake.started.wait, 2)
    second = await client.post("/api/sync/recovery-package", json={}, headers=headers)
    assert second.status_code == 409
    fake.resume.set()
    assert (await request).status_code == 201


@pytest.mark.asyncio
async def test_missing_optional_service_is_503(enrollment_api, storage, monkeypatch):
    client, _ = enrollment_api
    headers, _, _ = await session(storage)
    monkeypatch.setattr(sync, "_service", None)
    response = await client.get("/api/sync/status", headers=headers)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_lazy_real_service_and_clean_recovery_confirmation(
    enrollment_api, storage, tmp_path, monkeypatch
):
    pytest.importorskip("cryptography.hazmat.primitives.hpke")
    pytest.importorskip("nacl")
    client, _ = enrollment_api
    headers, _, _ = await session(storage)
    directory = tmp_path.resolve() / "sync-control"
    monkeypatch.setattr(sync, "_directory", directory)
    monkeypatch.setattr(sync, "_service", None)
    assert not directory.exists()
    response = await client.get("/api/sync/status", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"phase": "not_enrolled"}
    assert directory.is_dir()
    response = await client.post("/api/sync/enrollment", json=BODY, headers=headers)
    assert response.status_code == 201
    assert response.json()["phase"] == "recovery_required"
    package = await client.post(
        "/api/sync/recovery-package", json={"password": BODY["password"]}, headers=headers
    )
    assert package.status_code == 200
    confirmed = await client.post(
        "/api/sync/confirm-recovery",
        json={**package.json(), "password": BODY["password"]},
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["phase"] == "ready"
    assert BODY["password"] not in confirmed.text
    other_headers, _, _ = await session(storage, username="different-admin")
    assert (await client.get("/api/sync/status", headers=other_headers)).status_code == 409


@pytest.mark.asyncio
async def test_cancellation_drains_worker_before_releasing_busy_guard(enrollment_api, storage):
    client, fake = enrollment_api
    headers, _, _ = await session(storage)
    fake.pause = True
    pending = asyncio.create_task(client.post("/api/sync/enrollment", json=BODY, headers=headers))
    assert await asyncio.to_thread(fake.started.wait, 2)
    pending.cancel()
    # Let cancellation reach run_file_operation; the worker must remain protected.
    await asyncio.sleep(0)
    response = await client.post("/api/sync/recovery-package", json={}, headers=headers)
    assert response.status_code == 409
    assert sync._operation_in_progress
    fake.resume.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not sync._operation_in_progress
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "endpoint,body,operation",
    [
        (
            "share",
            {
                "target_device_id": "11111111-1111-4111-8111-111111111111",
                "password": "private-unlock",
            },
            "create_profile_code",
        ),
        (
            "accept-code",
            {"pack": {"body": {"sequence": 2}, "signature": "signed"}},
            "accept_profile_code",
        ),
    ],
)
async def test_profile_codes_use_live_cookie_and_private_worker(
    enrollment_api, storage, monkeypatch, endpoint, body, operation
):
    from camoufox_pm.sync import profile_invitation

    client, fake = enrollment_api
    headers, user_id, _ = await session(storage)
    called = []

    def action(service, actor, value, *, authorize, **kwargs):
        authorize()
        called.append((service, actor, value))
        return {"accepted": True}

    monkeypatch.setattr(profile_invitation, operation, action)
    response = await client.post("/api/v1/sync/profiles/" + endpoint, headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert called[0][0] is fake and called[0][1] == user_id
    assert "private-unlock" not in response.text
    denied = await client.post(
        "/api/v1/sync/profiles/" + endpoint,
        headers={**headers, "Origin": "https://foreign.example"},
        json=body,
    )
    assert denied.status_code == 403
    assert len(called) == 1


@pytest.mark.parametrize("role", ["operator", "viewer"])
@pytest.mark.parametrize(
    "endpoint,body",
    [
        ("share", {"target_device_id": "11111111-1111-4111-8111-111111111111"}),
        ("accept-code", {"pack": {}}),
    ],
)
async def test_profile_code_routes_reject_nonadministrators(
    enrollment_api, storage, role, endpoint, body
):
    client, _ = enrollment_api
    headers, _, _ = await session(storage, role=role)
    response = await client.post("/api/v1/sync/profiles/" + endpoint, headers=headers, json=body)
    assert response.status_code == 403


async def test_profile_code_body_bounds_and_api_key_not_enough(
    enrollment_api, storage, monkeypatch
):
    client, _ = enrollment_api
    monkeypatch.setenv("CPM_API_KEY", "legacy-key")
    get_settings.cache_clear()
    denied = await client.post(
        "/api/v1/sync/profiles/accept-code", headers={"X-API-Key": "legacy-key"}, json={"pack": {}}
    )
    assert denied.status_code == 401
    headers, _, _ = await session(storage)
    headers["X-API-Key"] = "legacy-key"
    invalid = await client.post(
        "/api/v1/sync/profiles/accept-code",
        headers=headers,
        json={"pack": {}, "password": "MUST-NOT-ECHO"},
    )
    assert invalid.status_code == 422 and "MUST-NOT-ECHO" not in invalid.text
    oversized = await client.post(
        "/api/v1/sync/profiles/accept-code",
        headers=headers,
        json={"pack": {"text": "x" * (128 * 1024)}},
    )
    assert oversized.status_code == 413


@pytest.mark.parametrize(
    "endpoint,operation,body",
    [
        (
            "share",
            "create_profile_code",
            {"target_device_id": "11111111-1111-4111-8111-111111111111"},
        ),
        ("accept-code", "accept_profile_code", {"pack": {}}),
    ],
)
async def test_profile_code_worker_rechecks_logout_before_mutation(
    enrollment_api, storage, monkeypatch, endpoint, operation, body
):
    from camoufox_pm.sync import profile_invitation

    client, fake = enrollment_api
    headers, _, token = await session(storage)
    fake.pause = True

    def paused(service, user_id, value, *, authorize, **kwargs):
        return fake._action(operation, user_id, (value,), authorize)

    monkeypatch.setattr(profile_invitation, operation, paused)
    task = asyncio.create_task(
        client.post("/api/v1/sync/profiles/" + endpoint, headers=headers, json=body)
    )
    assert await asyncio.to_thread(fake.started.wait, 2)
    await storage.delete_session(auth.hash_token(token))
    fake.resume.set()
    response = await task
    assert response.status_code == 401
    assert fake.calls == []
