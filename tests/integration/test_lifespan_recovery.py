"""Exercise real lifespan startup/shutdown, which ASGITransport does not run."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from camoufox_pm import main
from camoufox_pm.api import dependencies
from camoufox_pm.api.routes import recovery
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager


@pytest.fixture
async def startup_database(tmp_path, monkeypatch):
    db_path = tmp_path / "profiles.db"
    storage = StorageManager(str(db_path))
    await storage.initialize()
    manager = ProfileManager(storage, str(tmp_path))
    for name in ("active account", "trashed account"):
        profile = await manager.create_profile(name, generate_fingerprint=False)
        Path(profile.storage_path, "cookies.sqlite").write_bytes(name.encode())
        if name.startswith("trashed"):
            await manager.delete_profile(profile.id)
    await storage.close()
    monkeypatch.setattr(
        main, "settings", main.settings.model_copy(update={"db_path": str(db_path)})
    )
    for variable in ("_storage_manager", "_profile_manager", "_scheduler"):
        monkeypatch.setattr(dependencies, variable, None)
    monkeypatch.setattr(recovery, "_backup_manager", None)
    return db_path


@pytest.mark.asyncio
async def test_real_lifespan_automatically_backs_up_active_and_trashed_profiles(startup_database):
    tasks_before = asyncio.all_tasks()
    async with main.lifespan(main.app):
        backup_manager = recovery.get_backup_manager()
        manager = dependencies.get_profile_manager()
        scheduler = dependencies.get_scheduler()
        deadline = asyncio.get_running_loop().time() + 3
        while len(backup_manager.list_backups()) < 2:
            assert asyncio.get_running_loop().time() < deadline, "startup backups did not finish"
            await asyncio.sleep(0.005)
        assert [item["reason"] for item in backup_manager.list_backups()] == [
            "automatic",
            "automatic",
        ]
        assert len(await manager.list_trashed_profiles()) == 1
        created_tasks = asyncio.all_tasks() - tasks_before
        assert any(task.get_name() == "profile-backups" for task in created_tasks)
    assert manager.storage.db._connection is None
    assert manager.browser_sessions._heartbeat_task is None
    assert scheduler._task is None
    assert all(task.done() for task in created_tasks)


@pytest.mark.asyncio
async def test_failed_lifespan_startup_closes_database_and_background_tasks(
    startup_database, monkeypatch
):
    tasks_before = asyncio.all_tasks()
    monkeypatch.setattr(main.TaskScheduler, "start", AsyncMock(side_effect=RuntimeError("startup")))
    try:
        with pytest.raises(RuntimeError, match="startup"):
            async with main.lifespan(main.app):
                pytest.fail("failed startup yielded an app")
        manager = dependencies.get_profile_manager()
        tasks_after = asyncio.all_tasks() - tasks_before
        assert manager.storage.db._connection is None
        assert manager.browser_sessions._heartbeat_task is None
        assert not tasks_after
    finally:
        # Keep the suite clean even if the startup regression recurs.
        leaked = asyncio.all_tasks() - tasks_before
        for task in leaked:
            task.cancel()
        if leaked:
            await asyncio.gather(*leaked, return_exceptions=True)
        if dependencies._storage_manager is not None:
            await dependencies._storage_manager.close()
