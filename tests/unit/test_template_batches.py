"""Retry receipts bind fresh profiles to exact batch intent, including lost responses."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.api.models.templates import TemplateBatchRequest
from camoufox_pm.config import get_settings
from camoufox_pm.core.database import StaleWriteError
from camoufox_pm.core.profile_templates import ProfileTemplates
from camoufox_pm.core.proxy_inventory import ProxyInventory
from camoufox_pm.core.template_batches import BatchAccessLost, BatchConflict, TemplateBatches


async def authorized():
    pass


def request(names, **values):
    return TemplateBatchRequest(batch_id=uuid4(), row_version=0, names=names, **values).model_dump(
        mode="json"
    )


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_fresh_profiles_and_proxy_copies_are_independent_and_receipts_have_no_secrets(
    profile_manager, encryption_key
):
    templates = ProfileTemplates(profile_manager)
    template = await templates.create("Defaults", {"os": "linux"}, tags=["work"])
    proxies = ProxyInventory(profile_manager)
    refs = []
    for i in range(2):
        proxy = await proxies.create(
            {
                "name": f"Proxy {i}",
                "type": "socks5",
                "server": f"host{i}:1080",
                "username": "private-user",
                "password": "private-password",
            }
        )
        refs.append({"id": proxy["id"], "row_version": 0})
    data = request(["Profile 001", "Profile 002"], proxy_refs=refs)
    batches = TemplateBatches(profile_manager)
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert [row["status"] for row in result["items"]] == ["created", "created"]
    profiles = [await profile_manager.get_profile(row["profile_id"]) for row in result["items"]]
    assert profiles[0].id != profiles[1].id and profiles[0].storage_path != profiles[1].storage_path
    assert all(p.fingerprint is None and p.tags == ["work"] for p in profiles)
    assert [p.proxy.server for p in profiles] == ["host0:1080", "host1:1080"]
    assert all(p.proxy.password == "private-password" for p in profiles)
    assert "private-password" not in json.dumps(result) and "private-user" not in json.dumps(result)
    assert "private-password" not in batches._batch(data["batch_id"])["request_json"]
    for row in profile_manager.storage.db._connection.execute("SELECT proxy_config FROM profiles"):
        assert "private-password" not in row[0] and "enc:" in row[0]


@pytest.mark.asyncio
async def test_lost_response_replay_and_deleted_results_never_recreate_profiles(profile_manager):
    templates = ProfileTemplates(profile_manager)
    template = await templates.create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["first", "second"])
    first = await batches.submit(template["id"], data, authorize=authorized)
    await templates.delete(template["id"], 0)
    again = await batches.submit(template["id"], data, authorize=authorized)
    assert first == again
    profile_id = first["items"][0]["profile_id"]
    await profile_manager.delete_profile(profile_id)
    assert (await batches.submit(template["id"], data, authorize=authorized))["items"][0][
        "status"
    ] == "in_trash"
    await profile_manager.permanently_delete_profile(profile_id)
    assert (await batches.submit(template["id"], data, authorize=authorized))["items"][0][
        "status"
    ] == "unavailable"
    assert len(await profile_manager.storage.list_profiles()) == 1


@pytest.mark.asyncio
async def test_reusing_a_batch_identifier_with_changed_intent_is_a_conflict(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["first"])
    await batches.submit(template["id"], data, authorize=authorized)
    with pytest.raises(BatchConflict):
        await batches.submit(template["id"], {**data, "names": ["changed"]}, authorize=authorized)
    assert len(await profile_manager.storage.list_profiles()) == 1


@pytest.mark.asyncio
async def test_partial_failure_retries_only_unpublished_rows(profile_manager, monkeypatch):
    from camoufox_pm.core import profile_templates as module

    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["one", "two", "three"])
    install = module._install_snapshots
    calls = 0

    def sometimes_fails(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private-provider-password")
        return install(*args)

    monkeypatch.setattr(module, "_install_snapshots", sometimes_fails)
    first = await batches.submit(template["id"], data, authorize=authorized)
    assert [row["status"] for row in first["items"]] == ["created", "failed", "created"]
    assert "private-provider-password" not in json.dumps(first)
    final = await batches.submit(template["id"], data, authorize=authorized)
    assert all(row["status"] == "created" for row in final["items"])
    assert calls == 4
    for position in (0, 2):
        assert first["items"][position]["profile_id"] == final["items"][position]["profile_id"]
    assert len(await profile_manager.storage.list_profiles()) == 3
    assert len(list(profile_manager.profiles_dir.glob("profile_*"))) == 3


@pytest.mark.asyncio
async def test_concurrent_identical_submissions_publish_each_row_once(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    data = request(["one", "two", "three"])
    batches = TemplateBatches(profile_manager)
    results = await asyncio.gather(
        *(batches.submit(template["id"], data, authorize=authorized) for _ in range(2))
    )
    assert [row["profile_id"] for row in results[0]["items"]] == [
        row["profile_id"] for row in results[1]["items"]
    ]
    assert len(await profile_manager.storage.list_profiles()) == 3
    assert len(list(profile_manager.profiles_dir.glob("profile_*"))) == 3


@pytest.mark.asyncio
async def test_duplicate_names_are_explicit_and_validated_before_any_batch_write(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    await profile_manager.create_profile("Existing", generate_fingerprint=False)
    batches = TemplateBatches(profile_manager)
    for names in (["fresh", "ＥＸＩＳＴＩＮＧ"], ["same", "SAME"]):
        with pytest.raises(ValueError):
            await batches.submit(template["id"], request(names), authorize=authorized)
    assert batches.list() == []
    assert len(await profile_manager.storage.list_profiles()) == 1
    result = await batches.submit(
        template["id"],
        request(["Existing", "Existing"], allow_duplicate_names=True),
        authorize=authorized,
    )
    assert all(row["status"] == "created" for row in result["items"])


@pytest.mark.asyncio
async def test_missing_proxy_in_the_last_row_blocks_the_whole_new_batch(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    proxy = await ProxyInventory(profile_manager).create(
        {"name": "Valid", "type": "http", "server": "host:80"}
    )
    batches = TemplateBatches(profile_manager)
    with pytest.raises(ValueError):
        await batches.submit(
            template["id"],
            request(
                ["one", "two"],
                proxy_refs=[
                    {"id": proxy["id"], "row_version": 0},
                    {"id": "missing", "row_version": 0},
                ],
            ),
            authorize=authorized,
        )
    assert batches.list() == []
    assert not await profile_manager.storage.list_profiles()


@pytest.mark.asyncio
async def test_receipt_rolls_back_if_profile_publication_fails(profile_manager, monkeypatch):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["one"])
    publish = profile_manager.storage.db._upsert_profile

    def failed(*_args):
        raise sqlite3.OperationalError("disk write failed")

    monkeypatch.setattr(profile_manager.storage.db, "_upsert_profile", failed)
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert result["items"][0]["status"] == "failed" and result["items"][0]["profile_id"] is None
    assert not await profile_manager.storage.list_profiles()
    assert not list(profile_manager.profiles_dir.glob("profile_*"))
    monkeypatch.setattr(profile_manager.storage.db, "_upsert_profile", publish)
    assert (await batches.submit(template["id"], data, authorize=authorized))["items"][0][
        "status"
    ] == "created"


@pytest.mark.asyncio
async def test_interruption_after_one_commit_can_resume_without_duplicate_creation(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["one", "two"])

    async def interrupted(_profile_id):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await batches.submit(template["id"], data, authorize=authorized, on_created=interrupted)
    saved = batches.get(data["batch_id"])
    assert [row["status"] for row in saved["items"]] == ["created", "pending"]
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert result["items"][0]["profile_id"] == saved["items"][0]["profile_id"]
    assert len(await profile_manager.storage.list_profiles()) == 2


@pytest.mark.asyncio
async def test_revocation_before_publication_leaves_no_profile_or_directory(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    calls = 0

    async def authority():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise BatchAccessLost

    with pytest.raises(BatchAccessLost):
        await batches.submit(template["id"], request(["one", "two"]), authorize=authority)
    assert not await profile_manager.storage.list_profiles()
    assert not list(profile_manager.profiles_dir.glob("profile_*"))


@pytest.mark.asyncio
async def test_stale_template_does_not_write_a_new_batch(profile_manager):
    templates = ProfileTemplates(profile_manager)
    template = await templates.create("Defaults", {})
    await templates.update(template["id"], 0, {"notes": "newer"})
    batches = TemplateBatches(profile_manager)
    with pytest.raises(StaleWriteError):
        await batches.submit(template["id"], request(["one"]), authorize=authorized)
    assert batches.list() == []


@pytest.mark.asyncio
async def test_changed_proxy_is_not_copied_after_preparation_and_preview_keeps_original_mapping(
    profile_manager,
):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    inventory = ProxyInventory(profile_manager)
    proxy = await inventory.create({"name": "Original", "type": "http", "server": "original:80"})
    batches = TemplateBatches(profile_manager)
    data = request(["one"], proxy_refs=[{"id": proxy["id"], "row_version": 0}])
    calls = 0

    async def authority():
        nonlocal calls
        calls += 1
        if calls == 3:
            await inventory.update(proxy["id"], {"server": "changed:80"}, 0)

    result = await batches.submit(template["id"], data, authorize=authority)
    assert result["items"][0]["status"] == "failed"
    assert result["preview"]["proxy_mapping"][0]["server"] == "original:80"
    assert not await profile_manager.storage.list_profiles()
    assert not list(profile_manager.profiles_dir.glob("profile_*"))
    with pytest.raises(StaleWriteError):
        await batches.submit(template["id"], data, authorize=authorized)


@pytest.mark.asyncio
async def test_receipts_remain_effective_after_reopening_the_database(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    data = request(["one"])
    result = await TemplateBatches(profile_manager).submit(
        template["id"], data, authorize=authorized
    )
    await profile_manager.storage.db.close()
    await profile_manager.storage.db.initialize()
    repeated = await TemplateBatches(profile_manager).submit(
        template["id"], data, authorize=authorized
    )
    assert result == repeated
    assert len(await profile_manager.storage.list_profiles()) == 1
