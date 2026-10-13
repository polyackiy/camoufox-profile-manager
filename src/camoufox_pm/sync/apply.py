"""Journalled same-local-profile activation via an atomic database path pointer.

The caller must authenticate the selected snapshot before invoking this adapter.
Both old and new native Firefox locks stay held through the database CAS. No
locked directory is renamed, including on Windows. Old data and private recovery
archives remain intact; rollback never restores distributed checkout authority.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import stat
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from camoufox_pm.core import profile_archive
from camoufox_pm.core.leases import lease_expired, make_lease_holder
from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.models import Profile
from camoufox_pm.core.profile_operations import (
    profile_data_path,
    profile_operation,
    run_file_operation,
)
from camoufox_pm.core.update_permissions import (
    create_private_directory,
    create_private_file,
    protect_path,
    reject_reparse_paths,
    verify_private_path,
)
from camoufox_pm.sync.control import ControlRecord, ControlStore
from camoufox_pm.sync.quiescence import browser_profile_lock
from camoufox_pm.sync.snapshots import Compatibility, SnapshotMetadata, validate_snapshot

_ERROR = "Synchronized profile activation requires recovery or validation"
_LOCKS = {"lock", ".parentlock", "parent.lock"}


class ApplyError(ValueError):
    """Safe error; the durable journal and existing data remain recoverable."""


@dataclass(frozen=True)
class ApplyResult:
    profile_id: str
    revision_id: str
    operation_id: str
    phase: str
    row_version: int


def _uuid(value: str) -> str:
    if type(value) is not str or str(UUID(value)) != value or UUID(value).int == 0:
        raise ApplyError(_ERROR)
    return value


def _local_id(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ApplyError(_ERROR)
    return value


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _profile_digest(profile: Profile) -> str:
    # StorageManager assigns updated_at during CAS; every other field is bound.
    return hashlib.sha256(
        _json(profile.model_dump(mode="json", exclude={"updated_at"}))
    ).hexdigest()


def _fsync_directory(directory: Path) -> None:
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _hash_file(path: Path) -> str:
    reject_reparse_paths(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ApplyError(_ERROR)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ApplyError(_ERROR)
        digest = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(source.fileno())
        if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise ApplyError(_ERROR)
        return digest.hexdigest()


def _mark_bound_target(manager, control: ControlStore, target: Profile) -> None:
    """Local provenance is written only after authenticated archive verification."""
    from camoufox_pm.core.sync_guard import mark_synchronized
    from camoufox_pm.sync.engine import Binding

    record = control.get("profile/" + target.id)
    if record is None:
        return
    binding = Binding.model_validate(record.payload)
    if binding.local_id != target.id:
        raise ApplyError(_ERROR)
    mark_synchronized(manager, target, binding.checkpoint.owner_id, binding.checkpoint.profile_id)


def _verify_tree(data: Path, metadata: SnapshotMetadata) -> None:
    verify_private_path(data)
    actual = set()
    for path in data.rglob("*"):
        relative = path.relative_to(data).as_posix()
        if relative in _LOCKS or relative == ".camoufox-pm-sync.json":
            # Do not stat/reopen a native lock held exclusively on Windows.
            # browser_profile_lock validated these entries before acquisition.
            continue
        reject_reparse_paths(path)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ApplyError(_ERROR)
        entry = metadata.files.get(relative)
        if entry is None or info.st_size != entry.size or _hash_file(path) != entry.sha256:
            raise ApplyError(_ERROR)
        actual.add(relative)
    if actual != set(metadata.files):
        raise ApplyError(_ERROR)
    for directory in sorted(
        (p for p in data.rglob("*") if p.relative_to(data).as_posix() not in _LOCKS and p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(data)
    _fsync_directory(data.parent)
    _fsync_directory(data.parent.parent)


def _paths(manager, control: ControlStore, operation_id: str) -> tuple[Path, Path]:
    _uuid(operation_id)
    root = manager.profiles_dir.absolute()
    reject_reparse_paths(root)
    if not root.is_dir():
        raise ApplyError(_ERROR)
    # Existing manager storage predates protected sync staging. Tighten this
    # current-user directory; do not relax any existing ACL or follow a symlink.
    protect_path(root)
    staging = root / ("sync-" + operation_id)
    reject_reparse_paths(staging)
    backup_root = control.directory / "apply-backups"
    reject_reparse_paths(backup_root)
    if not backup_root.exists():
        create_private_directory(backup_root)
    verify_private_path(backup_root)
    backup = backup_root / (operation_id + ".zip")
    reject_reparse_paths(backup)
    return staging, backup


def _write_backup(
    profile: Profile, target: Profile, old_data: Path, metadata: SnapshotMetadata, backup: Path
) -> str:
    # The archive contains plaintext credentials, so create privately BEFORE the
    # existing archive writer opens it. No plaintext metadata enters control.db.
    if profile.proxy and profile.proxy.password and profile.proxy.password.startswith("enc:"):
        raise ApplyError(_ERROR)
    for path in old_data.rglob("*"):
        relative = path.relative_to(old_data).as_posix()
        if relative in _LOCKS:
            continue
        reject_reparse_paths(path)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) and (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        ):
            raise ApplyError(_ERROR)
    os.close(create_private_file(backup))
    profile_archive.export_profile(profile, old_data, backup, strict=True)
    with zipfile.ZipFile(backup, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "sync-apply.json",
            _json(
                {
                    "version": 1,
                    "source": profile.model_dump(mode="json"),
                    "target": target.model_dump(mode="json"),
                    "snapshot": metadata.model_dump(mode="json"),
                }
            ),
        )
    verify_private_path(backup)
    with backup.open("r+b") as stream:
        os.fsync(stream.fileno())
    _fsync_directory(backup.parent)
    return _hash_file(backup)


def _read_backup(backup: Path, expected_hash: str) -> tuple[Profile, Profile, SnapshotMetadata]:
    verify_private_path(backup)
    if _hash_file(backup) != expected_hash:
        raise ApplyError(_ERROR)
    with zipfile.ZipFile(backup) as archive:
        info = archive.getinfo("sync-apply.json")
        if info.file_size > 4 * 1024 * 1024 or archive.namelist().count("sync-apply.json") != 1:
            raise ApplyError(_ERROR)
        record = json.loads(archive.read(info))
    if (
        set(record) != {"version", "source", "target", "snapshot"}
        or type(record["version"]) is not int
        or record["version"] != 1
    ):
        raise ApplyError(_ERROR)
    return (
        Profile.model_validate(record["source"]),
        Profile.model_validate(record["target"]),
        SnapshotMetadata.model_validate_json(json.dumps(record["snapshot"])),
    )


def _step(phase: str) -> None:
    """Fault-injection seam immediately after durable steps; no production effect."""


def _advance(control: ControlStore, record: ControlRecord, **updates: Any) -> ControlRecord:
    return control.update(record.key, dict(record.payload, **updates), record.revision)


def _result(record: ControlRecord) -> ApplyResult:
    payload = record.payload
    return ApplyResult(
        payload["profile_id"],
        payload["revision_id"],
        payload["operation_id"],
        payload["phase"],
        payload["expected_row_version"] + (payload["phase"] == "complete"),
    )


async def _lease(manager, profile_id: str, holder: str) -> None:
    lease = await manager.storage.get_lease(profile_id)
    if lease is None or lease[0] != holder or lease_expired(lease[1]):
        raise ApplyError(_ERROR)


async def _authorize(callback):
    if callback is not None:
        value = callback()
        if inspect.isawaitable(value):
            await value


async def apply_snapshot(
    manager,
    profile_id: str,
    authenticated_archive: Path,
    compatibility: Compatibility,
    control: ControlStore,
    revision_id: str,
    expected_row_version: int,
    *,
    maintenance: MaintenanceGate,
    operation_id: str | None = None,
    authorize=None,
) -> ApplyResult:
    """Activate an authenticated snapshot without changing the local profile ID.

    The engine must persist apply-pending first and record_applied only after this
    succeeds. This adapter never changes distributed ownership/freshness grants.
    """
    try:
        profile_id, revision_id = _local_id(profile_id), _uuid(revision_id)
        operation_id = _uuid(operation_id or str(uuid4()))
        if type(expected_row_version) is not int or not 0 <= expected_row_version < 2**53 - 1:
            raise ApplyError(_ERROR)
        holder = make_lease_holder()
        with maintenance.operation():
            async with profile_operation(
                manager, profile_id, holder=holder, sync_operation=True
            ) as profile:
                if profile.row_version != expected_row_version or profile.extensions:
                    raise ApplyError(_ERROR)
                old_data = profile_data_path(manager, profile)
                reject_reparse_paths(old_data)
                staging, backup = _paths(manager, control, operation_id)
                if staging.exists() or backup.exists():
                    raise ApplyError(_ERROR)
                key = "apply/" + profile_id
                previous = control.get(key)
                if previous is not None and previous.payload.get("phase") not in {
                    "complete",
                    "aborted",
                }:
                    raise ApplyError(_ERROR)
                intent = {
                    "version": 1,
                    "profile_id": profile_id,
                    "revision_id": revision_id,
                    "operation_id": operation_id,
                    "phase": "preparing",
                    "expected_row_version": expected_row_version,
                    "source_digest": _profile_digest(profile),
                    "target_digest": None,
                    "backup_sha256": None,
                }
                record = (
                    control.update(key, intent, previous.revision)
                    if previous
                    else control.create(key, intent)
                )
                _step("preparing")
                with ExitStack() as locks:
                    await run_file_operation(locks.enter_context, browser_profile_lock(old_data))
                    metadata = await run_file_operation(
                        validate_snapshot, authenticated_archive, staging, compatibility
                    )
                    if metadata.lifecycle == "trashed":
                        raise ApplyError(
                            "Deletion snapshots require explicit lifecycle confirmation"
                        )
                    new_data = staging / "data"
                    await run_file_operation(locks.enter_context, browser_profile_lock(new_data))
                    values = profile.model_dump(mode="json")
                    values.update(metadata.profile.apply_fields())
                    values.update(storage_path=str(new_data), row_version=expected_row_version + 1)
                    if values["proxy"] != profile.model_dump(mode="json")["proxy"]:
                        values["proxy_check"] = None
                    target = Profile.model_validate(values)
                    await run_file_operation(_verify_tree, new_data, metadata)
                    await run_file_operation(_mark_bound_target, manager, control, target)
                    _step("staged")
                    backup_hash = await run_file_operation(
                        _write_backup, profile, target, old_data, metadata, backup
                    )
                    _step("backup_written")
                    record = _advance(
                        control,
                        record,
                        phase="prepared",
                        target_digest=_profile_digest(target),
                        backup_sha256=backup_hash,
                    )
                    _step("prepared")
                    await _lease(manager, profile_id, holder)
                    await run_file_operation(_verify_tree, new_data, metadata)
                    current = await manager.storage.get_profile(profile_id)
                    if (
                        current is None
                        or _profile_digest(current) != record.payload["source_digest"]
                    ):
                        raise ApplyError(_ERROR)
                    await _authorize(authorize)
                    record = _advance(control, record, phase="publishing")
                    _step("publishing")
                    await manager.storage.durable_update_profile(
                        target, expected_row_version=expected_row_version
                    )
                    _step("database_committed")
                    current = await manager.storage.get_profile(profile_id)
                    if (
                        current is None
                        or _profile_digest(current) != record.payload["target_digest"]
                    ):
                        raise ApplyError(_ERROR)
                    await _lease(manager, profile_id, holder)
                    record = _advance(control, record, phase="complete")
                    _step("complete")
                    return _result(record)
    except Exception:
        raise ApplyError(_ERROR) from None


async def recover_apply(
    manager, profile_id: str, control: ControlStore, *, maintenance: MaintenanceGate, authorize=None
) -> ApplyResult | None:
    """Resolve an interrupted CAS without overwriting unrelated edits.

    Before the CAS: retain old data, abort the attempt. After a verifiable CAS:
    finish its journal. Any mismatch remains blocked for explicit recovery.
    """
    try:
        profile_id = _local_id(profile_id)
        key = "apply/" + profile_id
        holder = make_lease_holder()
        with maintenance.operation():
            async with profile_operation(
                manager, profile_id, holder=holder, sync_operation=True
            ) as profile:
                record = control.get(key)
                if record is None:
                    return None
                p = record.payload
                if (
                    set(p)
                    != {
                        "version",
                        "profile_id",
                        "revision_id",
                        "operation_id",
                        "phase",
                        "expected_row_version",
                        "source_digest",
                        "target_digest",
                        "backup_sha256",
                    }
                    or type(p["version"]) is not int
                    or p["version"] != 1
                    or p["profile_id"] != profile_id
                    or p["phase"]
                    not in {"preparing", "prepared", "publishing", "complete", "aborted"}
                ):
                    raise ApplyError(_ERROR)
                _uuid(p["revision_id"])
                staging, backup = _paths(manager, control, p["operation_id"])
                actual = _profile_digest(profile)
                if actual == p["source_digest"] and p["phase"] in {
                    "preparing",
                    "prepared",
                    "publishing",
                    "aborted",
                }:
                    with browser_profile_lock(profile_data_path(manager, profile)):
                        await _lease(manager, profile_id, holder)
                        if p["phase"] != "aborted":
                            record = _advance(control, record, phase="aborted")
                        return _result(record)
                if (
                    p["phase"] not in {"publishing", "complete"}
                    or not p["backup_sha256"]
                    or actual != p["target_digest"]
                    or Path(profile.storage_path or "") != staging / "data"
                    or profile.row_version != p["expected_row_version"] + 1
                ):
                    raise ApplyError(_ERROR)
                source, target, metadata = await run_file_operation(
                    _read_backup, backup, p["backup_sha256"]
                )
                if (
                    source.id != profile_id
                    or target.id != profile_id
                    or _profile_digest(source) != p["source_digest"]
                    or _profile_digest(target) != p["target_digest"]
                ):
                    raise ApplyError(_ERROR)
                with ExitStack() as locks:
                    await run_file_operation(
                        locks.enter_context,
                        browser_profile_lock(profile_data_path(manager, source)),
                    )
                    await run_file_operation(
                        locks.enter_context, browser_profile_lock(staging / "data")
                    )
                    await run_file_operation(_mark_bound_target, manager, control, target)
                    await run_file_operation(_verify_tree, staging / "data", metadata)
                    await _lease(manager, profile_id, holder)
                    current = await manager.storage.get_profile(profile_id)
                    if current is None or _profile_digest(current) != p["target_digest"]:
                        raise ApplyError(_ERROR)
                    await _authorize(authorize)
                    if p["phase"] != "complete":
                        record = _advance(control, record, phase="complete")
                    return _result(record)
    except Exception:
        raise ApplyError(_ERROR) from None
