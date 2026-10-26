"""Explicit proxy pair API, partial auditing and revocation across API aliases."""

import copy
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth, proxy_inventory


async def pairs(client):
    manager = get_profile_manager()
    result = []
    for i in range(2):
        proxy = (
            await client.post(
                "/api/v1/proxies",
                json={"name": f"Proxy {i}", "type": "socks5", "server": f"proxy{i}.example:1080"},
            )
        ).json()["data"]
        profile = await manager.create_profile(f"Work {i}", generate_fingerprint=False)
        result.append(
            {
                "profile": {"id": profile.id, "row_version": 0},
                "proxy": {"id": proxy["id"], "row_version": 0},
            }
        )
    return {"assignments": result}


@pytest.mark.asyncio
async def test_preview_has_no_profile_writes_and_replay_cannot_overwrite(client):
    data = await pairs(client)
    preview = await client.post("/api/proxies/assignment-preview", json=data)
    assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
    assert [item["proxy_id"] for item in preview.json()["data"]["items"]] == [
        item["proxy"]["id"] for item in data["assignments"]
    ]
    storage = get_profile_manager().storage
    for item in data["assignments"]:
        assert (await storage.get_profile(item["profile"]["id"])).row_version == 0
    executed = await client.post("/api/v1/proxies/assign-sequence", json=data)
    assert executed.status_code == 200 and not executed.json()["errors"]
    assert executed.headers["cache-control"] == "no-store"
    replayed = await client.post("/api/proxies/assign-sequence", json=data)
    assert replayed.status_code == 200 and replayed.json()["data"]["assigned"] == []
    assert len(replayed.json()["errors"]) == 2
    events = storage.db._connection.execute(
        "SELECT result FROM audit_events WHERE action='assign_proxy_sequence'"
    ).fetchall()
    assert [row[0] for row in events] == ["success", "partial"]
    assert (
        storage.db._connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='assign_proxy_sequence_profile'"
        ).fetchone()[0]
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "viewer"])
async def test_both_aliases_are_admin_only(client, role):
    data = await pairs(client)
    storage = get_profile_manager().storage
    await storage.create_user("admin-id", "admin", "unused")
    await storage.create_user("other-id", "other", "unused", role)
    client.cookies.set(auth.SESSION_COOKIE, await auth.create_session(storage, "other-id"))
    for prefix in ("/api", "/api/v1"):
        for path in ("assignment-preview", "assign-sequence"):
            assert (await client.post(f"{prefix}/proxies/{path}", json=data)).status_code == 403
    for item in data["assignments"]:
        assert (await storage.get_profile(item["profile"]["id"])).proxy is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["empty", "many", "duplicate-profile", "duplicate-proxy", "password"]
)
async def test_bounds_uniqueness_and_extra_secrets_are_rejected_without_writes(client, invalid):
    data = await pairs(client)
    if invalid == "empty":
        data["assignments"] = []
    elif invalid == "many":
        data["assignments"] *= 51
    elif invalid == "duplicate-profile":
        data["assignments"][1]["profile"] = copy.deepcopy(data["assignments"][0]["profile"])
    elif invalid == "duplicate-proxy":
        data["assignments"][1]["proxy"] = copy.deepcopy(data["assignments"][0]["proxy"])
    else:
        data["assignments"][0]["proxy"]["password"] = "private-input-secret"
    for path in ("assignment-preview", "assign-sequence"):
        response = await client.post(f"/api/v1/proxies/{path}", json=data)
        assert response.status_code == 422 and "private-input-secret" not in response.text
    assert all(
        profile.proxy is None for profile in await get_profile_manager().storage.list_profiles()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["assign-sequence", "legacy"])
async def test_real_session_revocation_inside_lease_prevents_assignment(client, monkeypatch, path):
    data = await pairs(client)
    manager = get_profile_manager()
    await manager.storage.create_user("admin-id", "admin", "unused")
    await manager.storage.create_user("backup-id", "backup", "unused")
    client.cookies.set(auth.SESSION_COOKIE, await auth.create_session(manager.storage, "admin-id"))
    original = proxy_inventory.profile_operation

    @asynccontextmanager
    async def revoked(*args, **kwargs):
        async with original(*args, **kwargs) as profile:
            await manager.storage.update_user_access("admin", role="viewer")
            yield profile

    monkeypatch.setattr(proxy_inventory, "profile_operation", revoked)
    if path == "legacy":
        proxy = data["assignments"][0]["proxy"]
        response = await client.post(
            f"/api/v1/proxies/{proxy['id']}/assign",
            json={
                "proxy_row_version": 0,
                "profiles": [item["profile"] for item in data["assignments"]],
            },
        )
    else:
        response = await client.post("/api/v1/proxies/assign-sequence", json=data)
    assert response.status_code == 403
    assert all(profile.proxy is None for profile in await manager.storage.list_profiles())
    for item in data["assignments"]:
        assert (await manager.storage.get_lease(item["profile"]["id"]))[0] is None


@pytest.mark.asyncio
async def test_missing_key_blocks_entire_sequence_without_reflecting_credentials(
    client, monkeypatch
):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        data = await pairs(client)
        proxy_id = data["assignments"][1]["proxy"]["id"]
        response = await client.patch(
            f"/api/v1/proxies/{proxy_id}",
            json={"row_version": 0, "username": "private-user", "password": "private-password"},
        )
        data["assignments"][1]["proxy"]["row_version"] = response.json()["data"]["row_version"]
        monkeypatch.delenv("CPM_SECRET_KEY")
        get_settings.cache_clear()
        response = await client.post("/api/v1/proxies/assign-sequence", json=data)
        assert response.status_code == 409
        assert (
            "private-user" not in response.text
            and "private-password" not in response.text
            and "enc:" not in response.text
        )
        assert all(
            profile.proxy is None for profile in await get_profile_manager().storage.list_profiles()
        )
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["role", "session"])
async def test_revocation_between_rows_preserves_committed_work_and_stops_the_rest(
    client, monkeypatch, revocation
):
    data = await pairs(client)
    manager = get_profile_manager()
    await manager.storage.create_user("admin-id", "admin", "unused")
    await manager.storage.create_user("backup-id", "backup", "unused")
    token = await auth.create_session(manager.storage, "admin-id")
    client.cookies.set(auth.SESSION_COOKIE, token)
    original = proxy_inventory.ProxyInventory.assign

    async def revoke_after_first(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if result["assigned"]:
            if revocation == "role":
                await manager.storage.update_user_access("admin", role="viewer")
            else:
                await manager.storage.delete_session(auth.hash_token(token))
        return result

    monkeypatch.setattr(proxy_inventory.ProxyInventory, "assign", revoke_after_first)
    response = await client.post("/api/v1/proxies/assign-sequence", json=data)
    assert response.status_code == 403
    first, second = [
        await manager.storage.get_profile(pair["profile"]["id"]) for pair in data["assignments"]
    ]
    assert first.row_version == 1 and first.proxy is not None
    assert second.row_version == 0 and second.proxy is None
    assert (
        manager.storage.db._connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='assign_proxy_sequence_profile'"
        ).fetchone()[0]
        == 1
    )
