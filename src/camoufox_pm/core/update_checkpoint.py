"""Offline update checkpoints. The caller must hold an exclusive maintenance gate.

The callback is an assertion, not a lock: it must remain true for the entire call,
with all browsers, database writers and filesystem operations stopped. Include all
configured storage roots and credential/config files in ``paths``. SQLite contains
views, schedules, templates and application metadata; external files must be listed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import stat
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .update_permissions import (
    create_private_directory,
    create_private_file,
    protect_path,
    verify_private_path,
)


class CheckpointError(RuntimeError):
    """Checkpoint validation or maintenance precondition failed."""


def _gate(check: Callable[[], bool]) -> None:
    if not check():
        raise CheckpointError("An exclusive offline maintenance gate is required")


def _is_profile_lock(path: Path, root: Path | None) -> bool:
    """Recognize only Firefox's POSIX root lock signature, without following it.

    This is not proof a browser stopped. The exclusive offline contract remains
    required. Never remove the live/failed-state link or copy it into a snapshot.
    Mozilla creates this link as IPv4:[+]PID, not a pointer to profile data:
    https://github.com/mozilla-firefox/firefox/blob/main/toolkit/profile/nsProfileLock.cpp
    """
    if (
        root is None
        or os.name == "nt"
        or path.name != "lock"
        or path.parent.parent != root
        or not re.fullmatch(r"profile_[A-Za-z0-9_-]+", path.parent.name)
        or not path.is_symlink()
    ):
        return False
    signature = re.fullmatch(r"([0-9.]+):\+?([1-9][0-9]*)", os.readlink(path))
    if not signature:
        return False
    try:
        ipaddress.IPv4Address(signature[1])
    except ipaddress.AddressValueError:
        return False
    return True


def _safe(path: Path, *, profile_locks: bool = False) -> Path:
    path = path.expanduser().absolute()
    if any(
        part.is_symlink()
        or (part.exists() and getattr(part.stat(), "st_file_attributes", 0) & 0x400)
        for part in (path, *path.parents)
    ):
        raise CheckpointError(f"Symlinks are not supported: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if _is_profile_lock(child, path if profile_locks else None):
                continue
            if (
                child.is_symlink()
                or getattr(child.stat(), "st_file_attributes", 0) & 0x400
                or not (child.is_file() or child.is_dir())
            ):
                raise CheckpointError(f"Unsupported filesystem entry: {child}")
    elif path.exists() and not path.is_file():
        raise CheckpointError(f"Unsupported filesystem entry: {path}")
    return path.resolve()


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    _safe(temporary)
    if temporary.exists():
        temporary.unlink()
    descriptor = create_private_file(temporary)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _sync_dir(path.parent)


def _inventory(path: Path, *, profile_locks: bool = False) -> dict:
    result: dict[str, dict[str, str | int]] = {}
    entries = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
    for entry in entries:
        if _is_profile_lock(entry, path if profile_locks else None):
            continue
        if entry.is_symlink():
            raise CheckpointError(f"Unsupported filesystem entry: {entry}")
        key = "." if entry == path else entry.relative_to(path).as_posix()
        if entry.is_dir():
            result[key] = {"kind": "directory"}
        else:
            digest = hashlib.sha256()
            with entry.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[key] = {
                "kind": "file",
                "size": entry.stat().st_size,
                "sha256": digest.hexdigest(),
                "executable": os.name != "nt" and bool(entry.stat().st_mode & stat.S_IXUSR),
            }
    return result


def _copy(source: Path, target: Path, *, profile_lock_root: Path | None = None) -> None:
    if source.is_symlink():
        raise CheckpointError(f"Unsupported filesystem entry: {source}")
    if source.is_dir():
        create_private_directory(target)
        for child in sorted(source.iterdir()):
            if _is_profile_lock(child, profile_lock_root):
                continue
            _copy(child, target / child.name, profile_lock_root=profile_lock_root)
        _sync_dir(target)
    else:
        descriptor = create_private_file(target)
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            if os.name != "nt" and source.stat().st_mode & stat.S_IXUSR:
                os.fchmod(output.fileno(), 0o700)
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        verify_private_path(target)
    _sync_dir(target.parent)


def _protect_tree(path: Path, *, profile_locks: bool = False) -> None:
    protect_path(path)
    if path.is_dir():
        for child in path.rglob("*"):
            if _is_profile_lock(child, path if profile_locks else None):
                continue
            protect_path(child)


def _integrity(path: Path) -> None:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise CheckpointError("SQLite checkpoint failed integrity verification")


def create_checkpoint(
    checkpoint_dir: Path,
    *,
    db_path: Path,
    paths: Mapping[str, Path],
    maintenance_check: Callable[[], bool],
    required_paths: tuple[str, ...] = (),
) -> Path:
    """Create an immutable-by-convention checkpoint, rejecting incomplete copies.

    Missing optional paths are recorded as absent, so rollback removes newly
    created state there. Labels are metadata only; numbered payloads prevent path
    traversal. Sources must be distinct roots (do not list files inside a root).
    """
    _gate(maintenance_check)
    root = _safe(Path(checkpoint_dir))
    sources = {"database": _safe(Path(db_path))}
    if "database" in paths:
        raise CheckpointError("The database label is reserved")
    sources.update(
        {name: _safe(Path(path), profile_locks=name == "profiles") for name, path in paths.items()}
    )
    for name in required_paths:
        if name not in sources or not sources[name].exists():
            raise CheckpointError(f"Required checkpoint source is missing: {name}")
    values = list(sources.values())
    if any(_overlaps(root, source) for source in values) or any(
        _overlaps(a, b) for i, a in enumerate(values) for b in values[i + 1 :]
    ):
        raise CheckpointError("Checkpoint and source paths must not overlap")
    if not sources["database"].is_file():
        raise CheckpointError("The application database is missing")
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    create_private_directory(root)
    journal: dict[str, Any] = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "creating",
        "entries": [],
    }
    _write(root / "journal.json", journal)
    for index, (name, source) in enumerate(sources.items()):
        _gate(maintenance_check)
        payload = root / str(index)
        entry: dict[str, Any] = {
            "label": name,
            "target": str(source),
            "payload": str(index),
            "exists": source.exists(),
        }
        if source.exists():
            if name == "database":
                os.close(create_private_file(payload))
                with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as live:
                    with closing(sqlite3.connect(payload)) as backup:
                        live.backup(backup)
                        backup.execute("PRAGMA journal_mode=DELETE")
                protect_path(payload)
                with payload.open("r+b") as handle:
                    os.fsync(handle.fileno())
                _integrity(payload)
            else:
                before = _inventory(source, profile_locks=name == "profiles")
                if name == "profiles":
                    _copy(source, payload, profile_lock_root=source)
                else:
                    _copy(source, payload)
                if before != _inventory(
                    source, profile_locks=name == "profiles"
                ) or before != _inventory(payload):
                    raise CheckpointError(f"Source changed while copying: {source}")
            entry["inventory"] = _inventory(payload)
        journal["entries"].append(entry)
        _write(root / "journal.json", journal)
    _gate(maintenance_check)
    journal["state"] = "ready"
    _write(root / "journal.json", journal)
    return root


def restore_checkpoint(
    checkpoint_dir: Path,
    *,
    maintenance_check: Callable[[], bool],
    db_path: Path,
    paths: Mapping[str, Path],
) -> Path:
    """Restore or resume restoration. Keep failed newer data in sibling paths.

    Every original is renamed on its own filesystem before replacement. A durable
    journal allows retries after interruption. A partially restored application
    must remain offline until this call succeeds. Never delete preserved state.
    """
    _gate(maintenance_check)
    root = _safe(Path(checkpoint_dir))
    verify_private_path(root)
    verify_private_path(root / "journal.json")
    journal = json.loads((root / "journal.json").read_text())
    if journal.get("version") != 1 or journal.get("state") not in {
        "ready",
        "restoring",
        "restored",
    }:
        raise CheckpointError("Checkpoint is incomplete or unsupported")
    trusted = {
        "database": _safe(Path(db_path)),
        **{
            name: _safe(Path(path), profile_locks=name == "profiles")
            for name, path in paths.items()
        },
    }
    if "database" in paths or {entry["label"] for entry in journal["entries"]} != set(trusted):
        raise CheckpointError("Checkpoint sources differ from trusted restore configuration")
    targets: list[Path] = []
    for index, entry in enumerate(journal["entries"]):
        if entry["payload"] != str(index):
            raise CheckpointError("Invalid checkpoint payload")
        target = _safe(Path(entry["target"]), profile_locks=entry["label"] == "profiles")
        if target != trusted[entry["label"]]:
            raise CheckpointError("Untrusted restore destination")
        if _overlaps(root, target) or any(_overlaps(target, old) for old in targets):
            raise CheckpointError("Overlapping restore destinations")
        targets.append(target)
        if entry["exists"]:
            payload = _safe(root / str(index))
            verify_private_path(payload)
            if payload.is_dir():
                for child in payload.rglob("*"):
                    verify_private_path(child)
            if _inventory(payload) != entry["inventory"]:
                raise CheckpointError("Checkpoint digest verification failed")
            if entry["label"] == "database":
                _integrity(payload)
    journal["state"] = "restoring"
    _write(root / "journal.json", journal)
    for index, entry in enumerate(journal["entries"]):
        _gate(maintenance_check)
        target = targets[index]
        failed = target.with_name(target.name + ".failed-" + root.name)
        stage = target.with_name(target.name + ".restore-" + root.name)
        if entry.get("restored"):
            continue
        _safe(failed, profile_locks=entry["label"] == "profiles")
        _safe(stage)
        if not entry.get("preserving"):
            if failed.exists() or stage.exists():
                raise CheckpointError("Recovery destination already exists")
            entry["preserving"] = True
            _write(root / "journal.json", journal)
        if not entry.get("preserved"):
            if target.exists() and not failed.exists():
                os.replace(target, failed)
                _protect_tree(failed, profile_locks=entry["label"] == "profiles")
                _sync_dir(target.parent)
            if failed.exists():
                _protect_tree(failed, profile_locks=entry["label"] == "profiles")
            entry["preserved"] = True
            _write(root / "journal.json", journal)
        # SQLite WAL belongs to the failed database, never the restored snapshot.
        if entry["label"] == "database":
            for suffix in ("-wal", "-shm"):
                sidecar = _safe(Path(str(target) + suffix))
                saved = Path(str(failed) + suffix)
                if sidecar.exists():
                    if saved.exists():
                        raise CheckpointError("Preserved SQLite sidecar already exists")
                    os.replace(sidecar, saved)
                    protect_path(saved)
                    _sync_dir(target.parent)
        if entry["exists"]:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not target.exists():
                if stage.exists():
                    if stage.is_dir():
                        shutil.rmtree(stage)
                    else:
                        stage.unlink()
                _copy(root / str(index), stage)
                os.replace(stage, target)
                _sync_dir(target.parent)
            if _inventory(target) != entry["inventory"]:
                raise CheckpointError("Restored data failed verification")
        elif target.exists():
            raise CheckpointError("Unexpected data in absent restore destination")
        entry["restored"] = True
        _write(root / "journal.json", journal)
    _gate(maintenance_check)
    journal["state"] = "restored"
    _write(root / "journal.json", journal)
    return root / "journal.json"
