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


async def test_backup_delete_requires_confirmation_and_preserves_profile(recovery_client):
    client = recovery_client
    profile = await create(client)
    cookies = Path(profile["storage_path"], "cookies.sqlite")
    cookies.write_bytes(b"cookies")
    result = await client.post("/api/v1/system/backups", json={})
    backup_id = result.json()["data"]["backups"][0]["id"]
    url = f"/api/v1/system/backups/{backup_id}"
    assert (await client.delete(url)).status_code == 400
    assert len((await client.get("/api/v1/system/backups")).json()["data"]["backups"]) == 1
    assert (await client.delete(url + "?confirm=true")).status_code == 200
    assert (await client.get("/api/v1/system/backups")).json()["data"]["backups"] == []
    assert (await client.get(f"/api/v1/profiles/{profile['id']}")).status_code == 200
    assert cookies.read_bytes() == b"cookies"
    assert (await client.delete(url + "?confirm=true")).status_code == 404
    assert (await client.delete("/api/v1/system/backups/invalid?confirm=true")).status_code == 400


async def test_backup_delete_requires_authentication(recovery_client, monkeypatch):
    from camoufox_pm.config import get_settings

    monkeypatch.setenv("CPM_API_KEY", "test-backup-delete-key")
    get_settings.cache_clear()
    try:
        response = await recovery_client.delete("/api/v1/system/backups/invalid?confirm=true")
        assert response.status_code == 401
    finally:
        get_settings.cache_clear()


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
    diagnostic = backup["skipped"][0]["diagnostic"]
    assert diagnostic["category"] == "profile_busy" and diagnostic["area"] == "backup"
    await storage.release_lease(profile_id, "remote")
    await client.delete(f"/api/v1/profiles/{profile_id}")
    await storage.acquire_lease(profile_id, "remote", 300, include_deleted=True)
    assert (await client.post(f"/api/v1/trash/profiles/{profile_id}/restore")).status_code == 409
    assert (
        await client.delete(f"/api/v1/trash/profiles/{profile_id}?confirm=true")
    ).status_code == 409


async def test_missing_profile_backup_reports_safe_lookup_without_creating_backup(recovery_client):
    client = recovery_client
    before = (await client.get("/api/v1/system/backups")).json()["data"]
    response = await client.post(
        "/api/v1/system/backups", json={"profile_ids": ["private-missing-profile"]}
    )
    assert response.status_code == 200
    value = response.json()["data"]
    assert not value["backups"] and len(value["skipped"]) == 1
    diagnostic = value["skipped"][0]["diagnostic"]
    assert diagnostic["version"] == 1 and diagnostic["area"] == "backup"
    assert diagnostic["stage"] == "profile_lookup" and diagnostic["category"] == "missing_profile"
    assert "private" not in str(diagnostic)
    assert (await client.get("/api/v1/system/backups")).json()["data"] == before


async def test_archive_failure_diagnostic_preserves_profile_and_releases_lease(
    recovery_client, monkeypatch
):
    import errno

    from camoufox_pm.core import profile_archive

    client = recovery_client
    profile = await create(client)
    data = Path(profile["storage_path"]) / "cookies.sqlite"
    data.write_bytes(b"private-session-content")

    def refuse(*args, **kwargs):
        raise OSError(errno.EACCES, "private-profile-path")

    monkeypatch.setattr(profile_archive, "export_profile", refuse)
    response = await client.post("/api/v1/system/backups", json={"profile_ids": [profile["id"]]})
    value = response.json()["data"]
    assert response.status_code == 200 and not value["backups"]
    diagnostic = value["skipped"][0]["diagnostic"]
    assert diagnostic["category"] == "permission_denied"
    assert diagnostic["area"] == "backup" and diagnostic["stage"] == "backup_profile"
    assert "private" not in str(diagnostic)
    assert data.read_bytes() == b"private-session-content"
    assert await get_profile_manager().storage.get_lease(profile["id"]) == (None, None)
    assert not (await client.get("/api/v1/system/backups")).json()["data"]["backups"]


async def test_due_backup_holds_launch_admission_then_stays_fresh(recovery_client, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from scripts.installed_sync_acceptance import installed_api_error

    client = recovery_client
    profile = await create(client)
    manager = get_profile_manager()
    backup = ProfileBackupManager(manager)
    original = backup._write_locked_profile
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(backup, "_write_locked_profile", hold)
    launch = AsyncMock(return_value={"status": "launched"})
    monkeypatch.setattr(manager, "_launch_browser", launch)
    task = asyncio.create_task(backup.create_due_backups())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        refused = await client.post(f"/api/v1/profiles/{profile['id']}/launch", json={})
        assert refused.status_code == 409
        evidence = installed_api_error(SimpleNamespace(status=409, json=refused.json))
        assert evidence.category == "profile_busy"
        assert profile["id"] not in repr(evidence.__dict__)
        launch.assert_not_awaited()
    finally:
        release.set()
        result = await task
    assert len(result["backups"]) == 1 and result["skipped"] == []
    assert await manager.storage.get_lease(profile["id"]) == (None, None)
    assert await backup.create_due_backups() == {"backups": [], "skipped": []}
    accepted = await client.post(f"/api/v1/profiles/{profile['id']}/launch", json={})
    assert accepted.status_code == 200
    launch.assert_awaited_once()
