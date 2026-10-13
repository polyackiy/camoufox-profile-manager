"""Every manager path is fenced without starting a browser."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from camoufox_pm.core.profile_operations import profile_operation
from camoufox_pm.core.sync_guard import SyncBlocked, mark_synchronized
from camoufox_pm.sync.engine import Binding, Checkpoint
from camoufox_pm.sync.identity import bootstrap, digest
from camoufox_pm.sync.service import Enrollment, SyncService


@pytest.fixture
async def synchronized(profile_manager):
    manager = profile_manager
    profile = await manager.create_profile("Synchronized")
    await manager.storage.create_user("stable-owner", "owner", "unused", "administrator")
    root, _, membership = bootstrap("https://relay.example")
    device_id = membership.membership.devices[0].device_id
    service = SyncService(Path(manager.storage.db.db_path).resolve().parent / "sync-control")
    enrollment = Enrollment(
        local_user_id="stable-owner",
        device_label="Owner",
        root=root,
        membership=membership,
        membership_digest=digest(membership),
        device_id=device_id,
        vault_kind="native",
        phase="ready",
    )
    service.control.create("installation", enrollment.model_dump(mode="json"))
    remote = str(uuid4())
    state = Binding(
        local_id=profile.id,
        checkpoint=Checkpoint(
            owner_id=root.owner_id,
            profile_id=remote,
            membership_epoch=1,
            checkout_generation=1,
            revision_id=None,
            holder_device_id=device_id,
        ),
        phase="held",
        installed_generation=1,
    )
    service.control.create("profile/" + profile.id, state.model_dump(mode="json"))
    service.control.create("mapping/" + remote, {"local_id": profile.id})
    mark_synchronized(manager, profile, root.owner_id, remote)
    return manager, profile, service


def blocked(service, profile):
    record = service.control.get("profile/" + profile.id)
    service.control.update(record.key, {**record.payload, "phase": "handoff"}, record.revision)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "update",
        "fingerprint",
        "refresh",
        "reconcile",
        "tags",
        "geography",
        "trash",
        "clone",
        "export",
    ],
)
async def test_all_manager_mutations_reject_handoff(synchronized, operation, tmp_path):
    manager, profile, service = synchronized
    blocked(service, profile)
    operations = {
        "update": lambda: manager.update_profile(profile.id, {"name": "changed"}),
        "fingerprint": lambda: manager.rotate_profile_fingerprint(profile.id),
        "refresh": lambda: manager.refresh_browser_version(profile.id),
        "reconcile": lambda: manager.reconcile_os(profile.id, True),
        "tags": lambda: manager.bulk_update_tags(
            [{"id": profile.id, "row_version": profile.row_version}], ["changed"], []
        ),
        "geography": lambda: manager.clear_geography([profile.id]),
        "trash": lambda: manager.delete_profile(profile.id),
        "clone": lambda: manager.clone_profile(profile.id, "copy"),
        "export": lambda: manager.export_profile(profile.id, tmp_path / "export.zip"),
    }
    with pytest.raises(SyncBlocked):
        await operations[operation]()
    assert (await manager.storage.get_profile(profile.id)).name == "Synchronized"
    assert (await manager.storage.get_lease(profile.id))[0] is None


@pytest.mark.asyncio
async def test_held_edits_take_and_release_local_lease(synchronized, monkeypatch):
    manager, profile, _ = synchronized
    original = manager.storage.update_profile
    observed = []

    async def checked(*args, **kwargs):
        observed.append((await manager.storage.get_lease(profile.id))[0])
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager.storage, "update_profile", checked)
    await manager.update_profile(profile.id, {"name": "edited"})
    assert observed and observed[0]
    assert (await manager.storage.get_lease(profile.id))[0] is None
    with pytest.raises(SyncBlocked):
        await manager.update_profile(
            profile.id, {"storage_path": str(manager.profiles_dir / "other")}
        )


@pytest.mark.asyncio
async def test_launch_blocked_after_lease_before_browser_start(synchronized, monkeypatch):
    manager, profile, service = synchronized
    blocked(service, profile)
    launch = AsyncMock(side_effect=AssertionError("browser must not start"))
    monkeypatch.setattr(manager.browser_sessions, "launch", launch)
    with pytest.raises(SyncBlocked):
        await manager.launch_browser(profile.id)
    launch.assert_not_called()
    assert (await manager.storage.get_lease(profile.id))[0] is None


@pytest.mark.asyncio
async def test_only_internal_sync_operation_bypasses_remote_hold(synchronized):
    manager, profile, service = synchronized
    blocked(service, profile)
    with pytest.raises(SyncBlocked):
        async with profile_operation(manager, profile.id):
            pytest.fail("ordinary operation must be blocked")
    async with profile_operation(manager, profile.id, sync_operation=True) as found:
        assert found.id == profile.id
        assert (await manager.storage.get_lease(profile.id))[0]


@pytest.mark.asyncio
async def test_unbound_edit_serializes_against_first_sync_enrollment(profile_manager, monkeypatch):
    import asyncio

    from camoufox_pm.core.leases import ProfileLocked

    manager = profile_manager
    profile = await manager.create_profile("Local only")
    entered, resume = asyncio.Event(), asyncio.Event()
    original = manager.storage.update_profile

    async def delayed(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager.storage, "update_profile", delayed)
    edit = asyncio.create_task(manager.update_profile(profile.id, {"name": "edited"}))
    await entered.wait()
    try:
        with pytest.raises(ProfileLocked):
            async with profile_operation(manager, profile.id, sync_operation=True):
                pytest.fail("enrollment must wait for the preexisting ordinary edit")
    finally:
        resume.set()
        await edit


@pytest.mark.asyncio
async def test_child_task_cannot_inherit_profile_admission(profile_manager):
    import asyncio

    from camoufox_pm.core.leases import ProfileLocked
    from camoufox_pm.core.sync_admission import profile_admission

    manager = profile_manager
    profile = await manager.create_profile("Local only")

    async def contender():
        with pytest.raises(ProfileLocked):
            async with profile_admission(manager, profile.id):
                pytest.fail("inherited context must not grant admission")

    async with profile_admission(manager, profile.id):
        async with profile_admission(manager, profile.id):
            await asyncio.create_task(contender())


@pytest.mark.asyncio
async def test_windows_admission_existing_create_race(profile_manager, monkeypatch):
    from pathlib import Path

    from camoufox_pm.core import sync_admission
    from camoufox_pm.core.update_permissions import PermissionProtectionError

    manager = profile_manager
    profile = await manager.create_profile("Local")
    async with sync_admission.profile_admission(manager, profile.id):
        pass
    original_exists = Path.exists
    target = manager.data_dir / "profile-admission" / (profile.id + ".lock")
    observed = False

    def raced_exists(path):
        nonlocal observed
        if path == target and not observed:
            observed = True
            return False
        return original_exists(path)

    def windows_existing(_path):
        raise PermissionProtectionError("Windows CREATE_NEW already exists")

    monkeypatch.setattr(Path, "exists", raced_exists)
    monkeypatch.setattr(sync_admission, "create_private_file", windows_existing)
    async with sync_admission.profile_admission(manager, profile.id):
        assert observed
