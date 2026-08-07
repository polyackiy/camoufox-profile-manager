"""Integration tests for the optional API-key guard."""

import pytest

from camoufox_pm.config import get_settings


@pytest.mark.asyncio
async def test_no_key_configured_allows_requests(client):
    """With CPM_API_KEY unset the guard is a no-op."""
    get_settings.cache_clear()
    try:
        response = await client.get("/api/profiles")
        assert response.status_code == 200
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_key_configured_enforces_header(client, monkeypatch):
    monkeypatch.setenv("CPM_API_KEY", "top-secret")
    get_settings.cache_clear()
    try:
        missing = await client.get("/api/profiles")
        assert missing.status_code == 401

        wrong = await client.get("/api/profiles", headers={"X-API-Key": "nope"})
        assert wrong.status_code == 401

        correct = await client.get("/api/profiles", headers={"X-API-Key": "top-secret"})
        assert correct.status_code == 200
    finally:
        get_settings.cache_clear()
