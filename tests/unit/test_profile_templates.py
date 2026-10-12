"""Fresh identity and reusable extension snapshots, without account secrets."""

import json
from pathlib import Path

import pytest

from camoufox_pm.core.database import StaleWriteError
from camoufox_pm.core.extension_library import ExtensionLibrary, resolve_profile_addons
from camoufox_pm.core.leases import ProfileLocked
from camoufox_pm.core.profile_templates import ProfileTemplates
from tests.unit.test_extension_library import xpi


@pytest.mark.asyncio
async def test_template_profiles_fresh_identity_and_snapshots_survive_library_deletion(
    profile_manager,
):
    extensions = ExtensionLibrary(profile_manager)
    entry = await extensions.upload(xpi())
    source = await profile_manager.create_profile(
        "source",
        notes="Reusable",
        tags=["work"],
        browser_settings={"os": "linux", "languages": ["en-US"]},
        proxy_config={"type": "http", "server": "proxy:80", "username": "u", "password": "private"},
        generate_fingerprint=False,
    )
    await extensions.assign(source.id, [entry["id"]], source.row_version)
    source = await profile_manager.get_profile(source.id)
    source.fingerprint = {"navigator.hardwareConcurrency": 8}
    await profile_manager.storage.update_profile(source)
    Path(source.storage_path, "cookies.sqlite").write_bytes(b"private cookies")
    templates = ProfileTemplates(profile_manager)
    template = await templates.from_profile(source.id, source.row_version, "Work defaults")
    assert "private" not in json.dumps(template) and "fingerprint" not in template
    await extensions.delete(entry["id"], 0)
    first = await templates.create_profile(template["id"], 0, "first")
    second = await templates.create_profile(template["id"], 0, "second")
    assert first.id != second.id and first.storage_path != second.storage_path
    assert first.proxy is None and first.fingerprint is None and first.last_used is None
    assert first.browser_settings.os == "linux" and first.tags == ["work"]
    assert not Path(first.storage_path, "cookies.sqlite").exists()
    assert resolve_profile_addons(first, Path(first.storage_path))
    await templates.update(template["id"], 0, {"name": "Renamed", "notes": None})
    assert (await profile_manager.get_profile(first.id)).notes == "Reusable"
    with pytest.raises(StaleWriteError):
        await templates.create_profile(template["id"], 0, "stale")


@pytest.mark.asyncio
async def test_template_from_profile_requires_closed_lease_and_version(profile_manager):
    templates = ProfileTemplates(profile_manager)
    profile = await profile_manager.create_profile("source", generate_fingerprint=False)
    with pytest.raises(StaleWriteError):
        await templates.from_profile(profile.id, 99, "stale")
    await profile_manager.storage.acquire_lease(profile.id, "other", 300)
    with pytest.raises(ProfileLocked):
        await templates.from_profile(profile.id, 0, "running")
    assert await templates.list() == []


@pytest.mark.asyncio
async def test_template_cas_edit_delete_and_no_secret_fields(profile_manager):
    templates = ProfileTemplates(profile_manager)
    created = await templates.create("Default", {"os": "windows"}, tags=[" Work "])
    updated = await templates.update(
        created["id"], 0, {"tags": ["ＴＥＳＴ"], "browser_settings": {"os": "macos"}}
    )
    assert updated["row_version"] == 1
    with pytest.raises(StaleWriteError):
        await templates.delete(created["id"], 0)
    await templates.delete(created["id"], 1)
    assert await templates.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "template_edit"])
async def test_template_profile_is_never_published_half_prepared(
    profile_manager, monkeypatch, ending
):
    import asyncio
    import threading

    from camoufox_pm.core import profile_templates as module

    templates = ProfileTemplates(profile_manager)
    template = await templates.create("Default", {})
    entered = threading.Event()
    finished = threading.Event()
    install = module._install_snapshots

    def pending(*args):
        result = install(*args)
        entered.set()
        finished.wait(3)
        return result

    monkeypatch.setattr(module, "_install_snapshots", pending)
    task = asyncio.create_task(templates.create_profile(template["id"], 0, "new"))
    await asyncio.to_thread(entered.wait, 2)
    assert not await profile_manager.storage.list_profiles()
    if ending == "cancel":
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    else:
        await templates.update(template["id"], 0, {"name": "Newer defaults"})
    finished.set()
    with pytest.raises(asyncio.CancelledError if ending == "cancel" else StaleWriteError):
        await task
    assert not await profile_manager.storage.list_profiles()
    assert not list(profile_manager.profiles_dir.glob("profile_*"))
