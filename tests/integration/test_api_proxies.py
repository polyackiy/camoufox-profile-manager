"""Protected inventory API, write-only credentials, and guarded assignments."""

from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth, proxy_check
from camoufox_pm.core.proxy_inventory import ProxyInventory

SECRET = "api-inventory-secret:@/"
PAYLOAD = {
    "name": "Office proxy",
    "type": "http",
    "server": "Proxy.Example:8080",
    "username": "account",
    "password": SECRET,
    "country": "US",
}


@pytest.fixture
async def proxy_client(client, monkeypatch):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    get_settings.cache_clear()
    yield client
    get_settings.cache_clear()


async def create(client, **changes):
    response = await client.post("/api/v1/proxies", json={**PAYLOAD, **changes})
    assert response.status_code == 201, response.text
    assert SECRET not in response.text and "password" not in response.json()["data"]
    return response.json()["data"]


@pytest.mark.asyncio
async def test_create_list_patch_and_delete_keep_password_write_only(proxy_client):
    client = proxy_client
    proxy = await create(client)
    assert proxy["server"] == "proxy.example:8080" and proxy["has_password"]
    response = await client.get("/api/v1/proxies")
    assert response.json()["data"]["proxies"] == [proxy]
    assert SECRET not in response.text and "enc:" not in response.text
    changed = await client.patch(
        f"/api/v1/proxies/{proxy['id']}", json={"row_version": 0, "name": "Rename only"}
    )
    assert changed.status_code == 200 and changed.json()["data"]["has_password"]
    stale = await client.patch(
        f"/api/v1/proxies/{proxy['id']}", json={"row_version": 0, "password": "unsaved-secret"}
    )
    assert stale.status_code == 409 and "unsaved-secret" not in stale.text
    assert (await client.delete(f"/api/v1/proxies/{proxy['id']}?row_version=0")).status_code == 409
    clear = await client.patch(
        f"/api/v1/proxies/{proxy['id']}", json={"row_version": 1, "password": None}
    )
    assert clear.status_code == 200 and not clear.json()["data"]["has_password"]
    assert (await client.delete(f"/api/v1/proxies/{proxy['id']}")).status_code == 422
    assert (await client.delete(f"/api/v1/proxies/{proxy['id']}?row_version=2")).status_code == 200
    assert (await client.get("/api/v1/proxies")).json()["data"]["proxies"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"server": "account:invalid-secret@proxy.example:8080"},
        {"server": "proxy.example:8080/path?invalid-secret"},
        {"server": "[::1]suffix:80"},
        {"server": "::1:8080"},
        {"server": "proxy.example:0"},
        {"server": "proxy.example:65536"},
        {"server": "proxy.example"},
        {"username": "invalid-secret\n"},
        {"password": "invalid-secret\r"},
        {"password": {"raw": "invalid-secret"}},
        {"name": " "},
        {"username": None, "password": "invalid-secret"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_inventory_payloads_never_echo_credentials(proxy_client, changes):
    response = await proxy_client.post("/api/v1/proxies", json={**PAYLOAD, **changes})
    assert response.status_code == 422
    assert "invalid-secret" not in response.text and SECRET not in response.text
    assert all(
        "input" not in item and "ctx" not in item
        for item in response.json()["error"]["details"] or []
    )
    assert (await proxy_client.get("/api/v1/proxies")).json()["data"]["proxies"] == []


@pytest.mark.asyncio
async def test_bracketed_ipv6_and_explicit_patch_clear_rules(proxy_client):
    proxy = await create(proxy_client, server="[2001:0db8:0:0::1]:1080", type="socks5")
    assert proxy["server"] == "[2001:db8::1]:1080"
    for changes in ({"server": None}, {"name": None}, {"type": None}, {"username": None}):
        response = await proxy_client.patch(
            f"/api/v1/proxies/{proxy['id']}", json={"row_version": 0, **changes}
        )
        assert response.status_code == 422
    response = await proxy_client.patch(
        f"/api/v1/proxies/{proxy['id']}",
        json={
            "row_version": 0,
            "username": None,
            "password": None,
        },
    )
    assert response.status_code == 200
    assert (
        response.json()["data"]["username"] is None and not response.json()["data"]["has_password"]
    )


@pytest.mark.asyncio
async def test_check_returns_safe_stored_shape_and_refuses_stale_result(proxy_client, monkeypatch):
    proxy = await create(proxy_client)
    monkeypatch.setattr(
        proxy_check,
        "check",
        AsyncMock(
            return_value=proxy_check.ProxyCheckResult(
                reachable=True,
                latency_ms=23,
                location=proxy_check.ProxyLocation("1.2.3.4", country="US"),
            )
        ),
    )
    checked = await proxy_client.post(f"/api/v1/proxies/{proxy['id']}/check")
    assert checked.status_code == 200
    record = checked.json()["data"]["last_check"]
    assert record["ip"] == "1.2.3.4" and record["latency_ms"] == 23 and record["checked_at"]
    assert SECRET not in checked.text

    async def concurrent_edit(*args):
        await ProxyInventory(get_profile_manager()).update(
            proxy["id"], {"server": "new.example:8080"}, 0
        )
        return proxy_check.ProxyCheckResult(reachable=True)

    monkeypatch.setattr(proxy_check, "check", concurrent_edit)
    stale = await proxy_client.post(f"/api/v1/proxies/{proxy['id']}/check")
    assert stale.status_code == 409
    assert (await proxy_client.get("/api/v1/proxies")).json()["data"]["proxies"][0][
        "last_check"
    ] is None


@pytest.mark.asyncio
async def test_assign_is_bounded_and_skips_leased_trashed_missing_and_stale_profiles(proxy_client):
    proxy = await create(proxy_client)
    manager = get_profile_manager()
    good, leased, trash, stale = [
        await manager.create_profile(name, generate_fingerprint=False)
        for name in ("good", "leased", "trash", "stale")
    ]
    await manager.storage.acquire_lease(leased.id, "remote", 300)
    await manager.delete_profile(trash.id)
    await manager.update_profile(stale.id, {"notes": "changed"})
    request = {
        "proxy_row_version": 0,
        "profiles": [{"id": item.id, "row_version": 0} for item in (good, leased, trash, stale)]
        + [{"id": "missing", "row_version": 0}],
    }
    response = await proxy_client.post(f"/api/v1/proxies/{proxy['id']}/assign", json=request)
    assert response.status_code == 200
    assert response.json()["data"]["assigned"] == [good.id]
    assert len(response.json()["data"]["skipped"]) == 4 and SECRET not in response.text
    assert (await manager.get_profile(good.id)).proxy.password == SECRET
    too_many = {
        "proxy_row_version": 0,
        "profiles": [{"id": str(index), "row_version": 0} for index in range(101)],
    }
    assert (
        await proxy_client.post(f"/api/v1/proxies/{proxy['id']}/assign", json=too_many)
    ).status_code == 422
    assert (
        await proxy_client.post(
            f"/api/v1/proxies/{proxy['id']}/assign",
            json={
                "proxy_row_version": 99,
                "profiles": [{"id": good.id, "row_version": 1}],
            },
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_import_validates_all_lines_before_write_and_skips_duplicates(proxy_client):
    malformed = "http://account:import-secret@proxy.example:8080\nhttp://account:import-secret@proxy.example:70000"
    response = await proxy_client.post("/api/v1/proxies/import", json={"text": malformed})
    assert response.status_code == 422 and "Line 2" in response.text
    assert "import-secret" not in response.text and "proxy.example" not in response.text
    assert (await proxy_client.get("/api/v1/proxies")).json()["data"]["proxies"] == []
    text = "http://account:import-secret@proxy.example:8080\nhttp://account:import-secret@proxy.example:8080"
    imported = await proxy_client.post("/api/v1/proxies/import", json={"text": text})
    assert imported.status_code == 200 and "import-secret" not in imported.text
    assert len(imported.json()["data"]["created"]) == 1
    assert imported.json()["data"]["skipped"] == [{"line": 2, "reason": "Proxy already exists"}]
    repeated = await proxy_client.post("/api/v1/proxies/import", json={"text": text})
    assert repeated.json()["data"]["created"] == []
    assert len(repeated.json()["data"]["skipped"]) == 2


@pytest.mark.asyncio
async def test_missing_key_is_explicit_conflict_and_metadata_stays_available(
    proxy_client, monkeypatch
):
    proxy = await create(proxy_client)
    manager = get_profile_manager()
    profile = await manager.create_profile("target", generate_fingerprint=False)
    monkeypatch.delenv("CPM_SECRET_KEY")
    get_settings.cache_clear()
    for response in (
        await proxy_client.post("/api/v1/proxies", json=PAYLOAD),
        await proxy_client.post(f"/api/v1/proxies/{proxy['id']}/check"),
        await proxy_client.post(
            f"/api/v1/proxies/{proxy['id']}/assign",
            json={
                "proxy_row_version": 0,
                "profiles": [{"id": profile.id, "row_version": 0}],
            },
        ),
    ):
        assert response.status_code == 409
        assert SECRET not in response.text and "enc:" not in response.text
    assert (await proxy_client.get("/api/v1/proxies")).status_code == 200
    assert (await manager.get_profile(profile.id)).proxy is None


@pytest.mark.asyncio
async def test_every_inventory_route_uses_existing_auth_guard(proxy_client):
    manager = get_profile_manager()
    await manager.storage.create_user(
        auth.new_user_id(), "alice", auth.hash_password("test-password")
    )
    requests = [
        ("GET", "/api/v1/proxies", None),
        ("POST", "/api/v1/proxies", PAYLOAD),
        ("PATCH", "/api/v1/proxies/id", {"row_version": 0}),
        ("DELETE", "/api/v1/proxies/id?row_version=0", None),
        ("POST", "/api/v1/proxies/id/check", None),
        (
            "POST",
            "/api/v1/proxies/id/assign",
            {"proxy_row_version": 0, "profiles": [{"id": "id", "row_version": 0}]},
        ),
        ("POST", "/api/v1/proxies/import", {"text": "proxy.example:8080"}),
        ("GET", "/api/proxies", None),
    ]
    for method, path, body in requests:
        response = await proxy_client.request(method, path, json=body)
        assert response.status_code == 401 and SECRET not in response.text
    login = await proxy_client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "test-password"}
    )
    assert login.status_code == 200
    assert (await proxy_client.get("/api/v1/proxies")).status_code == 200
