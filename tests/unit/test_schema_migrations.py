import sqlite3

import pytest

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.schema_migrations import CURRENT_SCHEMA


@pytest.mark.asyncio
async def test_future_schema_refused_without_changes(tmp_path):
    path = tmp_path / "db.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    storage = StorageManager(str(path))
    for _ in range(2):
        with pytest.raises(ValueError, match="newer manager"):
            await storage.initialize()
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


@pytest.mark.asyncio
async def test_baseline_migration_numbered_and_retryable(tmp_path):
    path = tmp_path / "db.sqlite"
    for _ in range(2):
        storage = StorageManager(str(path))
        await storage.initialize()
        await storage.close()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
