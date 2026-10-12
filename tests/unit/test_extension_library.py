"""Owned extension files, hostile upload gates and guarded profile snapshots."""

import asyncio
import io
import json
import stat
import threading
import zipfile
from pathlib import Path

import pytest

from camoufox_pm.core import extension_library as el
from camoufox_pm.core.database import StaleWriteError
from camoufox_pm.core.extension_library import (
    ExtensionLibrary,
    profile_extension_payloads,
    resolve_profile_addons,
    validate_xpi,
)
from camoufox_pm.core.leases import ProfileLocked


def xpi(version="1.0", addon_id="proof@example.invalid", extra=None, manifest_changes=None):
    manifest = {
        "manifest_version": 2,
        "name": "Local proof",
        "version": version,
        "browser_specific_settings": {"gecko": {"id": addon_id}},
        "permissions": ["storage"],
        "content_scripts": [
            {"matches": ["http://127.0.0.1/*"], "js": ["proof.js"], "run_at": "document_end"}
        ],
    }
    manifest.update(manifest_changes or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(
            "proof.js",
            f'document.documentElement.setAttribute("data-extension-proof", "{version}");',
        )
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "a/../../escape", "a\\b", "C:escape", "./alias"]
)
def test_unsafe_paths_rejected_before_any_write(name):
    with pytest.raises(ValueError):
        validate_xpi(xpi(extra={name: "bad"}))


def test_symlink_duplicate_crc_bomb_and_bad_manifest_rejected():
    payload = xpi()
    for kind in ("symlink", "duplicate", "bomb", "bad"):
        buffer = io.BytesIO(payload)
        with zipfile.ZipFile(buffer, "a", zipfile.ZIP_DEFLATED) as archive:
            if kind == "symlink":
                entry = zipfile.ZipInfo("link")
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, "../outside")
            elif kind == "duplicate":
                archive.writestr("PROOF.js", "duplicate")
            elif kind == "bomb":
                archive.writestr("huge", b"a" * (2 * 1024 * 1024))
            else:
                archive.writestr("manifest.json", "[]")
        with pytest.raises(ValueError):
            validate_xpi(buffer.getvalue())


@pytest.mark.asyncio
async def test_snapshot_assignment_library_deletion_and_retention(profile_manager):
    library = ExtensionLibrary(profile_manager)
    entry = await library.upload(xpi())
    profile = await profile_manager.create_profile("target", generate_fingerprint=False)
    saved = await library.assign(profile.id, [entry["id"]], profile.row_version)
    assert saved["extensions"][0]["addon_id"] == "proof@example.invalid"
    loaded = await profile_manager.get_profile(profile.id)
    assert all(not Path(ref).is_absolute() for ref in loaded.extensions)
    await library.delete(entry["id"], entry["row_version"])
    assert resolve_profile_addons(loaded, Path(loaded.storage_path))
    retained = await library.assign(
        profile.id, [], saved["row_version"], retained_refs=loaded.extensions
    )
    assert retained["extensions"]
    removed = await library.assign(profile.id, [], retained["row_version"])
    assert removed["extensions"] == []
    assert not list((Path(loaded.storage_path) / ".cpm-addons").glob("[a-f0-9]*"))


@pytest.mark.asyncio
async def test_stale_running_lease_and_duplicate_versions_refused(profile_manager):
    library = ExtensionLibrary(profile_manager)
    first = await library.upload(xpi())
    second = await library.upload(xpi("2.0"))
    profile = await profile_manager.create_profile("target", generate_fingerprint=False)
    with pytest.raises(StaleWriteError):
        await library.assign(profile.id, [first["id"]], 99)
    with pytest.raises(ValueError, match="one version"):
        await library.assign(profile.id, [first["id"], second["id"]], 0)
    await profile_manager.storage.acquire_lease(profile.id, "other", 300)
    with pytest.raises(ProfileLocked):
        await library.assign(profile.id, [first["id"]], 0)
    assert not (await profile_manager.get_profile(profile.id)).extensions


@pytest.mark.asyncio
async def test_imported_arbitrary_path_and_symlink_are_never_launched(profile_manager, tmp_path):
    profile = await profile_manager.create_profile("unsafe", generate_fingerprint=False)
    profile.extensions = [str(tmp_path)]
    with pytest.raises(ValueError):
        resolve_profile_addons(profile, Path(profile.storage_path))
    profile.extensions = [".cpm-addons/" + "a" * 64]
    parent = Path(profile.storage_path) / ".cpm-addons"
    parent.mkdir()
    (parent / ("a" * 64)).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        profile_extension_payloads(profile, Path(profile.storage_path))


@pytest.mark.asyncio
async def test_cancel_assignment_twice_keeps_lease_until_worker_rollback(
    profile_manager, monkeypatch
):
    library = ExtensionLibrary(profile_manager)
    entry = await library.upload(xpi())
    profile = await profile_manager.create_profile("target", generate_fingerprint=False)
    entered = threading.Event()
    finish = threading.Event()
    install = el._install_snapshots

    def pending(*args):
        result = install(*args)
        entered.set()
        finish.wait(3)
        return result

    monkeypatch.setattr(el, "_install_snapshots", pending)
    task = asyncio.create_task(library.assign(profile.id, [entry["id"]], 0))
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    lease = await profile_manager.storage.get_lease(profile.id)
    assert lease[0] and not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (await profile_manager.get_profile(profile.id)).extensions
    assert not list((Path(profile.storage_path) / ".cpm-addons").glob("[a-f0-9]*"))
    assert (await profile_manager.storage.get_lease(profile.id))[0] is None


@pytest.mark.asyncio
async def test_explicit_ublock_snapshot_overrides_only_bundled_ublock(profile_manager):
    from camoufox.addons import DefaultAddons

    from camoufox_pm.core.extension_library import managed_default_exclusions

    library = ExtensionLibrary(profile_manager)
    entry = await library.upload(xpi(addon_id="uBlock0@raymondhill.net"))
    profile = await profile_manager.create_profile("uBlock override", generate_fingerprint=False)
    await library.assign(profile.id, [entry["id"]], 0)
    profile = await profile_manager.get_profile(profile.id)
    paths = resolve_profile_addons(profile, Path(profile.storage_path))
    assert managed_default_exclusions(paths) == [DefaultAddons.UBO]
    assert managed_default_exclusions([]) == []


@pytest.mark.parametrize("version", ["01", "1.01", "1.2.3.4.5", "1" * 10, "1.0beta"])
def test_versions_follow_bounded_firefox_store_format(version):
    with pytest.raises(ValueError, match="numeric version"):
        validate_xpi(xpi(version))


def test_crc_and_file_directory_conflicts_are_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "manifest_version": 2,
                    "name": "CRC proof",
                    "version": "1.0",
                    "browser_specific_settings": {"gecko": {"id": "crc@example.invalid"}},
                }
            ),
        )
        archive.writestr("proof.js", "data")
    payload = bytearray(buffer.getvalue())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entry = archive.getinfo("proof.js")
        offset = entry.header_offset
        name_length = int.from_bytes(payload[offset + 26 : offset + 28], "little")
        extra_length = int.from_bytes(payload[offset + 28 : offset + 30], "little")
        payload[offset + 30 + name_length + extra_length] ^= 1
    with pytest.raises(ValueError):
        validate_xpi(bytes(payload))
    with pytest.raises(ValueError, match="conflicting"):
        validate_xpi(xpi(extra={"folder": "file", "folder/child.js": "nested"}))


@pytest.mark.parametrize(
    "name",
    [
        "CON",
        "folder/aux.js",
        "NUL.txt",
        "COM1.js",
        "LPT9.js",
        "conin$.js",
        "name.",
        "folder /file.js",
        "a?.js",
        "a*.js",
        'a".js',
        "a|.js",
        "a<.js",
        "a>.js",
        "COM\u00b9.js",
    ],
)
def test_windows_device_names_and_nonportable_paths_are_rejected(name):
    with pytest.raises(ValueError, match="unsafe"):
        validate_xpi(xpi(extra={name: "bad"}))


def test_unicode_normalization_path_collision_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        validate_xpi(xpi(extra={"caf\u00e9.js": "one", "cafe\u0301.js": "two"}))


def test_localized_manifest_name_is_displayed_from_bounded_default_locale():
    metadata = validate_xpi(
        xpi(
            extra={
                "_locales/en/messages.json": json.dumps(
                    {"extensionName": {"message": "Readable name"}}
                )
            },
            manifest_changes={"name": "__MSG_extensionName__", "default_locale": "en"},
        )
    )
    assert metadata["name"] == "Readable name"
    with pytest.raises(ValueError):
        validate_xpi(
            xpi(manifest_changes={"name": "__MSG_missing__", "default_locale": "../outside"})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["code", "permissions", "extra"])
async def test_forged_library_snapshot_is_not_trusted_or_reused(profile_manager, tamper):
    library = ExtensionLibrary(profile_manager)
    trusted_payload = xpi()
    entry = await library.upload(trusted_payload)
    profile = await profile_manager.create_profile("forged", generate_fingerprint=False)
    saved = await library.assign(profile.id, [entry["id"]], profile.row_version)
    loaded = await profile_manager.get_profile(profile.id)
    target = Path(loaded.storage_path) / loaded.extensions[0]
    if tamper == "code":
        (target / "proof.js").write_text("foreignCode();")
    elif tamper == "permissions":
        manifest = json.loads((target / "manifest.json").read_text())
        manifest["permissions"] = ["<all_urls>", "cookies"]
        (target / "manifest.json").write_text(json.dumps(manifest))
    else:
        (target / "foreign.js").write_text("foreignCode();")
    actual = await library.profile_extensions(profile.id)
    assert actual["extensions"][0]["sha256"] != entry["sha256"]
    if tamper == "permissions":
        assert "cookies" in actual["extensions"][0]["permissions"]
    with pytest.raises(ValueError, match="does not match"):
        await library.assign(profile.id, [entry["id"]], saved["row_version"])
    unchanged = await profile_manager.get_profile(profile.id)
    assert unchanged.row_version == saved["row_version"]
    assert target.exists()


@pytest.mark.asyncio
async def test_repacked_identical_snapshot_matches_and_reuses_library(profile_manager):
    library = ExtensionLibrary(profile_manager)
    entry = await library.upload(xpi(extra={"nested/asset.txt": "asset"}))
    profile = await profile_manager.create_profile("same", generate_fingerprint=False)
    saved = await library.assign(profile.id, [entry["id"]], profile.row_version)
    assert saved["extensions"][0]["sha256"] == entry["sha256"]
    again = await library.assign(profile.id, [entry["id"]], saved["row_version"])
    assert again["extensions"][0]["sha256"] == entry["sha256"]
