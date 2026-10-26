"""Batch previews, retry identity and live administrator authorization over both API aliases."""

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth, profile_templates


async def template(client):
    response = await client.post("/api/v1/templates", json={"name": "Work defaults"})
    assert response.status_code == 201
    return response.json()["data"]


def batch(**changes):
    return {
        "batch_id": str(uuid4()),
        "row_version": 0,
        "names": ["Work 001", "Work 002"],
        "group": None,
        "proxy_refs": [],
        "allow_duplicate_names": False,
        **changes,
    }


@pytest.mark.asyncio
async def test_preview_creates_nothing_and_saved_results_survive_alias_retries(client):
    entry = await template(client)
    data = batch()
    checked = await client.post(f"/api/v1/templates/{entry['id']}/batch-preview", json=data)
    assert checked.status_code == 200 and checked.json()["data"]["names"] == data["names"]
    assert checked.headers["cache-control"] == "no-store"
    assert (await client.get("/api/v1/template-profile-batches")).json()["data"]["batches"] == []
    manager = get_profile_manager()
    assert not await manager.storage.list_profiles()
    first = await client.post(f"/api/v1/templates/{entry['id']}/profile-batches", json=data)
    assert first.status_code == 200 and first.json()["errors"] == []
    repeated = await client.post(f"/api/templates/{entry['id']}/profile-batches", json=data)
    assert repeated.json()["data"] == first.json()["data"]
    assert len(await manager.storage.list_profiles()) == 2
    saved = await client.get(f"/api/template-profile-batches/{data['batch_id']}")
    assert saved.json()["data"] == first.json()["data"]
    assert saved.headers["cache-control"] == "no-store"
    events = manager.storage.db._connection.execute(
        "SELECT action FROM audit_events WHERE action='create_profile_from_template_batch'"
    ).fetchall()
    assert len(events) == 2, "replaying the response must not claim another profile was created"


@pytest.mark.asyncio
async def test_changed_intent_cannot_reuse_a_creation_receipt(client):
    entry = await template(client)
    data = batch(names=["Original"])
    assert (
        await client.post(f"/api/v1/templates/{entry['id']}/profile-batches", json=data)
    ).status_code == 200
    response = await client.post(
        f"/api/v1/templates/{entry['id']}/profile-batches", json={**data, "names": ["Different"]}
    )
    assert response.status_code == 409
    assert len(await get_profile_manager().storage.list_profiles()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"names": ["x"] * 26},
        {"names": [" "]},
        {"proxy_refs": [{"id": "one", "row_version": 0}]},
        {"password": "private-provider-password"},
        {"fingerprint": {"x": "private-cookie"}},
    ],
)
async def test_invalid_batches_do_not_write_profiles_or_receipts(client, changes):
    entry = await template(client)
    response = await client.post(
        f"/api/v1/templates/{entry['id']}/profile-batches", json=batch(**changes)
    )
    assert response.status_code == 422
    assert (
        "private-provider-password" not in response.text and "private-cookie" not in response.text
    )
    assert not await get_profile_manager().storage.list_profiles()
    assert (await client.get("/api/v1/template-profile-batches")).json()["data"]["batches"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "viewer"])
async def test_only_administrators_can_preview_create_or_read_batches(client, role):
    entry = await template(client)
    storage = get_profile_manager().storage
    await storage.create_user("admin-id", "admin", "unused")
    await storage.create_user("other-id", "other", "unused", role)
    token = await auth.create_session(storage, "other-id")
    client.cookies.set(auth.SESSION_COOKIE, token)
    for prefix in ("/api", "/api/v1"):
        assert (await client.get(f"{prefix}/template-profile-batches")).status_code == 403
        assert (await client.get(f"{prefix}/template-profile-batches/{uuid4()}")).status_code == 403
        assert (
            await client.post(f"{prefix}/templates/{entry['id']}/batch-preview", json=batch())
        ).status_code == 403
        assert (
            await client.post(f"{prefix}/templates/{entry['id']}/profile-batches", json=batch())
        ).status_code == 403
    assert not await storage.list_profiles()


@pytest.mark.asyncio
async def test_admin_revocation_during_file_preparation_blocks_publication(client, monkeypatch):
    entry = await template(client)
    manager = get_profile_manager()
    await manager.storage.create_user("admin-id", "admin", "unused")
    await manager.storage.create_user("backup-id", "backup", "unused")
    client.cookies.set(auth.SESSION_COOKIE, await auth.create_session(manager.storage, "admin-id"))
    original = profile_templates.ProfileTemplates.create_profile

    async def revoke(self, *args, **kwargs):
        admission = kwargs["before_publish"]

        async def after_files():
            await manager.storage.update_user_access("admin", role="viewer")
            await admission()

        return await original(self, *args, **{**kwargs, "before_publish": after_files})

    monkeypatch.setattr(profile_templates.ProfileTemplates, "create_profile", revoke)
    response = await client.post(f"/api/v1/templates/{entry['id']}/profile-batches", json=batch())
    assert response.status_code == 403
    assert not await manager.storage.list_profiles()
    assert not list(manager.profiles_dir.glob("profile_*"))


@pytest.mark.asyncio
async def test_partial_batch_results_are_audited_as_partial_and_do_not_leak_errors(
    client, monkeypatch
):
    entry = await template(client)
    install = profile_templates._install_snapshots
    calls = 0

    def failure(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private-provider-password")
        return install(*args)

    monkeypatch.setattr(profile_templates, "_install_snapshots", failure)
    response = await client.post(f"/api/v1/templates/{entry['id']}/profile-batches", json=batch())
    assert response.status_code == 200 and response.json()["errors"] == [1]
    assert "private-provider-password" not in response.text
    events = (
        get_profile_manager()
        .storage.db._connection.execute(
            "SELECT result FROM audit_events WHERE action='create_template_batch'"
        )
        .fetchall()
    )
    assert [row[0] for row in events] == ["partial"]


@pytest.mark.asyncio
async def test_unavailable_proxy_key_is_actionable_and_creates_nothing(client, monkeypatch):
    entry = await template(client)
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        proxy = await client.post(
            "/api/v1/proxies",
            json={
                "name": "Private proxy",
                "type": "http",
                "server": "proxy.example:80",
                "username": "provider-user",
                "password": "private-provider-password",
            },
        )
        assert proxy.status_code == 201
        proxy = proxy.json()["data"]
        monkeypatch.delenv("CPM_SECRET_KEY")
        get_settings.cache_clear()
        data = batch(names=["one"], proxy_refs=[{"id": proxy["id"], "row_version": 0}])
        for suffix in ("batch-preview", "profile-batches"):
            response = await client.post(f"/api/v1/templates/{entry['id']}/{suffix}", json=data)
            assert response.status_code == 409
            assert "Restore the manager encryption key" in response.text
            assert "private-provider-password" not in response.text and "enc:" not in response.text
        assert not await get_profile_manager().storage.list_profiles()
        assert (await client.get("/api/v1/template-profile-batches")).json()["data"][
            "batches"
        ] == []
    finally:
        get_settings.cache_clear()
