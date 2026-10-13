"""Offline checkpoints preserve all configured bytes and resume rollback."""

import json
import sqlite3
from contextlib import closing

import pytest

from camoufox_pm.core.update_checkpoint import (
    CheckpointError,
    create_checkpoint,
    restore_checkpoint,
)


def setup_state(tmp_path):
    db = tmp_path / "state.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE settings (value TEXT)")
        conn.execute("INSERT INTO settings VALUES ('old')")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "cookies").write_bytes(b"private-cookie")
    key = tmp_path / "secret.key"
    key.write_bytes(b"private-key")
    return db, {"profiles": profiles, "key": key, "optional": tmp_path / "optional"}


def test_complete_restore_and_preserve(tmp_path):
    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    (paths["profiles"] / "cookies").write_bytes(b"new")
    paths["optional"].write_text("new setting")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE settings SET value = 'new'")
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert (paths["profiles"] / "cookies").read_bytes() == b"private-cookie"
    assert (tmp_path / "profiles.failed-checkpoint" / "cookies").read_bytes() == b"new"
    assert not paths["optional"].exists()
    with closing(sqlite3.connect(db)) as conn, conn:
        assert conn.execute("SELECT value FROM settings").fetchone() == ("old",)
    if __import__("os").name != "nt":
        assert (root / "0").stat().st_mode & 0o777 == 0o600
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)


def test_gate_required(tmp_path):
    db, paths = setup_state(tmp_path)
    with pytest.raises(CheckpointError, match="maintenance"):
        create_checkpoint(
            tmp_path / "checkpoint", db_path=db, paths=paths, maintenance_check=lambda: False
        )


def test_digest_and_trusted_destination(tmp_path):
    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    journal = json.loads((root / "journal.json").read_text())
    journal["entries"][0]["target"] = str(tmp_path / "other.db")
    (root / "journal.json").write_text(json.dumps(journal))
    with pytest.raises(CheckpointError, match="Untrusted"):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    journal["entries"][0]["target"] = str(db)
    (root / "journal.json").write_text(json.dumps(journal))
    (root / "2").write_text("tampered")
    with pytest.raises(CheckpointError, match="digest"):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)


def test_reject_symlinks_overlap_missing(tmp_path):
    db, paths = setup_state(tmp_path)
    with pytest.raises(CheckpointError, match="missing"):
        create_checkpoint(
            tmp_path / "checkpoint",
            db_path=db,
            paths=paths,
            required_paths=("optional",),
            maintenance_check=lambda: True,
        )
    with pytest.raises(CheckpointError, match="overlap"):
        create_checkpoint(
            paths["profiles"] / "checkpoint",
            db_path=db,
            paths=paths,
            maintenance_check=lambda: True,
        )
    try:
        (paths["profiles"] / "linked").symlink_to(paths["key"])
    except OSError:
        pytest.skip("Native symlink creation requires Windows developer mode")
    with pytest.raises(CheckpointError, match="Unsupported"):
        create_checkpoint(
            tmp_path / "checkpoint", db_path=db, paths=paths, maintenance_check=lambda: True
        )


def test_resume_after_gate_lost(tmp_path):
    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    count = 0

    def gate():
        nonlocal count
        count += 1
        return count < 3

    with pytest.raises(CheckpointError):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=gate)
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert json.loads((root / "journal.json").read_text())["state"] == "restored"


def test_resume_after_original_rename_and_sqlite_sidecars(tmp_path, monkeypatch):
    from camoufox_pm.core import update_checkpoint as module

    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    wal = tmp_path / "state.db-wal"
    wal.write_bytes(b"failed-wal")
    original_replace = module.os.replace
    interrupted = False

    def fail_after_rename(source, destination):
        nonlocal interrupted
        original_replace(source, destination)
        if source == db and not interrupted:
            interrupted = True
            raise OSError("simulated power loss")

    monkeypatch.setattr(module.os, "replace", fail_after_rename)
    with pytest.raises(OSError, match="power loss"):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    monkeypatch.setattr(module.os, "replace", original_replace)
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert not wal.exists()
    assert (tmp_path / "state.db.failed-checkpoint-wal").read_bytes() == b"failed-wal"
    assert (tmp_path / "state.db.failed-checkpoint").exists()


@pytest.mark.parametrize("boundary", range(1, 23))
@pytest.mark.parametrize("after", [False, True])
def test_resume_every_rename_and_journal_transition(tmp_path, monkeypatch, boundary, after):
    from camoufox_pm.core import update_checkpoint as module

    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    (paths["profiles"] / "cookies").write_bytes(b"failed-new-state")
    original_replace = module.os.replace
    count = 0

    def interrupted_replace(source, destination):
        nonlocal count
        count += 1
        if count == boundary and not after:
            raise OSError("interrupted")
        original_replace(source, destination)
        if count == boundary and after:
            raise OSError("interrupted")

    monkeypatch.setattr(module.os, "replace", interrupted_replace)
    try:
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    except OSError:
        pass
    monkeypatch.setattr(module.os, "replace", original_replace)
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert (paths["profiles"] / "cookies").read_bytes() == b"private-cookie"
    assert (tmp_path / "profiles.failed-checkpoint" / "cookies").read_bytes() == b"failed-new-state"


def test_disk_full_restore_preserves_original_and_can_resume(tmp_path, monkeypatch):
    from camoufox_pm.core import update_checkpoint as module

    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    original_copy = module._copy

    def full_disk(source, target, **kwargs):
        target.write_bytes(b"partial")
        raise OSError(28, "No space left")

    monkeypatch.setattr(module, "_copy", full_disk)
    with pytest.raises(OSError):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert (tmp_path / "state.db.failed-checkpoint").exists()
    monkeypatch.setattr(module, "_copy", original_copy)
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)


def test_interrupted_creation_never_restorable(tmp_path, monkeypatch):
    from camoufox_pm.core import update_checkpoint as module

    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left")

    monkeypatch.setattr(module, "_copy", full_disk)
    with pytest.raises(OSError):
        create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    with pytest.raises(CheckpointError, match="incomplete"):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert db.exists() and paths["key"].read_bytes() == b"private-key"


def test_restore_renames_only_siblings_and_copies_across_volumes(tmp_path, monkeypatch):
    from camoufox_pm.core import update_checkpoint as module

    db, paths = setup_state(tmp_path)
    root = tmp_path / "other-volume" / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    original_replace = module.os.replace

    def reject_cross_device_rename(source, target):
        assert source.parent == target.parent
        original_replace(source, target)

    monkeypatch.setattr(module.os, "replace", reject_cross_device_rename)
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)


def test_live_wal_database_checkpoint_is_self_contained(tmp_path):
    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    live = sqlite3.connect(db)
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("UPDATE settings SET value='in-wal'")
        live.commit()
        assert (tmp_path / "state.db-wal").exists()
        create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    finally:
        live.close()
    with closing(sqlite3.connect(root / "0")) as snapshot:
        assert snapshot.execute("SELECT value FROM settings").fetchone() == ("in-wal",)
    assert not (root / "0-wal").exists()


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX mode semantics")
def test_private_checkpoint_and_restore_symlink_rejected(tmp_path):
    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    root.chmod(0o755)
    from camoufox_pm.core.update_permissions import PermissionProtectionError

    with pytest.raises(PermissionProtectionError, match="Private"):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    root.chmod(0o700)
    paths["key"].unlink()
    paths["key"].symlink_to(db)
    with pytest.raises(CheckpointError, match="Symlinks"):
        restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX executable modes")
def test_browser_executable_round_trip_and_private_copy(tmp_path):
    db, paths = setup_state(tmp_path)
    browser = tmp_path / "browser"
    browser.mkdir()
    executable = browser / "camoufox"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    paths["browser"] = browser
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    executable.write_bytes(b"failed browser")
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert executable.read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert executable.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "browser.failed-checkpoint" / "camoufox").stat().st_mode & 0o777 == 0o700
    journal = json.loads((root / "journal.json").read_text())
    entry = next(entry for entry in journal["entries"] if entry["label"] == "browser")
    assert entry["inventory"]["camoufox"]["executable"] is True
    assert (root / entry["payload"] / "camoufox").stat().st_mode & 0o777 == 0o700


def test_regular_file_fsync_uses_writable_handles_for_windows(tmp_path, monkeypatch):
    import os
    import stat

    from camoufox_pm.core import update_checkpoint as module

    db, paths = setup_state(tmp_path)
    root = tmp_path / "checkpoint"
    original_fsync = module.os.fsync

    def writable_fsync(descriptor):
        # Windows _commit rejects read-only regular file descriptors. A zero-byte
        # write detects that same access error on POSIX without changing content.
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.write(descriptor, b"")
        original_fsync(descriptor)

    monkeypatch.setattr(module.os, "fsync", writable_fsync)
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)


@pytest.mark.skipif(__import__("os").name == "nt", reason="Firefox POSIX signature symlink")
@pytest.mark.parametrize("signature", ["127.0.0.1:+12345", "192.168.1.12:12345"])
def test_offline_profile_lock_not_followed_copied_or_deleted(tmp_path, monkeypatch, signature):
    from pathlib import Path

    db, paths = setup_state(tmp_path)
    profile = paths["profiles"] / "profile_rbazb7vc"
    profile.mkdir()
    (profile / "cookies.sqlite").write_bytes(b"session")
    lock = profile / "lock"
    lock.symlink_to(signature)
    original_open = Path.open

    def no_lock_open(path, *args, **kwargs):
        assert path.name != "lock", "Firefox lock symlink must never be followed"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_lock_open)
    root = tmp_path / "checkpoint"
    create_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert lock.is_symlink() and __import__("os").readlink(lock) == signature
    journal = json.loads((root / "journal.json").read_text())
    entry = next(entry for entry in journal["entries"] if entry["label"] == "profiles")
    assert "profile_rbazb7vc/lock" not in entry["inventory"]
    assert not (root / entry["payload"] / "profile_rbazb7vc" / "lock").is_symlink()
    (profile / "cookies.sqlite").write_bytes(b"new-session")
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)
    assert (profile / "cookies.sqlite").read_bytes() == b"session"
    assert not lock.is_symlink()
    retained_lock = tmp_path / "profiles.failed-checkpoint" / "profile_rbazb7vc" / "lock"
    assert retained_lock.is_symlink() and __import__("os").readlink(retained_lock) == signature
    restore_checkpoint(root, db_path=db, paths=paths, maintenance_check=lambda: True)


@pytest.mark.skipif(__import__("os").name == "nt", reason="Firefox POSIX signature symlink")
@pytest.mark.parametrize(
    "relative,signature",
    [
        ("profile_rbazb7vc/lock", "../../secret.key"),
        ("profile_rbazb7vc/lock", "256.0.0.1:+123"),
        ("profile_rbazb7vc/lock", "127.0.0.1:+0"),
        ("profile_rbazb7vc/lock", "localhost:+123"),
        ("profile_rbazb7vc/lock", "127.0.0.1:+123/../../secret.key"),
        ("profile_rbazb7vc/nested/lock", "127.0.0.1:+123"),
        ("profile_rbazb7vc/other", "127.0.0.1:+123"),
        ("other/lock", "127.0.0.1:+123"),
        ("lock", "127.0.0.1:+123"),
    ],
)
def test_profile_lock_exception_rejects_arbitrary_symlinks(tmp_path, relative, signature):
    db, paths = setup_state(tmp_path)
    lock = paths["profiles"] / relative
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.symlink_to(signature)
    with pytest.raises(CheckpointError, match="Unsupported"):
        create_checkpoint(
            tmp_path / "checkpoint", db_path=db, paths=paths, maintenance_check=lambda: True
        )
    assert lock.is_symlink()


@pytest.mark.skipif(__import__("os").name == "nt", reason="Firefox POSIX signature symlink")
def test_profile_lock_exception_never_bypasses_offline_gate(tmp_path):
    db, paths = setup_state(tmp_path)
    profile = paths["profiles"] / "profile_rbazb7vc"
    profile.mkdir()
    lock = profile / "lock"
    lock.symlink_to(f"127.0.0.1:+{__import__('os').getpid()}")
    with pytest.raises(CheckpointError, match="maintenance"):
        create_checkpoint(
            tmp_path / "checkpoint", db_path=db, paths=paths, maintenance_check=lambda: False
        )
    assert lock.is_symlink()
