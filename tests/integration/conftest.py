"""Fixtures for API integration tests."""

import httpx
import pytest
from httpx import ASGITransport

from camoufox_pm.api import dependencies
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.main import app


@pytest.fixture
async def client(tmp_path):
    """An HTTP client wired to the app with a throwaway database."""
    storage = StorageManager(str(tmp_path / "api.db"))
    await storage.initialize()
    manager = ProfileManager(storage, str(tmp_path))
    await manager.initialize()

    dependencies.set_storage_manager(storage)
    dependencies.set_profile_manager(manager)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    await storage.close()
