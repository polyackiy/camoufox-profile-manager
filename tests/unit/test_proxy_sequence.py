"""Sequential assignment keeps exact reviewed pairs under retries and concurrent edits."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.config import get_settings
from camoufox_pm.core import proxy_inventory
from camoufox_pm.core.database import StaleWriteError
from camoufox_pm.core.proxy_inventory import ProxyCredentialsUnavailable, ProxyInventory
from camoufox_pm.core.proxy_sequence import ProxySequence, connection_label
from camoufox_pm.core.sync_guard import SyncBlocked


@pytest.fixture
async def setup(profile_manager, monkeypatch):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    inventory = ProxyInventory(profile_manager)
    proxies = [
        await inventory.create(
            {
                "name": f"Proxy {i}",
                "type": "socks5",
                "server": f"proxy{i}.example:1080",
                "username": "test-user",
                "password": "test-secret",
            }
        )
        for i in range(3)
    ]
    profiles = [
        await profile_manager.create_profile(f"Profile {i}", generate_fingerprint=False)
        for i in range(3)
    ]
    pairs = [
        {
            "profile": {"id": profile.id, "row_version": 0},
            "proxy": {"id": proxy["id"], "row_version": 0},
        }
        for profile, proxy in zip(profiles, proxies, strict=True)
    ]
    yield ProxySequence(inventory), profiles, proxies, pairs
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_preview_is_read_only_and_replay_never_reassigns(setup):
    sequence, profiles, proxies, pairs = setup
    preview = await sequence.preview(pairs)
    assert [item["proxy_id"] for item in preview] == [proxy["id"] for proxy in proxies]
    assert all(item["reason"] is None for item in preview)
    assert "test-secret" not in str(preview) and "test-user" not in str(preview)
    for profile in profiles:
        assert (await sequence.inventory.storage.get_profile(profile.id)).row_version == 0
    authorize = AsyncMock()
    recorded = AsyncMock()
    result = await sequence.assign(pairs, authorize=authorize, on_assigned=recorded)
    assert result == {"assigned": [profile.id for profile in profiles], "skipped": []}
    assert recorded.await_count == 3
    for profile, proxy in zip(profiles, proxies, strict=True):
        saved = await sequence.inventory.storage.get_profile(profile.id)
        assert saved.proxy.server == proxy["server"] and saved.proxy.password == "test-secret"
        raw = sequence.connection.execute(
            "SELECT proxy_config FROM profiles WHERE id=?", (profile.id,)
        ).fetchone()[0]
        assert "test-secret" not in raw and "enc:" in raw
    repeated = await sequence.assign(pairs, authorize=authorize)
    assert repeated["assigned"] == [] and len(repeated["skipped"]) == 3
    for profile in profiles:
        assert (await sequence.inventory.storage.get_profile(profile.id)).row_version == 1


@pytest.mark.asyncio
async def test_whole_proxy_pool_preflight_before_first_write(setup, monkeypatch):
    sequence, profiles, proxies, pairs = setup
    await sequence.inventory.update(proxies[-1]["id"], {"name": "Changed"}, 0)
    with pytest.raises(StaleWriteError):
        await sequence.assign(pairs, authorize=AsyncMock())
    for profile in profiles:
        assert (await sequence.inventory.storage.get_profile(profile.id)).proxy is None
    pairs[-1]["proxy"]["row_version"] = 1
    monkeypatch.delenv("CPM_SECRET_KEY")
    get_settings.cache_clear()
    with pytest.raises(ProxyCredentialsUnavailable):
        await sequence.assign(pairs, authorize=AsyncMock())
    for profile in profiles:
        assert (await sequence.inventory.storage.get_profile(profile.id)).proxy is None


@pytest.mark.asyncio
async def test_skips_running_leased_stale_trashed_without_shifting_mapping(setup):
    sequence, profiles, proxies, pairs = setup
    manager = sequence.inventory.manager
    manager.browser_sessions._starting[profiles[0].id] = 1
    await manager.storage.acquire_lease(profiles[1].id, "another-manager", 300)
    preview = await sequence.preview(pairs)
    assert [item["reason"] for item in preview][:2] == ["Profile is running or leased"] * 2
    try:
        result = await sequence.assign(pairs, authorize=AsyncMock())
        assert result["assigned"] == [profiles[2].id]
        assert (await manager.storage.get_profile(profiles[2].id)).proxy.server == proxies[2][
            "server"
        ]
    finally:
        manager.browser_sessions._starting.clear()
        await manager.storage.release_lease(profiles[1].id, "another-manager")
    await manager.delete_profile(profiles[0].id)
    await manager.update_profile(profiles[1].id, {"notes": "edited"})
    result = await sequence.assign(pairs, authorize=AsyncMock())
    assert result["assigned"] == [] and len(result["skipped"]) == 3


@pytest.mark.asyncio
async def test_revoked_authority_after_lease_acquisition_prevents_write(setup, monkeypatch):
    sequence, profiles, proxies, pairs = setup
    original = proxy_inventory.profile_operation
    revoked = False

    @asynccontextmanager
    async def guarded(*args, **kwargs):
        nonlocal revoked
        async with original(*args, **kwargs) as profile:
            revoked = True
            yield profile

    async def authorize():
        if revoked:
            raise PermissionError("revoked")

    monkeypatch.setattr(proxy_inventory, "profile_operation", guarded)
    with pytest.raises(PermissionError):
        await sequence.assign(pairs, authorize=authorize)
    for profile in profiles:
        assert (await sequence.inventory.storage.get_profile(profile.id)).proxy is None
        assert (await sequence.inventory.storage.get_lease(profile.id))[0] is None


@pytest.mark.asyncio
async def test_changed_proxy_after_preflight_is_skipped_and_other_pairs_keep_positions(
    setup, monkeypatch
):
    sequence, profiles, proxies, pairs = setup
    original = sequence.inventory.assign
    calls = 0

    async def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await sequence.inventory.update(proxies[1]["id"], {"server": "changed.example:1080"}, 0)
        return await original(*args, **kwargs)

    monkeypatch.setattr(sequence.inventory, "assign", changed)
    result = await sequence.assign(pairs, authorize=AsyncMock())
    assert result["assigned"] == [profiles[0].id, profiles[2].id]
    assert result["skipped"][0]["id"] == profiles[1].id
    assert (await sequence.inventory.storage.get_profile(profiles[1].id)).proxy is None
    assert (await sequence.inventory.storage.get_profile(profiles[2].id)).proxy.server == proxies[
        2
    ]["server"]


@pytest.mark.asyncio
async def test_sync_ownership_failure_is_sanitized_per_target(setup, monkeypatch):
    sequence, profiles, proxies, pairs = setup
    original = proxy_inventory.profile_operation

    @asynccontextmanager
    async def guarded(manager, profile_id, **kwargs):
        if profile_id == profiles[1].id:
            raise SyncBlocked("test-secret should never appear")
        async with original(manager, profile_id, **kwargs) as profile:
            yield profile

    monkeypatch.setattr(proxy_inventory, "profile_operation", guarded)
    result = await sequence.assign(pairs, authorize=AsyncMock())
    assert result["assigned"] == [profiles[0].id, profiles[2].id]
    assert result["skipped"] == [
        {"id": profiles[1].id, "reason": "Device synchronization blocks this operation"}
    ]
    assert "test-secret" not in str(result)


def test_old_connection_labels_hide_userinfo_and_reject_malformed_ports():
    assert (
        connection_label({"server": "https://account:private@proxy.example:443", "type": "https"})
        == "https://proxy.example:443"
    )
    assert (
        connection_label({"server": "proxy.example:invalid", "type": "http"}) == "Proxy configured"
    )
    assert connection_label({"server": "[::1]:1080", "type": "socks5"}) == "socks5://[::1]:1080"


@pytest.mark.asyncio
async def test_storage_failure_reports_safe_partial_result_and_later_pairs_keep_mapping(
    setup, monkeypatch
):
    sequence, profiles, proxies, pairs = setup
    original = proxy_inventory._serialize_proxy

    def fail_one(config):
        if config.server == proxies[1]["server"]:
            raise OSError("private-password must not be reflected")
        return original(config)

    monkeypatch.setattr(proxy_inventory, "_serialize_proxy", fail_one)
    result = await sequence.assign(pairs, authorize=AsyncMock())
    assert result["assigned"] == [profiles[0].id, profiles[2].id]
    assert result["skipped"][0]["id"] == profiles[1].id
    assert "private-password" not in str(result)
    assert (await sequence.inventory.storage.get_profile(profiles[1].id)).proxy is None
    monkeypatch.setattr(proxy_inventory, "_serialize_proxy", original)
    retry = await sequence.assign(pairs, authorize=AsyncMock())
    assert retry["assigned"] == [profiles[1].id]
    assert (await sequence.inventory.storage.get_profile(profiles[1].id)).proxy.server == proxies[
        1
    ]["server"]


@pytest.mark.asyncio
async def test_cancellation_after_commit_can_resume_original_pairs_without_repeating_completed_rows(
    setup,
):
    import asyncio

    sequence, profiles, proxies, pairs = setup

    async def cancel(profile_id):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await sequence.assign(pairs, authorize=AsyncMock(), on_assigned=cancel)
    retry = await sequence.assign(pairs, authorize=AsyncMock())
    assert retry["assigned"] == [profiles[1].id, profiles[2].id]
    for profile, proxy in zip(profiles, proxies, strict=True):
        saved = await sequence.inventory.storage.get_profile(profile.id)
        assert saved.row_version == 1 and saved.proxy.server == proxy["server"]


@pytest.mark.asyncio
async def test_key_loss_after_config_read_never_copies_plaintext(setup, monkeypatch):
    sequence, profiles, proxies, pairs = setup
    original = proxy_inventory.profile_operation

    @asynccontextmanager
    async def lose_key(*args, **kwargs):
        async with original(*args, **kwargs) as profile:
            monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
            get_settings.cache_clear()
            yield profile

    monkeypatch.setattr(proxy_inventory, "profile_operation", lose_key)
    result = await sequence.assign(pairs, authorize=AsyncMock())
    assert result["assigned"] == [] and len(result["skipped"]) == 3
    for profile in profiles:
        assert (await sequence.inventory.storage.get_profile(profile.id)).proxy is None


@pytest.mark.asyncio
async def test_concurrent_managers_cannot_publish_the_same_assignment_twice(setup):
    import asyncio

    from camoufox_pm.core.database import StorageManager
    from camoufox_pm.core.profile_manager import ProfileManager

    sequence, profiles, proxies, pairs = setup
    storage = StorageManager(str(sequence.inventory.storage.db.db_path))
    await storage.initialize()
    manager = ProfileManager(storage, str(sequence.inventory.manager.data_dir))
    try:
        second = ProxySequence(ProxyInventory(manager))
        left, right = await asyncio.gather(
            sequence.assign(pairs, authorize=AsyncMock()),
            second.assign(pairs, authorize=AsyncMock()),
        )
        assert sorted(left["assigned"] + right["assigned"]) == sorted(
            profile.id for profile in profiles
        )
        for profile in profiles:
            assert (await storage.get_profile(profile.id)).row_version == 1
    finally:
        await storage.close()
