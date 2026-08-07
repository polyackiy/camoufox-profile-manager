"""Shared pytest fixtures."""

import pytest

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager


@pytest.fixture
async def storage(tmp_path):
    """A StorageManager backed by a throwaway SQLite database."""
    manager = StorageManager(str(tmp_path / "test.db"))
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
async def profile_manager(tmp_path):
    """A ProfileManager backed by a throwaway data directory."""
    storage = StorageManager(str(tmp_path / "test.db"))
    await storage.initialize()
    manager = ProfileManager(storage, str(tmp_path))
    await manager.initialize()
    yield manager
    await storage.close()
