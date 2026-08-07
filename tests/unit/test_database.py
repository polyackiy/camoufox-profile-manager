"""Tests for the SQLite storage layer."""

import json

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.config import get_settings
from camoufox_pm.core.models import Profile, ProfileStatus, ProxyConfig, ProxyType


@pytest.mark.asyncio
async def test_save_and_get_profile(storage):
    profile = Profile(name="acct-1")
    await storage.save_profile(profile)
    loaded = await storage.get_profile(profile.id)
    assert loaded is not None
    assert loaded.name == "acct-1"


@pytest.mark.asyncio
async def test_update_and_delete_profile(storage):
    profile = Profile(name="acct-2")
    await storage.save_profile(profile)

    profile.name = "renamed"
    await storage.update_profile(profile)
    assert (await storage.get_profile(profile.id)).name == "renamed"

    assert await storage.delete_profile(profile.id) is True
    assert await storage.get_profile(profile.id) is None


@pytest.mark.asyncio
async def test_list_and_filter_profiles(storage):
    await storage.save_profile(Profile(name="a", status=ProfileStatus.ACTIVE))
    await storage.save_profile(Profile(name="b", status=ProfileStatus.INACTIVE))
    everyone = await storage.list_profiles()
    assert len(everyone) == 2
    active = await storage.list_profiles({"status": "active"})
    assert len(active) == 1
    assert active[0].name == "a"


@pytest.mark.asyncio
async def test_proxy_password_roundtrips_through_storage(storage):
    profile = Profile(
        name="proxied",
        proxy=ProxyConfig(
            type=ProxyType.HTTP, server="1.2.3.4:8080", username="u", password="secret"
        ),
    )
    await storage.save_profile(profile)
    loaded = await storage.get_profile(profile.id)
    assert loaded.proxy is not None
    assert loaded.proxy.password == "secret"


@pytest.mark.asyncio
async def test_proxy_password_is_encrypted_at_rest(storage, monkeypatch):
    """With a key configured, the password must be ciphertext on disk, not plaintext."""
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        profile = Profile(
            name="proxied",
            proxy=ProxyConfig(
                type=ProxyType.HTTP, server="1.2.3.4:8080", username="u", password="secret"
            ),
        )
        await storage.save_profile(profile)

        row = storage.db._connection.execute(
            "SELECT proxy_config FROM profiles WHERE id = ?", (profile.id,)
        ).fetchone()
        stored = json.loads(row["proxy_config"])["password"]
        assert stored.startswith("enc:")
        assert "secret" not in stored

        loaded = await storage.get_profile(profile.id)
        assert loaded.proxy.password == "secret"
    finally:
        get_settings.cache_clear()
