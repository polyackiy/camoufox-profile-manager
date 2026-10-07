"""Recovery must preserve existing identities and refuse live browser data."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from camoufox_pm.core import profile_archive
from camoufox_pm.core.backups import ProfileBackupManager
from camoufox_pm.core.cleanup import ProfileCleanupManager
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.leases import ProfileLocked
from camoufox_pm.core.models import Schedule, ScheduleAction, ScheduleKind
from camoufox_pm.core.profile_operations import profile_operation
from camoufox_pm.core.scheduler import TaskScheduler


async def seeded(manager):
    profile = await manager.create_profile(
        "account",
        generate_fingerprint=False,
        proxy_config={"type": "http", "server": "localhost:8080", "password": "secret"},
    )
    path = Path(profile.storage_path)
    (path / "cookies.sqlite").write_bytes(b"identity")
    profile.fingerprint = {"screen.width": 1920}
    await manager.storage.save_profile(profile)
    return profile, path


@pytest.mark.asyncio
async def test_trash_is_hidden_but_retains_data_and_pauses_schedules(profile_manager):
    profile, path = await seeded(profile_manager)
    schedule = Schedule(
        profile_id=profile.id,
        action=ScheduleAction.LAUNCH,
        kind=ScheduleKind.INTERVAL,
        interval_minutes=5,
    )
    await profile_manager.storage.save_schedule(schedule)
    assert await profile_manager.delete_profile(profile.id)
    assert await profile_manager.list_profiles() == []
    assert await profile_manager.storage.count_profiles() == 0
    assert await profile_manager.storage.get_profile(profile.id) is None
    assert (path / "cookies.sqlite").read_bytes() == b"identity"
    assert not (await profile_manager.storage.get_schedule(schedule.id)).enabled
    scheduler = TaskScheduler(profile_manager.storage, profile_manager)
    profile_manager.launch_browser = AsyncMock()
    await scheduler.run_now(schedule)
    profile_manager.launch_browser.assert_not_called()
    restored = await profile_manager.restore_trashed_profile(profile.id)
    assert restored.id == profile.id and restored.deleted_at is None
    assert not (await profile_manager.storage.get_schedule(schedule.id)).enabled


@pytest.mark.asyncio
async def test_trash_survives_cleanup_even_with_a_stale_orphan_list(profile_manager):
    profile, path = await seeded(profile_manager)
    await profile_manager.delete_profile(profile.id)
    cleanup = ProfileCleanupManager(
        str(profile_manager.data_dir), str(profile_manager.storage.db.db_path)
    )
    await cleanup.initialize()
    try:
        assert await cleanup.find_orphaned_profile_directories() == []
        assert (
            await cleanup.cleanup_orphaned_directories(
                [{"path": path, "profile_id": profile.id, "size_mb": 0}], confirm=False
            )
            == 0
        )
        assert path.exists()
    finally:
        await cleanup.close()


@pytest.mark.asyncio
async def test_permanent_delete_requires_trash_and_rejects_remote_lease(profile_manager):
    profile, path = await seeded(profile_manager)
    assert not await profile_manager.permanently_delete_profile(profile.id)
    await profile_manager.delete_profile(profile.id)
    assert await profile_manager.storage.acquire_lease(
        profile.id, "other", 300, include_deleted=True
    )
    with pytest.raises(ProfileLocked):
        await profile_manager.permanently_delete_profile(profile.id)
    assert path.exists()
    await profile_manager.storage.release_lease(profile.id, "other")
    assert await profile_manager.permanently_delete_profile(profile.id)
    assert not path.exists()
    assert await profile_manager.storage.get_profile(profile.id, include_deleted=True) is None


@pytest.mark.asyncio
async def test_leased_profile_cannot_be_deleted_cloned_exported_or_backed_up(profile_manager):
    profile, path = await seeded(profile_manager)
    await profile_manager.storage.acquire_lease(profile.id, "remote", 300)
    with pytest.raises(ProfileLocked):
        await profile_manager.delete_profile(profile.id)
    with pytest.raises(ProfileLocked):
        await profile_manager.clone_profile(profile.id, "clone")
    with pytest.raises(ProfileLocked):
        await profile_manager.export_profile(profile.id, profile_manager.data_dir / "export.zip")
    result = await ProfileBackupManager(profile_manager).create_backups()
    assert not result["backups"] and result["skipped"][0]["profile_id"] == profile.id
    assert path.exists()


@pytest.mark.asyncio
async def test_operation_lock_blocks_other_instance_launch_and_releases(profile_manager):
    profile, _ = await seeded(profile_manager)
    other = StorageManager(str(profile_manager.storage.db.db_path))
    await other.initialize()
    try:
        async with profile_operation(profile_manager, profile.id):
            assert not await other.acquire_lease(profile.id, "another-instance", 300)
        assert await other.acquire_lease(profile.id, "another-instance", 300)
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_backup_restore_creates_new_identity_and_survives_source_deletion(profile_manager):
    profile, path = await seeded(profile_manager)
    backups = ProfileBackupManager(profile_manager)
    result = await backups.create_backups()
    backup = result["backups"][0]
    assert backup["size_bytes"] > 0
    (path / "cookies.sqlite").write_bytes(b"newer")
    restored = await backups.restore_backup(backup["id"], name="Recovered account")
    assert restored.id != profile.id and restored.name == "Recovered account"
    assert restored.fingerprint == profile.fingerprint
    assert restored.proxy.password == "secret"
    assert Path(restored.storage_path, "cookies.sqlite").read_bytes() == b"identity"
    assert (path / "cookies.sqlite").read_bytes() == b"newer"
    await profile_manager.delete_profile(profile.id)
    await profile_manager.permanently_delete_profile(profile.id)
    again = await backups.restore_backup(backup["id"])
    assert Path(again.storage_path, "cookies.sqlite").read_bytes() == b"identity"


@pytest.mark.asyncio
async def test_retention_keeps_update_checkpoints_separate_and_backs_up_trash(profile_manager):
    profile, _ = await seeded(profile_manager)
    backups = ProfileBackupManager(profile_manager, retention=1)
    for _ in range(3):
        await backups.create_backups(reason="before_update")
    for _ in range(2):
        await backups.create_backups()
    assert sorted(item["reason"] for item in backups.list_backups()) == [
        "before_update",
        "before_update",
        "manual",
    ]
    assert (await backups.create_due_backups()) == {"backups": [], "skipped": []}
    await profile_manager.delete_profile(profile.id)
    result = await backups.create_backups(reason="before_update")
    assert result["backups"][0]["profile_id"] == profile.id
    restored = await backups.restore_backup(result["backups"][0]["id"])
    assert restored.deleted_at is None


@pytest.mark.asyncio
async def test_failed_archive_does_not_publish_partial_backup(profile_manager, monkeypatch):
    profile, _ = await seeded(profile_manager)
    backups = ProfileBackupManager(profile_manager)

    def broken(profile, data, destination, **kwargs):
        destination.write_bytes(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(profile_archive, "export_profile", broken)
    result = await backups.create_backups()
    assert result["skipped"] and backups.list_backups() == []
    assert list(backups.directory.iterdir()) == []
    assert (await profile_manager.storage.get_lease(profile.id))[0] is None


@pytest.mark.asyncio
async def test_backup_paths_and_symbolic_links_cannot_escape_storage(profile_manager, tmp_path):
    profile, path = await seeded(profile_manager)
    backups = ProfileBackupManager(profile_manager)
    with pytest.raises(ValueError):
        await backups.restore_backup("../../some-file")
    secret = tmp_path / "outside"
    secret.write_text("private")
    (path / "secret").symlink_to(secret)
    result = await backups.create_backups()
    assert result["skipped"] and backups.list_backups() == []
    assert secret.read_text() == "private"


@pytest.mark.asyncio
async def test_cancelled_backup_keeps_lease_until_io_finishes(profile_manager, monkeypatch):
    profile, _ = await seeded(profile_manager)
    backups = ProfileBackupManager(profile_manager)
    started, finish = threading.Event(), threading.Event()
    original = profile_archive.export_profile

    def slow(*args, **kwargs):
        started.set()
        assert finish.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(profile_archive, "export_profile", slow)
    task = asyncio.create_task(backups.create_backups())
    while not started.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not await profile_manager.storage.acquire_lease(profile.id, "racing-launch", 300)
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await profile_manager.storage.get_lease(profile.id))[0] is None
    assert list(backups.directory.iterdir()) == []


@pytest.mark.asyncio
async def test_legacy_relative_profile_path_survives_changed_working_directory(
    profile_manager, monkeypatch, tmp_path
):
    profile, path = await seeded(profile_manager)
    profile.storage_path = f"data/profiles/profile_{profile.id}"
    await profile_manager.storage.save_profile(profile)
    new_cwd = tmp_path / "desktop-shortcut-working-directory"
    new_cwd.mkdir()
    monkeypatch.chdir(new_cwd)
    loaded = await profile_manager.get_profile(profile.id)
    assert Path(loaded.storage_path) == path.resolve()
    backups = ProfileBackupManager(profile_manager)
    result = await backups.create_backups()
    assert result["skipped"] == []
    fresh = await backups.restore_backup(result["backups"][0]["id"])
    assert Path(fresh.storage_path, "cookies.sqlite").read_bytes() == b"identity"
    await profile_manager.delete_profile(profile.id)
    assert await profile_manager.permanently_delete_profile(profile.id)
    assert not path.exists()


@pytest.mark.asyncio
async def test_backup_refuses_unreadable_credentials_and_keeps_previous_checkpoint(
    profile_manager, monkeypatch
):
    from cryptography.fernet import Fernet

    from camoufox_pm.config import get_settings

    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        profile, _ = await seeded(profile_manager)
        backups = ProfileBackupManager(profile_manager)
        good = (await backups.create_backups())["backups"][0]
        monkeypatch.delenv("CPM_SECRET_KEY")
        get_settings.cache_clear()
        result = await backups.create_backups(reason="before_update")
        assert result["backups"] == []
        assert "could not be decrypted" in result["skipped"][0]["reason"]
        assert [backup["id"] for backup in backups.list_backups()] == [good["id"]]
    finally:
        get_settings.cache_clear()
