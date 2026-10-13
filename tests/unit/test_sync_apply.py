"""Same-ID activation, crash journals and cooperative browser exclusion."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.models import Profile, Schedule
from camoufox_pm.core.update_permissions import create_private_directory
from camoufox_pm.sync import apply as adapter
from camoufox_pm.sync.control import ControlStore
from camoufox_pm.sync.quiescence import browser_profile_lock
from camoufox_pm.sync.snapshots import Compatibility, build_snapshot


@pytest.fixture
async def area(tmp_path, monkeypatch):
    from camoufox_pm.sync import quiescence

    monkeypatch.setattr(quiescence, "assert_no_browser_process", lambda _: None)
    root = tmp_path.resolve()
    data = root / "local"
    create_private_directory(data)
    profiles = data / "profiles"
    create_private_directory(profiles)
    old = profiles / "profile_Abc123xy"
    create_private_directory(old)
    (old / "cookies.sqlite").write_bytes(b"old local cookies")
    storage = StorageManager(str(data / "profiles.db"))
    await storage.initialize()
    profile = Profile(
        id="Abc123xy",
        name="Old local",
        storage_path=str(old),
        group="local-team",
        notes="old private note",
        tags=["old"],
    )
    await storage.save_profile(profile)
    await storage.db.create_user("operator-id", "operator", "hash", role="operator")
    await storage.set_profile_grant("operator", profile.id, can_export=True)
    await storage.save_schedule(
        Schedule(profile_id=profile.id, action="launch", kind="interval", interval_minutes=60)
    )
    manager = SimpleNamespace(
        storage=storage,
        data_dir=data,
        profiles_dir=profiles,
        lease_holder="manager-test",
        browser_sessions=SimpleNamespace(is_live=lambda _: False),
    )
    control = ControlStore(root / "sync-control")
    maintenance = MaintenanceGate(root / "maintenance" / "gate.sqlite3")
    remote = root / "remote"
    create_private_directory(remote)
    remote_data = remote / "browser"
    create_private_directory(remote_data)
    (remote_data / "cookies.sqlite").write_bytes(b"new authenticated cookies")
    compatibility = Compatibility(browser_build="test-build", os="linux", architecture="x86_64")
    archive = remote / "snapshot.zip"
    build_snapshot(
        Profile(
            name="Synced profile",
            notes="never-put-this-in-control",
            tags=["remote"],
            fingerprint={"navigator.hardwareConcurrency": 8},
        ),
        remote_data,
        archive,
        compatibility,
    )
    context = SimpleNamespace(
        manager=manager,
        profile=profile,
        old=old,
        control=control,
        maintenance=maintenance,
        archive=archive,
        compatibility=compatibility,
        revision=str(uuid4()),
        operation=str(uuid4()),
    )
    yield context
    await storage.close()


async def activate(area, **changes):
    args = {
        "manager": area.manager,
        "profile_id": area.profile.id,
        "authenticated_archive": area.archive,
        "compatibility": area.compatibility,
        "control": area.control,
        "revision_id": area.revision,
        "expected_row_version": 0,
        "maintenance": area.maintenance,
        "operation_id": area.operation,
    }
    args.update(changes)
    return await adapter.apply_snapshot(**args)


async def recover(area):
    return await adapter.recover_apply(
        area.manager, area.profile.id, area.control, maintenance=area.maintenance
    )


async def test_same_id_publish_preserves_grants_schedules_and_old_data(area):
    grants = await area.manager.storage.list_profile_grants("operator")
    schedules = await area.manager.storage.list_schedules(area.profile.id)
    result = await activate(area)
    assert result.phase == "complete" and result.row_version == 1
    profile = await area.manager.storage.get_profile(area.profile.id)
    assert profile.id == area.profile.id and profile.group == "local-team"
    assert profile.name == "Synced profile" and profile.tags == ["remote"]
    assert profile.fingerprint == {"navigator.hardwareConcurrency": 8}
    assert (
        Path(profile.storage_path) / "cookies.sqlite"
    ).read_bytes() == b"new authenticated cookies"
    assert (area.old / "cookies.sqlite").read_bytes() == b"old local cookies"
    assert await area.manager.storage.list_profile_grants("operator") == grants
    assert await area.manager.storage.list_schedules(area.profile.id) == schedules
    assert b"never-put-this-in-control" not in area.control.path.read_bytes()
    assert (area.control.directory / "apply-backups" / (area.operation + ".zip")).is_file()
    assert (await recover(area)).phase == "complete"


@pytest.mark.parametrize(
    "phase",
    [
        "preparing",
        "staged",
        "backup_written",
        "prepared",
        "publishing",
        "database_committed",
        "complete",
    ],
)
async def test_crash_after_each_durable_step_recovers_without_data_loss(area, monkeypatch, phase):
    class Crash(BaseException):
        pass

    def fail(step):
        if step == phase:
            raise Crash()

    monkeypatch.setattr(adapter, "_step", fail)
    with pytest.raises(Crash):
        await activate(area)
    monkeypatch.setattr(adapter, "_step", lambda _: None)
    result = await recover(area)
    profile = await area.manager.storage.get_profile(area.profile.id)
    committed = phase in {"database_committed", "complete"}
    assert result.phase == ("complete" if committed else "aborted")
    assert profile.row_version == (1 if committed else 0)
    assert (Path(profile.storage_path) / "cookies.sqlite").read_bytes() == (
        b"new authenticated cookies" if committed else b"old local cookies"
    )
    assert (area.old / "cookies.sqlite").read_bytes() == b"old local cookies"


async def test_stale_row_version_refused_before_journal(area):
    with pytest.raises(adapter.ApplyError):
        await activate(area, expected_row_version=7)
    assert area.control.get("apply/" + area.profile.id) is None
    assert (area.old / "cookies.sqlite").read_bytes() == b"old local cookies"


async def test_concurrent_edit_not_overwritten_after_prepare(area, monkeypatch):
    original = adapter._step

    def edit(phase):
        if phase == "publishing":
            connection = area.manager.storage.db._connection
            connection.execute(
                "UPDATE profiles SET name='concurrent user edit',row_version=row_version+1 WHERE id=?",
                (area.profile.id,),
            )
            connection.commit()
        return original(phase)

    monkeypatch.setattr(adapter, "_step", edit)
    with pytest.raises(adapter.ApplyError):
        await activate(area)
    assert (await area.manager.storage.get_profile(area.profile.id)).name == "concurrent user edit"
    with pytest.raises(adapter.ApplyError):
        await recover(area)


async def test_subsequent_edit_never_rolled_back_by_recovery(area):
    await activate(area)
    profile = await area.manager.storage.get_profile(area.profile.id)
    profile.name = "new local change"
    await area.manager.storage.update_profile(profile, expected_row_version=profile.row_version)
    with pytest.raises(adapter.ApplyError):
        await recover(area)
    assert (await area.manager.storage.get_profile(area.profile.id)).name == "new local change"


async def test_incompatible_snapshot_preserves_old_profile(area):
    incompatible = area.compatibility.model_copy(update={"browser_build": "other-build"})
    with pytest.raises(adapter.ApplyError):
        await activate(area, compatibility=incompatible)
    assert (await area.manager.storage.get_profile(area.profile.id)).row_version == 0
    assert (area.old / "cookies.sqlite").read_bytes() == b"old local cookies"
    assert (await recover(area)).phase == "aborted"


async def test_corrupt_staging_cannot_be_activated(area, monkeypatch):
    def corrupt(phase):
        if phase == "staged":
            staged = (
                area.manager.profiles_dir / ("sync-" + area.operation) / "data" / "cookies.sqlite"
            )
            staged.write_bytes(b"tampered staging")

    monkeypatch.setattr(adapter, "_step", corrupt)
    with pytest.raises(adapter.ApplyError):
        await activate(area)
    assert (await area.manager.storage.get_profile(area.profile.id)).row_version == 0
    assert (await recover(area)).phase == "aborted"


async def test_active_browser_manager_and_native_lock_block_apply(area):
    area.manager.browser_sessions.is_live = lambda _: True
    with pytest.raises(adapter.ApplyError):
        await activate(area)
    area.manager.browser_sessions.is_live = lambda _: False
    with browser_profile_lock(area.old):
        with pytest.raises(adapter.ApplyError):
            await activate(area)
    assert (await area.manager.storage.get_profile(area.profile.id)).row_version == 0


async def test_other_process_lease_blocks_apply(area):
    assert await area.manager.storage.acquire_lease(area.profile.id, "other-holder", 300)
    with pytest.raises(adapter.ApplyError):
        await activate(area)
    assert area.control.get("apply/" + area.profile.id) is None
    await area.manager.storage.release_lease(area.profile.id, "other-holder")


async def test_both_native_locks_held_at_database_commit(area, monkeypatch):
    original = area.manager.storage.durable_update_profile
    called = []

    async def update(profile, expected_row_version=None):
        called.append(True)
        for path in [area.old, Path(profile.storage_path)]:
            with pytest.raises(ValueError), browser_profile_lock(path):
                pytest.fail("lock unexpectedly available")
        return await original(profile, expected_row_version)

    monkeypatch.setattr(area.manager.storage, "durable_update_profile", update)
    assert (await activate(area)).phase == "complete"
    assert called == [True]


async def test_cancel_drains_worker_before_releasing_profile_lease(area, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = adapter.validate_snapshot

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(adapter, "validate_snapshot", blocked)
    task = asyncio.create_task(activate(area))
    assert await asyncio.to_thread(entered.wait, 10)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert (await area.manager.storage.get_lease(area.profile.id))[0] is not None
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await area.manager.storage.get_lease(area.profile.id))[0] is None
    assert (await area.manager.storage.get_profile(area.profile.id)).row_version == 0


async def test_private_backup_tampering_blocks_recovery(area, monkeypatch):
    class Crash(BaseException):
        pass

    def fail(phase):
        if phase == "database_committed":
            raise Crash()

    monkeypatch.setattr(adapter, "_step", fail)
    with pytest.raises(Crash):
        await activate(area)
    backup = area.control.directory / "apply-backups" / (area.operation + ".zip")
    with backup.open("ab") as stream:
        stream.write(b"tampering")
    with pytest.raises(adapter.ApplyError):
        await recover(area)
    assert area.control.get("apply/" + area.profile.id).payload["phase"] == "publishing"


@pytest.mark.parametrize("bad", ["../escape", "/absolute", "a/b", "a" * 65])
async def test_malicious_local_paths_rejected(area, bad):
    with pytest.raises(adapter.ApplyError):
        await activate(area, profile_id=bad)
    assert area.control.list("apply/") == ()


async def test_database_rollback_cannot_reactivate_old_directory(area):
    await activate(area)
    connection = area.manager.storage.db._connection
    connection.execute(
        "UPDATE profiles SET storage_path=?,row_version=0 WHERE id=?",
        (str(area.old), area.profile.id),
    )
    connection.commit()
    with pytest.raises(adapter.ApplyError):
        await recover(area)
    assert area.control.get("apply/" + area.profile.id).payload["phase"] == "complete"


async def test_local_extensions_require_compatibility_mapping_before_apply(area):
    profile = await area.manager.storage.get_profile(area.profile.id)
    profile.extensions = ["local-addon"]
    await area.manager.storage.update_profile(profile, expected_row_version=0)
    with pytest.raises(adapter.ApplyError):
        await activate(area, expected_row_version=1)
    assert area.control.get("apply/" + area.profile.id) is None


async def test_maintenance_gate_blocks_before_profile_changes(area):
    area.maintenance.acquire("manager-update")
    try:
        with pytest.raises(adapter.ApplyError):
            await activate(area)
        assert area.control.get("apply/" + area.profile.id) is None
    finally:
        area.maintenance.release("manager-update")


async def test_lost_lease_never_commits_profile_pointer(area, monkeypatch):
    original = adapter._step

    def steal(phase):
        if phase == "prepared":
            connection = area.manager.storage.db._connection
            connection.execute(
                "UPDATE profiles SET locked_by='other-process' WHERE id=?", (area.profile.id,)
            )
            connection.commit()
        return original(phase)

    monkeypatch.setattr(adapter, "_step", steal)
    with pytest.raises(adapter.ApplyError):
        await activate(area)
    assert (await area.manager.storage.get_profile(area.profile.id)).row_version == 0
    assert area.control.get("apply/" + area.profile.id).payload["phase"] == "prepared"


async def test_native_os_locks_block_independent_process_during_pointer_commit(area, monkeypatch):
    import subprocess
    import sys

    original = area.manager.storage.durable_update_profile
    script = """
import os, sys
from pathlib import Path
path = Path(sys.argv[1]) / ("parent.lock" if os.name == "nt" else ".parentlock")
try:
    with path.open("r+b") as stream:
        if os.name != "nt":
            import fcntl
            fcntl.lockf(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except (PermissionError, BlockingIOError):
    sys.exit(3)
sys.exit(0)
"""
    checked = []

    async def commit(profile, expected_row_version):
        for directory in [area.old, Path(profile.storage_path)]:
            result = subprocess.run(
                [sys.executable, "-c", script, str(directory)], capture_output=True, timeout=10
            )
            assert result.returncode == 3, result.stderr.decode()
            checked.append(directory)
        return await original(profile, expected_row_version)

    monkeypatch.setattr(area.manager.storage, "durable_update_profile", commit)
    await activate(area)
    assert len(checked) == 2


async def test_bound_activation_writes_local_markers_and_recovers(area):
    from camoufox_pm.core.sync_guard import SYNC_MARKER
    from camoufox_pm.sync.engine import Binding, Checkpoint

    checkpoint = Checkpoint(
        owner_id=str(uuid4()),
        profile_id=str(uuid4()),
        membership_epoch=1,
        checkout_generation=1,
        revision_id=area.revision,
        holder_device_id=str(uuid4()),
    )
    binding = Binding(local_id=area.profile.id, checkpoint=checkpoint, phase="apply_required")
    area.control.create("profile/" + area.profile.id, binding.model_dump(mode="json"))
    await activate(area)
    profile = await area.manager.storage.get_profile(area.profile.id)
    marker = Path(profile.storage_path) / SYNC_MARKER
    assert marker.is_file()
    registry = area.manager.data_dir / "sync-markers" / (profile.id + ".json")
    assert registry.read_bytes() == marker.read_bytes()
    assert (await recover(area)).phase == "complete"
    marker.write_text("substituted binding")
    with pytest.raises(adapter.ApplyError):
        await recover(area)


async def test_revoked_authorization_after_backup_never_publishes(area):
    checks = []

    async def authorize():
        checks.append(area.control.get("apply/" + area.profile.id).payload["phase"])
        raise PermissionError("owner revoked")

    with pytest.raises(adapter.ApplyError):
        await activate(area, authorize=authorize)
    assert checks == ["prepared"]
    assert (await area.manager.storage.get_profile(area.profile.id)).storage_path == str(area.old)
    assert area.control.get("apply/" + area.profile.id).payload["phase"] == "prepared"
