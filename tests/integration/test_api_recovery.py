"""Recovery API actions are explicit, guarded and never overwrite a profile."""

from pathlib import Path

import pytest

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.routes.recovery import set_backup_manager
from camoufox_pm.core.backups import ProfileBackupManager


@pytest.fixture
async def recovery_client(client):
    set_backup_manager(ProfileBackupManager(get_profile_manager()))
    yield client
    set_backup_manager(None)


async def create(client):
    result = await client.post(
        "/api/v1/profiles", json={"name": "Recoverable", "generate_fingerprint": False}
    )
    assert result.status_code == 201
    return result.json()


@pytest.mark.asyncio
async def test_trash_restore_and_explicit_permanent_delete(recovery_client):
    client = recovery_client
    profile = await create(client)
    profile_id = profile["id"]
    assert (await client.delete(f"/api/v1/profiles/{profile_id}")).status_code == 200
    assert (await client.get(f"/api/v1/profiles/{profile_id}")).status_code == 404
    trash = (await client.get("/api/v1/trash/profiles")).json()["data"]["profiles"]
    assert trash[0]["id"] == profile_id and trash[0]["deleted_at"]
    denied = await client.delete(f"/api/v1/trash/profiles/{profile_id}")
    assert denied.status_code == 400
    restored = await client.post(f"/api/v1/trash/profiles/{profile_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["data"]["id"] == profile_id
    await client.delete(f"/api/v1/profiles/{profile_id}")
    deleted = await client.delete(f"/api/v1/trash/profiles/{profile_id}?confirm=true")
    assert deleted.status_code == 200
    assert (await client.get("/api/v1/trash/profiles")).json()["data"]["profiles"] == []


@pytest.mark.asyncio
async def test_backup_create_list_and_additive_restore(recovery_client):
    client = recovery_client
    profile = await create(client)
    Path(profile["storage_path"], "cookies.sqlite").write_bytes(b"cookies")
    result = await client.post("/api/v1/system/backups", json={"profile_ids": [profile["id"]]})
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["skipped"] == []
    backup_id = data["backups"][0]["id"]
    assert (await client.get("/api/v1/system/backups")).json()["data"]["backups"][0][
        "id"
    ] == backup_id
    restored = await client.post(f"/api/v1/system/backups/{backup_id}/restore", json={})
    assert restored.status_code == 200
    fresh = restored.json()["data"]
    assert fresh["id"] != profile["id"]
    assert Path(fresh["storage_path"], "cookies.sqlite").read_bytes() == b"cookies"
    assert (await client.get("/api/v1/profiles")).json()["total"] == 2


@pytest.mark.asyncio
async def test_lease_conflicts_are_409_and_backups_report_skips(recovery_client):
    client = recovery_client
    profile = await create(client)
    profile_id = profile["id"]
    storage = get_profile_manager().storage
    await storage.acquire_lease(profile_id, "remote", 300)
    assert (await client.delete(f"/api/v1/profiles/{profile_id}")).status_code == 409
    assert (
        await client.post(f"/api/v1/profiles/{profile_id}/clone", json={"new_name": "copy"})
    ).status_code == 409
    backup = (await client.post("/api/v1/system/backups", json={})).json()["data"]
    assert backup["backups"] == [] and backup["skipped"][0]["profile_id"] == profile_id
    await storage.release_lease(profile_id, "remote")
    await client.delete(f"/api/v1/profiles/{profile_id}")
    await storage.acquire_lease(profile_id, "remote", 300, include_deleted=True)
    assert (await client.post(f"/api/v1/trash/profiles/{profile_id}/restore")).status_code == 409
    assert (
        await client.delete(f"/api/v1/trash/profiles/{profile_id}?confirm=true")
    ).status_code == 409
