"""Key-free synchronization fences shared by API, CLI, scheduler and file operations."""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import UUID

from .update_permissions import (
    PermissionProtectionError,
    create_private_directory,
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)

SYNC_MARKER = ".camoufox-pm-sync.json"
_MESSAGE = "Synchronized profile is blocked; reconcile checkout and recovery before continuing"
_held: ContextVar[frozenset[tuple[object, str]]] = ContextVar(
    "sync_mutation_held", default=frozenset()
)


class SyncBlocked(ValueError):
    """A synchronized profile lacks independently verified local permission."""


def _local_id(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise SyncBlocked(_MESSAGE)
    return value


def _data_dir(manager) -> Path:
    value = getattr(getattr(manager.storage, "db", None), "db_path", None)
    return (
        Path(value).absolute().parent
        if isinstance(value, (str, Path))
        else Path(manager.data_dir).absolute()
    )


def _profile_path(manager, profile) -> Path:
    from .profile_operations import profile_data_path

    return profile_data_path(manager, profile)


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def require_local_lifecycle(manager, profile):
    """Synced deletion/restoration must publish authenticated lifecycle revisions."""
    if _inspect(manager, profile) is not None:
        raise SyncBlocked("Use synchronized Trash or Restore; local lifecycle changes are blocked")


def _inspect(manager, profile) -> tuple[Any, str] | None:
    """No absent-control initialization. A marker never grants any permission."""
    from camoufox_pm.sync.control import ControlStore

    local_id = _local_id(profile.id)
    root = _data_dir(manager)
    registry_marker = root / "sync-markers" / (local_id + ".json")
    marker = _present(registry_marker)
    try:
        marker |= _present(_profile_path(manager, profile) / SYNC_MARKER)
    except ValueError:
        # Invalid local-only paths stay editable through existing manager behavior;
        # registry-marked profiles cannot escape this check by changing their path.
        if marker:
            raise SyncBlocked(_MESSAGE) from None
    directory = root / "sync-control"
    if not _present(directory / "control.sqlite3"):
        if marker:
            raise SyncBlocked(_MESSAGE)
        return None
    try:
        control = ControlStore(directory)
        binding = control.get("profile/" + local_id)
        if binding is None:
            if marker:
                raise SyncBlocked(_MESSAGE)
            return None
        owner = control.get("installation")
        if owner is None or type(owner.payload.get("local_user_id")) is not str:
            raise SyncBlocked(_MESSAGE)
        from camoufox_pm.sync.service import SyncService

        return SyncService(directory), owner.payload["local_user_id"]
    except Exception:
        raise SyncBlocked(_MESSAGE) from None


def _validate(service, user_id: str, manager, profile) -> None:
    from camoufox_pm.sync.engine import may_launch_from_control

    fence = service.control.get("sync/restore-fence")
    if fence is not None and fence.payload.get("phase") != "complete":
        raise SyncBlocked(_MESSAGE)
    if not may_launch_from_control(service, user_id, profile.id):
        raise SyncBlocked(_MESSAGE)
    record = service.control.get("profile/" + profile.id)
    assert record is not None
    checkpoint = record.payload["checkpoint"]
    expected_marker = {
        "version": 1,
        "local_id": profile.id,
        "owner_id": checkpoint["owner_id"],
        "profile_id": checkpoint["profile_id"],
    }
    for marker_path in (
        _data_dir(manager) / "sync-markers" / (profile.id + ".json"),
        _profile_path(manager, profile) / SYNC_MARKER,
    ):
        if not _present(marker_path):
            continue
        try:
            reject_reparse_paths(marker_path)
            descriptor = os.open(
                marker_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                    raise ValueError
                if json.loads(stream.read(4097)) != expected_marker:
                    raise ValueError
        except Exception:
            raise SyncBlocked(_MESSAGE) from None
    journal = service.control.get("apply/" + profile.id)
    if journal is not None:
        value = journal.payload
        if value.get("phase") == "complete":
            operation = value.get("operation_id")
            try:
                if str(UUID(operation)) != operation:
                    raise ValueError
                expected = manager.profiles_dir.resolve() / ("sync-" + operation) / "data"
                if _profile_path(manager, profile) != expected:
                    raise ValueError
            except Exception:
                raise SyncBlocked(_MESSAGE) from None
        elif value.get("phase") != "aborted":
            raise SyncBlocked(_MESSAGE)


async def guard_profile(manager, profile_id: str, *, profile=None) -> bool:
    """Return whether synchronized; reject uncertain ownership before any mutation."""
    from .profile_operations import run_file_operation

    profile = profile or await manager.storage.get_profile(profile_id, include_deleted=True)
    if profile is None:
        return False
    inspected = await run_file_operation(_inspect, manager, profile)
    if inspected is None:
        return False
    service, user_id = inspected
    users = await manager.storage.list_users()
    if not any(user.get("id") == user_id and user.get("role") == "administrator" for user in users):
        raise SyncBlocked(_MESSAGE)
    await run_file_operation(_validate, service, user_id, manager, profile)
    return True


@asynccontextmanager
async def _sync_mutation(manager, profile_id: str):
    """Hold the existing cross-process local lease for synchronized mutations only."""
    from .profile_operations import profile_operation, run_file_operation

    local_id = _local_id(profile_id)
    held_key = (asyncio.current_task(), str(_data_dir(manager)) + "/" + local_id)
    if held_key in _held.get():
        yield True
        return
    profile = await manager.storage.get_profile(local_id, include_deleted=True)
    if profile is None:
        yield False
        return
    if await run_file_operation(_inspect, manager, profile) is None:
        yield False
        return
    # Guard is performed after acquiring the same local lease used by launches
    # and the transfer runtime, so a handoff cannot race an ordinary edit.
    async with profile_operation(manager, local_id, include_deleted=True):
        token = _held.set(_held.get() | {held_key})
        try:
            # An enrollment may have acquired the lease before us; determine
            # synchronized status only after admission, never from the stale read.
            synchronized = await run_file_operation(_inspect, manager, profile) is not None
            yield synchronized
        finally:
            _held.reset(token)


@asynccontextmanager
async def sync_mutation(manager, profile_id: str):
    from .sync_admission import profile_admission

    async with profile_admission(manager, profile_id):
        async with _sync_mutation(manager, profile_id) as synchronized:
            yield synchronized


def sync_mutation_guard(function):
    @wraps(function)
    async def guarded(manager, profile_id, *args, **kwargs):
        async with sync_mutation(manager, profile_id) as synchronized:
            if synchronized and function.__name__ == "update_profile":
                updates = args[0] if args else kwargs.get("updates", {})
                if {"id", "storage_path"} & updates.keys():
                    raise SyncBlocked(
                        "Synchronized profile identity and activation path are immutable"
                    )
            return await function(manager, profile_id, *args, **kwargs)

    return guarded


def mark_synchronized(manager, profile, owner_id: str, remote_profile_id: str) -> None:
    """Write durable conservative markers before exposing a synchronization binding.

    Registry protects an old restored DB pointer; profile marker protects copied
    browser directories. Neither marker transfers membership or local grants.
    """
    local_id = _local_id(profile.id)
    for identifier in (owner_id, remote_profile_id):
        if str(UUID(identifier)) != identifier or UUID(identifier).int == 0:
            raise SyncBlocked(_MESSAGE)
    root = _data_dir(manager)
    registry = root / "sync-markers"
    if not registry.exists():
        try:
            create_private_directory(registry)
        except FileExistsError:
            pass
    profile_path = _profile_path(manager, profile)
    payload = json.dumps(
        {"version": 1, "local_id": local_id, "owner_id": owner_id, "profile_id": remote_profile_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    for path in (registry / (local_id + ".json"), profile_path / SYNC_MARKER):
        reject_reparse_paths(path)
        try:
            if path.exists():
                raise FileExistsError
            descriptor = create_private_file(path)
        except (FileExistsError, PermissionProtectionError):
            # Windows CREATE_NEW maps an existing file to the permission helper's
            # typed error. Reopen only an existing, independently verified file.
            verify_private_path(path)
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size > 4096
                    or stream.read(4097) != payload
                ):
                    raise SyncBlocked(_MESSAGE) from None
            continue
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def invalidate_all_sync(data_dir: Path) -> None:
    """Fence activation BEFORE DB/profile rollback, preserving authority/history.

    Missing control is never recreated. Registry/profile markers then continue to
    block affected profiles. At most 1024 profiles exist per relay owner; batches
    remain conservative under interruption, and callers must abort restore on error.
    """
    from camoufox_pm.sync.control import ControlMutation, ControlStore

    directory = Path(data_dir) / "sync-control"
    if not _present(directory / "control.sqlite3"):
        return
    control = ControlStore(directory)
    # A global fence is durable before batched record changes. It survives a crash
    # between batches; engine reconciliation clears only individual profile proof.
    fence = control.get("sync/restore-fence")
    if fence is None:
        control.create("sync/restore-fence", {"phase": "invalidating"})
    else:
        control.update(fence.key, {"phase": "invalidating"}, fence.revision)
    records = control.list("profile/")
    mutations = [
        ControlMutation(
            r.key, {**r.payload, "phase": "unknown", "installed_generation": None}, r.revision
        )
        for r in records
    ]
    for offset in range(0, len(mutations), 128):
        control.batch(mutations[offset : offset + 128])
    fence = control.get("sync/restore-fence")
    assert fence is not None
    control.update(fence.key, {"phase": "complete"}, fence.revision)
