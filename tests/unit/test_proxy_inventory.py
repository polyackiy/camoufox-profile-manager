"""Inventory credentials, snapshot assignment and optimistic concurrency."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.config import get_settings
from camoufox_pm.core import proxy_check, proxy_inventory
from camoufox_pm.core.database import StaleWriteError, StorageManager
from camoufox_pm.core.models import ProxyConfig
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.proxy_inventory import ProxyCredentialsUnavailable, ProxyInventory

SECRET = "inventory-secret:@/long"
VALUES = {
    "name": "Named proxy",
    "type": "http",
    "server": "proxy.example:8080",
    "username": "account",
    "password": SECRET,
    "country": "US",
}


@pytest.fixture
async def inventory(profile_manager, monkeypatch):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield ProxyInventory(profile_manager)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_password_is_encrypted_and_metadata_never_decrypts(inventory, monkeypatch):
    created = await inventory.create(VALUES)
    raw = inventory.connection.execute("SELECT password FROM proxy_inventory").fetchone()[0]
    assert raw.startswith("enc:") and SECRET not in raw
    assert "password" not in created and created["has_password"]
    assert inventory._secret_config(created["id"]).password == SECRET
    monkeypatch.setattr(proxy_inventory, "decrypt", lambda value: pytest.fail("metadata decrypted"))
    assert (await inventory.list())[0] == created


@pytest.mark.asyncio
async def test_patch_password_is_preserved_then_explicitly_cleared(inventory):
    proxy = await inventory.create(VALUES)
    original = inventory.connection.execute("SELECT password FROM proxy_inventory").fetchone()[0]
    updated = await inventory.update(proxy["id"], {"name": "Renamed"}, 0)
    assert updated["row_version"] == 1
    assert (
        inventory.connection.execute("SELECT password FROM proxy_inventory").fetchone()[0]
        == original
    )
    cleared = await inventory.update(proxy["id"], {"password": None}, 1)
    assert not cleared["has_password"] and cleared["row_version"] == 2
    assert inventory._secret_config(proxy["id"]).password is None


@pytest.mark.asyncio
async def test_stale_patch_and_delete_leave_latest_proxy_unchanged(inventory):
    proxy = await inventory.create(VALUES)
    await inventory.update(proxy["id"], {"server": "other.example:8080"}, 0)
    with pytest.raises(StaleWriteError):
        await inventory.update(proxy["id"], {"name": "Old editor"}, 0)
    with pytest.raises(StaleWriteError):
        await inventory.delete(proxy["id"], 0)
    assert (await inventory.list())[0]["server"] == "other.example:8080"


@pytest.mark.asyncio
async def test_missing_wrong_and_invalid_keys_never_forward_ciphertext(inventory, monkeypatch):
    proxy = await inventory.create(VALUES)
    checker = AsyncMock(return_value=proxy_check.ProxyCheckResult(reachable=True))
    monkeypatch.setattr(proxy_check, "check", checker)
    profile = await inventory.manager.create_profile("target", generate_fingerprint=False)
    for key in (None, Fernet.generate_key().decode(), "invalid"):
        if key is None:
            monkeypatch.delenv("CPM_SECRET_KEY")
        else:
            monkeypatch.setenv("CPM_SECRET_KEY", key)
        get_settings.cache_clear()
        with pytest.raises(ProxyCredentialsUnavailable):
            await inventory.check(proxy["id"])
        with pytest.raises(ProxyCredentialsUnavailable):
            await inventory.assign(proxy["id"], [{"id": profile.id, "row_version": 0}], 0)
        assert (await inventory.list())[0]["has_password"]
        assert (await inventory.storage.get_profile(profile.id)).proxy is None
    checker.assert_not_called()


@pytest.mark.asyncio
async def test_password_write_requires_key_but_plain_metadata_does_not(inventory, monkeypatch):
    monkeypatch.delenv("CPM_SECRET_KEY")
    get_settings.cache_clear()
    with pytest.raises(ProxyCredentialsUnavailable):
        await inventory.create(VALUES)
    assert await inventory.list() == []
    proxy = await inventory.create({**VALUES, "password": None})
    assert proxy["has_password"] is False


@pytest.mark.asyncio
async def test_literal_enc_prefix_in_a_decrypted_password_is_valid(inventory, monkeypatch):
    proxy = await inventory.create({**VALUES, "password": "enc:literal-password"})
    checker = AsyncMock(return_value=proxy_check.ProxyCheckResult(reachable=True))
    monkeypatch.setattr(proxy_check, "check", checker)
    await inventory.check(proxy["id"])
    assert checker.call_args.args[0].password == "enc:literal-password"


@pytest.mark.asyncio
async def test_check_is_guarded_against_config_edit_and_does_not_bump_version(
    inventory, monkeypatch
):
    proxy = await inventory.create(VALUES)
    checker = AsyncMock(return_value=proxy_check.ProxyCheckResult(reachable=True))
    monkeypatch.setattr(proxy_check, "check", checker)
    checked = await inventory.check(proxy["id"])
    assert checked["last_check"]["reachable"] and checked["row_version"] == 0

    async def edit_during_check(*args):
        await inventory.update(proxy["id"], {"server": "changed.example:8080"}, 0)
        return proxy_check.ProxyCheckResult(reachable=True)

    monkeypatch.setattr(proxy_check, "check", edit_during_check)
    with pytest.raises(StaleWriteError):
        await inventory.check(proxy["id"])
    assert (await inventory.list())[0]["last_check"] is None


@pytest.mark.asyncio
async def test_older_check_cannot_overwrite_newer_check(inventory, monkeypatch):
    proxy = await inventory.create(VALUES)
    waiting, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def checker(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            waiting.set()
            await finish.wait()
            ip = "1.1.1.1"
        else:
            ip = "2.2.2.2"
        return proxy_check.ProxyCheckResult(reachable=True, location=proxy_check.ProxyLocation(ip))

    monkeypatch.setattr(proxy_check, "check", checker)
    older = asyncio.create_task(inventory.check(proxy["id"]))
    await waiting.wait()
    newer = await inventory.check(proxy["id"])
    finish.set()
    with pytest.raises(StaleWriteError):
        await older
    assert newer["last_check"]["ip"] == "2.2.2.2"
    assert (await inventory.list())[0]["last_check"]["ip"] == "2.2.2.2"


@pytest.mark.asyncio
async def test_check_error_never_reflects_raw_or_encoded_credentials(inventory, monkeypatch):
    from urllib.parse import quote

    proxy = await inventory.create(VALUES)
    monkeypatch.setattr(
        proxy_check,
        "check",
        AsyncMock(
            return_value=proxy_check.ProxyCheckResult(
                reachable=False,
                error=f"http://account:{quote(SECRET, safe='')}@proxy.example:8080 {SECRET}",
            )
        ),
    )
    result = await inventory.check(proxy["id"])
    assert SECRET not in str(result) and quote(SECRET, safe="") not in str(result)


@pytest.mark.asyncio
async def test_assignment_is_a_versioned_independent_snapshot(inventory):
    proxy = await inventory.create(VALUES)
    profile = await inventory.manager.create_profile(
        "target", notes="Preserve", generate_fingerprint=False
    )
    result = await inventory.assign(proxy["id"], [{"id": profile.id, "row_version": 0}], 0)
    assert result == {"assigned": [profile.id], "skipped": []}
    assigned = await inventory.storage.get_profile(profile.id)
    assert assigned.proxy.password == SECRET and assigned.row_version == 1
    assert assigned.notes == "Preserve" and assigned.proxy_check is None
    await inventory.update(proxy["id"], {"password": "replacement"}, 0)
    await inventory.delete(proxy["id"], 1)
    assert (await inventory.storage.get_profile(profile.id)).proxy == assigned.proxy


@pytest.mark.asyncio
async def test_assignment_reports_live_leased_trashed_stale_and_missing(inventory):
    proxy = await inventory.create(VALUES)
    profiles = [
        await inventory.manager.create_profile(name, generate_fingerprint=False)
        for name in ("good", "starting", "leased", "trash", "edited")
    ]
    good, starting, leased, trash, edited = profiles
    inventory.manager.browser_sessions._starting[starting.id] = 1
    await inventory.storage.acquire_lease(leased.id, "remote-instance", 300)
    await inventory.manager.delete_profile(trash.id)
    await inventory.manager.update_profile(edited.id, {"notes": "newer"})
    try:
        result = await inventory.assign(
            proxy["id"],
            [{"id": profile.id, "row_version": 0} for profile in profiles]
            + [{"id": "missing", "row_version": 0}],
            0,
        )
        assert result["assigned"] == [good.id]
        assert {entry["id"] for entry in result["skipped"]} == {
            starting.id,
            leased.id,
            trash.id,
            edited.id,
            "missing",
        }
        for profile in (starting, leased, trash, edited):
            assert (
                await inventory.storage.get_profile(profile.id, include_deleted=True)
            ).proxy is None
    finally:
        inventory.manager.browser_sessions._starting.clear()


@pytest.mark.asyncio
async def test_assignment_rejects_stale_template_and_edit_after_profile_lock(
    inventory, monkeypatch
):
    proxy = await inventory.create(VALUES)
    profile = await inventory.manager.create_profile("target", generate_fingerprint=False)
    with pytest.raises(StaleWriteError):
        await inventory.assign(proxy["id"], [{"id": profile.id, "row_version": 0}], 99)
    original = proxy_inventory.profile_operation

    @asynccontextmanager
    async def edit_after_lock(*args, **kwargs):
        async with original(*args, **kwargs) as locked:
            await inventory.update(proxy["id"], {"server": "changed.example:8080"}, 0)
            yield locked

    monkeypatch.setattr(proxy_inventory, "profile_operation", edit_after_lock)
    result = await inventory.assign(proxy["id"], [{"id": profile.id, "row_version": 0}], 0)
    assert result["assigned"] == [] and result["skipped"][0]["id"] == profile.id
    assert (await inventory.storage.get_profile(profile.id)).proxy is None


@pytest.mark.asyncio
async def test_cross_instance_profile_lease_prevents_assignment(inventory):
    proxy = await inventory.create(VALUES)
    profile = await inventory.manager.create_profile("target", generate_fingerprint=False)
    other_storage = StorageManager(str(inventory.storage.db.db_path))
    await other_storage.initialize()
    other_manager = ProfileManager(other_storage, str(inventory.manager.data_dir))
    try:
        await inventory.storage.acquire_lease(profile.id, "other-launch", 300)
        result = await ProxyInventory(other_manager).assign(
            proxy["id"],
            [{"id": profile.id, "row_version": 0}],
            0,
        )
        assert result["assigned"] == [] and "leased" in result["skipped"][0]["reason"]
    finally:
        await other_storage.close()


@pytest.mark.asyncio
async def test_import_skips_exact_duplicates_without_echoing_credentials(inventory):
    config = ProxyConfig(
        **{field: VALUES[field] for field in ("type", "server", "username", "password")}
    )
    result = await inventory.import_configs([(1, config), (2, config)])
    assert len(result["created"]) == 1 and result["skipped"] == [
        {"line": 2, "reason": "Proxy already exists"}
    ]
    assert result["created"][0]["name"] == "proxy.example:8080"
    assert SECRET not in str(result)
    again = await inventory.import_configs([(1, config)])
    assert again["created"] == [] and again["skipped"][0]["line"] == 1


@pytest.mark.asyncio
async def test_import_dedup_ignores_country_labels_and_separates_credentials(inventory):
    await inventory.create(VALUES)
    base = {field: VALUES[field] for field in ("type", "server", "username", "password")}
    result = await inventory.import_configs(
        [
            (1, ProxyConfig(**base)),
            (2, ProxyConfig(**{**base, "password": "different-account"})),
        ]
    )
    assert result["skipped"] == [{"line": 1, "reason": "Proxy already exists"}]
    assert len(result["created"]) == 1
    assert len(await inventory.list()) == 2


@pytest.mark.asyncio
async def test_assignment_does_not_use_another_holders_replaced_lease(inventory, monkeypatch):
    proxy = await inventory.create(VALUES)
    profile = await inventory.manager.create_profile("target", generate_fingerprint=False)
    original = proxy_inventory.profile_operation

    @asynccontextmanager
    async def replace_operation_lease(*args, **kwargs):
        async with original(*args, **kwargs) as locked:
            await inventory.storage.force_release_lease(profile.id)
            await inventory.storage.acquire_lease(profile.id, "new-browser-holder", 300)
            yield locked

    monkeypatch.setattr(proxy_inventory, "profile_operation", replace_operation_lease)
    result = await inventory.assign(proxy["id"], [{"id": profile.id, "row_version": 0}], 0)
    assert result["assigned"] == [] and result["skipped"][0]["id"] == profile.id
    assert (await inventory.storage.get_profile(profile.id)).proxy is None
    assert (await inventory.storage.get_lease(profile.id))[0] == "new-browser-holder"
