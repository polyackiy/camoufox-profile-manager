import sqlite3
from contextlib import closing

import pytest

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.schema_migrations import CURRENT_SCHEMA


@pytest.mark.asyncio
async def test_future_schema_refused_without_changes(tmp_path):
    path = tmp_path / "db.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA user_version=99")
    storage = StorageManager(str(path))
    for _ in range(2):
        with pytest.raises(ValueError, match="newer manager"):
            await storage.initialize()
    with closing(sqlite3.connect(path)) as db, db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


@pytest.mark.asyncio
async def test_baseline_migration_numbered_and_retryable(tmp_path):
    path = tmp_path / "db.sqlite"
    for _ in range(2):
        storage = StorageManager(str(path))
        await storage.initialize()
        await storage.close()
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.asyncio
async def test_stage_two_existing_accounts_become_administrators(tmp_path):
    path = tmp_path / "legacy.sqlite"
    storage = StorageManager(str(path))
    await storage.initialize()
    await storage.close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE profile_grants")
        db.execute("DROP TABLE audit_events")
        db.execute("ALTER TABLE users DROP COLUMN role")
        db.execute("ALTER TABLE schedules DROP COLUMN actor_kind")
        db.execute("ALTER TABLE schedules DROP COLUMN actor_username")
        db.execute("ALTER TABLE schedules DROP COLUMN actor_user_id")
        db.execute(
            "INSERT INTO users(id,username,password_hash,created_at) VALUES ('existing','host-owner','hash','2026-01-01')"
        )
        db.execute("PRAGMA user_version=1")
    storage = StorageManager(str(path))
    await storage.initialize()
    assert (await storage.get_user_by_username("host-owner"))["role"] == "administrator"
    await storage.close()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA


@pytest.mark.asyncio
async def test_partial_schedule_actor_migration_resumes(tmp_path):
    path = tmp_path / "partial.sqlite"
    storage = StorageManager(str(path))
    await storage.initialize()
    await storage.close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("ALTER TABLE schedules DROP COLUMN actor_username")
        db.execute("ALTER TABLE schedules DROP COLUMN actor_user_id")
        db.execute("PRAGMA user_version=1")
    storage = StorageManager(str(path))
    await storage.initialize()
    assert await storage.list_schedules() == []
    await storage.close()
    with closing(sqlite3.connect(path)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(schedules)")}
        assert {"actor_username", "actor_user_id"} <= columns


@pytest.mark.asyncio
async def test_sync_schema_refuses_pre_sync_manager(tmp_path, monkeypatch):
    from camoufox_pm.core import schema_migrations

    path = tmp_path / "sync.sqlite"
    storage = StorageManager(str(path))
    await storage.initialize()
    await storage.close()
    with closing(sqlite3.connect(path)) as db:
        assert (
            db.execute(
                "SELECT version FROM manager_features WHERE feature='sync-admission'"
            ).fetchone()[0]
            == 1
        )
        monkeypatch.setattr(schema_migrations, "CURRENT_SCHEMA", 2)
        with pytest.raises(ValueError, match="newer manager"):
            schema_migrations.check_schema(db)


@pytest.mark.asyncio
async def test_lifecycle_schema_refuses_pre_lifecycle_manager(tmp_path, monkeypatch):
    from camoufox_pm.core import schema_migrations

    path = tmp_path / "lifecycle.sqlite"
    storage = StorageManager(str(path))
    await storage.initialize()
    await storage.close()
    with closing(sqlite3.connect(path)) as db:
        assert (
            db.execute(
                "SELECT version FROM manager_features WHERE feature='sync-lifecycle'"
            ).fetchone()[0]
            == 1
        )
        monkeypatch.setattr(schema_migrations, "CURRENT_SCHEMA", 3)
        with pytest.raises(ValueError, match="newer manager"):
            schema_migrations.check_schema(db)
