"""Tests for the SQLite storage layer."""

import pytest

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
        proxy=ProxyConfig(type=ProxyType.HTTP, server="1.2.3.4:8080", username="u", password="secret"),
    )
    await storage.save_profile(profile)
    loaded = await storage.get_profile(profile.id)
    assert loaded.proxy is not None
    assert loaded.proxy.password == "secret"
