"""Offline launch/mutation gates; no native browser or remote service."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from camoufox_pm.core.models import Profile
from camoufox_pm.core.sync_guard import (
    SYNC_MARKER,
    SyncBlocked,
    guard_profile,
    invalidate_all_sync,
    mark_synchronized,
)
from camoufox_pm.sync.engine import Binding, Checkpoint
from camoufox_pm.sync.identity import bootstrap, digest
from camoufox_pm.sync.service import Enrollment, SyncService


@pytest.fixture
def guarded(tmp_path):
    directory = tmp_path.resolve()
    profiles = directory / "profiles"
    profiles.mkdir()
    profile = Profile(
        name="Private", id="profile1", storage_path=str(profiles / "profile_profile1")
    )
    Path(profile.storage_path).mkdir()
    storage = SimpleNamespace(
        db=SimpleNamespace(db_path=str(directory / "app.db")),
        get_profile=AsyncMock(return_value=profile),
        list_users=AsyncMock(return_value=[{"id": "owner-user", "role": "administrator"}]),
    )
    manager = SimpleNamespace(storage=storage, profiles_dir=profiles, data_dir=directory)
    service = SyncService(directory / "sync-control")
    root, _, membership = bootstrap("https://relay.example")
    device_id = membership.membership.devices[0].device_id
    enrollment = Enrollment(
        local_user_id="owner-user",
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
        installed_revision=None,
        installed_generation=1,
    )
    service.control.create("profile/" + profile.id, state.model_dump(mode="json"))
    service.control.create("mapping/" + remote, {"local_id": profile.id})
    mark_synchronized(manager, profile, root.owner_id, remote)
    return manager, profile, service, state


@pytest.mark.asyncio
async def test_valid_offline_holder_needs_live_same_administrator(guarded):
    manager, profile, _, _ = guarded
    assert await guard_profile(manager, profile.id)
    for users in (
        [],
        [{"id": "replacement-same-name", "role": "administrator"}],
        [{"id": "owner-user", "role": "operator"}],
    ):
        manager.storage.list_users.return_value = users
        with pytest.raises(SyncBlocked):
            await guard_profile(manager, profile.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    ["unknown", "handoff", "pending", "conflict", "revoked", "held_elsewhere", "apply_required"],
)
async def test_uncertain_phases_never_launch(guarded, phase):
    manager, profile, service, state = guarded
    record = service.control.get("profile/" + profile.id)
    service.control.update(
        record.key,
        state.model_copy(update={"phase": phase}).model_dump(mode="json"),
        record.revision,
    )
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)


@pytest.mark.asyncio
async def test_missing_control_cannot_turn_marked_profile_local(guarded):
    manager, profile, service, _ = guarded
    service.control.path.unlink()
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)
    assert not service.control.path.exists()
    # Registry independently fences a DB pointer restored to an unmarked directory.
    (Path(profile.storage_path) / SYNC_MARKER).unlink()
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)


@pytest.mark.asyncio
async def test_copied_profile_marker_does_not_transfer_authority(guarded):
    manager, profile, _, _ = guarded
    copied = manager.profiles_dir / "profile_copied01"
    shutil.copytree(profile.storage_path, copied)
    profile.id, profile.storage_path = "copied01", str(copied)
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)


@pytest.mark.asyncio
async def test_complete_apply_journal_checks_actual_db_pointer(guarded):
    manager, profile, service, _ = guarded
    operation = str(uuid4())
    service.control.create("apply/" + profile.id, {"phase": "complete", "operation_id": operation})
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)
    target = manager.profiles_dir / ("sync-" + operation) / "data"
    target.mkdir(parents=True)
    profile.storage_path = str(target)
    assert await guard_profile(manager, profile.id)
    profile.storage_path = str(manager.profiles_dir / "profile_profile1")
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)


@pytest.mark.asyncio
async def test_restore_invalidates_activation_without_erasing_history(guarded):
    manager, profile, service, state = guarded
    service.control.create("event/observed", {"public": "retained"})
    invalidate_all_sync(manager.data_dir)
    record = service.control.get("profile/" + profile.id)
    assert record.payload["checkpoint"] == state.checkpoint.model_dump(mode="json")
    assert record.payload["installed_generation"] is None
    assert service.control.get("event/observed").payload == {"public": "retained"}
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)


@pytest.mark.asyncio
async def test_interrupted_bulk_restore_fence_blocks_untouched_profiles(guarded, monkeypatch):
    from camoufox_pm.sync.control import ControlStore

    manager, profile, service, _ = guarded
    original = ControlStore.batch

    def fail_profiles(self, mutations):
        if any(m.key.startswith("profile/") for m in mutations):
            raise OSError("power lost")
        return original(self, mutations)

    monkeypatch.setattr(ControlStore, "batch", fail_profiles)
    with pytest.raises(OSError):
        invalidate_all_sync(manager.data_dir)
    assert service.control.get("profile/" + profile.id).payload["phase"] == "held"
    assert service.control.get("sync/restore-fence").payload["phase"] == "invalidating"
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)


@pytest.mark.asyncio
async def test_local_only_profile_does_not_need_optional_crypto(tmp_path, monkeypatch):
    import sys

    profile_dir = tmp_path / "profiles" / "profile_local001"
    profile_dir.mkdir(parents=True)
    profile = Profile(name="Local", id="local001", storage_path=str(profile_dir))
    manager = SimpleNamespace(
        data_dir=tmp_path,
        profiles_dir=tmp_path / "profiles",
        storage=SimpleNamespace(
            db=SimpleNamespace(db_path=str(tmp_path / "app.db")),
            get_profile=AsyncMock(return_value=profile),
        ),
    )
    monkeypatch.setitem(sys.modules, "camoufox_pm.sync.engine", None)
    assert not await guard_profile(manager, profile.id)
    assert not (tmp_path / "sync-control").exists()


def test_marker_is_private_idempotent_and_rejects_identity_reassignment(guarded):
    manager, profile, _, state = guarded
    mark_synchronized(manager, profile, state.checkpoint.owner_id, state.checkpoint.profile_id)
    with pytest.raises(SyncBlocked):
        mark_synchronized(manager, profile, state.checkpoint.owner_id, str(uuid4()))
    assert (Path(profile.storage_path) / SYNC_MARKER).is_file()


@pytest.mark.asyncio
async def test_bound_profile_rejects_substituted_marker(guarded):
    manager, profile, service, _ = guarded
    (Path(profile.storage_path) / SYNC_MARKER).write_text("{}")
    with pytest.raises(SyncBlocked):
        await guard_profile(manager, profile.id)


def test_windows_existing_marker_create_error_reopens_only_verified_file(guarded, monkeypatch):
    from camoufox_pm.core import sync_guard
    from camoufox_pm.core.update_permissions import PermissionProtectionError

    manager, profile, service, state = guarded
    original_exists = Path.exists
    targets = {
        manager.data_dir / "sync-markers" / (profile.id + ".json"),
        Path(profile.storage_path) / SYNC_MARKER,
    }
    # Simulate the create race on Windows: the file becomes present after exists.
    observed = set()

    def raced_exists(path):
        if path in targets and path not in observed:
            observed.add(path)
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", raced_exists)

    def windows_existing(_path):
        raise PermissionProtectionError("Windows CREATE_NEW already exists")

    monkeypatch.setattr(sync_guard, "create_private_file", windows_existing)
    mark_synchronized(manager, profile, state.checkpoint.owner_id, state.checkpoint.profile_id)
