"""Certificate-independent transaction proof using an actual installed process."""

import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.update_permissions import protect_path
from camoufox_pm.core.update_service import UpdateService
from camoufox_pm.core.update_transaction import UpdateTransaction


@pytest.fixture(autouse=True)
def portable_fake_launcher(monkeypatch):
    # Production uses a signed native PE/Mach-O entrypoint. The fixture uses a
    # controlled Python script; Windows cannot execute its POSIX shebang. Keep
    # the actual subprocess and receipt/data exchange, substituting only the
    # test executable format on Windows.
    original = subprocess.run

    def run(command, *args, **kwargs):
        if os.name == "nt" and Path(command[0]).name == "manager":
            command = [sys.executable, *command]
        return original(command, *args, **kwargs)

    monkeypatch.setattr("camoufox_pm.core.update_transaction.subprocess.run", run)


def transaction(tmp_path, *, fail=False):
    protect_path(tmp_path)
    installation = tmp_path / "app"
    installation.mkdir(mode=0o700)
    (installation / "release").write_text("old")
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    executable = stage / "manager"
    executable.write_text(
        """#!/usr/bin/env python3
import json,sys,sqlite3
from contextlib import closing
from pathlib import Path
p=Path(sys.argv[2]); r=json.loads(p.read_text())
t=json.loads((p.parent/'transaction.json').read_text())
with closing(sqlite3.connect(t['db_path'])) as db, db:
    db.execute("INSERT INTO records VALUES ('migrated')")
"""
        + (
            "sys.exit(9)\n"
            if fail
            else "p.write_text(json.dumps({'validated':r['transaction_id']}))\n"
        )
    )
    executable.chmod(0o700)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()

    def verify(path):
        assert hashlib.sha256((path / "manager").read_bytes()).hexdigest() == digest

    database = tmp_path / "data.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE records(value TEXT)")
        db.execute("INSERT INTO records VALUES ('original')")
    profiles = tmp_path / "profiles"
    profiles.mkdir(mode=0o700)
    (profiles / "profile").write_text("original")
    state = tmp_path / "control"
    gate = MaintenanceGate(state / "maintenance.sqlite3")
    service = UpdateService(state, gate)
    engine = UpdateTransaction(
        service, installation, "manager", verify_tree=verify, offline_check=lambda: True
    )
    package = SimpleNamespace(
        path=stage,
        manifest={"version": "2", "browser_version": "135-1"},
        verify_tree=lambda path=stage: verify(path),
    )
    engine.prepare(
        package,
        db_path=database,
        paths={"profiles": profiles},
        current_version="1",
        required_paths=("profiles",),
    )
    return engine, database, installation


def values(database):
    with closing(sqlite3.connect(database)) as db, db:
        return [row[0] for row in db.execute("SELECT value FROM records")]


def test_actual_new_process_acceptance_and_informed_pair_rollback(tmp_path):
    engine, database, installation = transaction(tmp_path)
    result = engine.run()
    assert result["state"] == "committed"
    assert values(database) == ["original", "migrated"]
    assert not engine.service.gate.blocked
    with pytest.raises(ValueError, match="timestamp"):
        engine.rollback("wrong")
    engine.rollback(result["checkpoint_timestamp"])
    assert values(database) == ["original"]
    assert (installation / "release").read_text() == "old"


def test_actual_failed_process_restores_binary_and_data(tmp_path):
    engine, database, installation = transaction(tmp_path, fail=True)
    assert engine.run()["state"] == "rolled_back"
    assert values(database) == ["original"]
    assert (installation / "release").read_text() == "old"
    assert not engine.service.gate.blocked


@pytest.mark.parametrize(
    "boundary", ["prepared", "moving_old", "old_retained", "moving_new", "validating"]
)
def test_each_swap_journal_boundary_resumes(tmp_path, boundary):
    engine, database, installation = transaction(tmp_path)
    replacement = engine._replacement()
    original = replacement._write

    def crash(record, state):
        original(record, state)
        if state == boundary:
            raise KeyboardInterrupt("simulated power loss")

    replacement._write = crash
    with pytest.raises(KeyboardInterrupt):
        replacement.prepare(
            Path(engine.service.status()["package"]),
            verify_package=engine.verify_tree,
            transaction_id=engine.service.status()["transaction_id"],
        )
        replacement.apply(verify_package=engine.verify_tree)
    assert engine.run()["state"] == "committed"
    assert values(database) == ["original", "migrated"]


def test_browser_mutation_cannot_cancel_and_restores_before_binary_swap(tmp_path):
    from camoufox_pm.core.maintenance import MaintenanceBlocked

    engine, database, installation = transaction(tmp_path)
    engine.begin_browser_install()
    (tmp_path / "profiles" / "profile").write_text("new browser state")
    with pytest.raises(MaintenanceBlocked, match="Browser installation"):
        engine.service.cancel_preparation()
    with pytest.raises(MaintenanceBlocked, match="browser installation"):
        engine.run()
    result = engine.rollback(engine.service.status()["checkpoint_timestamp"])
    assert result["state"] == "rolled_back"
    assert (tmp_path / "profiles" / "profile").read_text() == "original"
    assert (installation / "release").read_text() == "old"
    assert not engine.service.gate.blocked


@pytest.mark.parametrize(
    "boundary", ["rolling_back", "restoring_old", "restoring_data", "rolled_back"]
)
def test_each_rollback_journal_boundary_resumes(tmp_path, boundary):
    engine, database, installation = transaction(tmp_path)
    result = engine.run()
    engine.service.gate.acquire(result["transaction_id"])
    replacement = engine._replacement()
    original = replacement._write

    def crash(record, state):
        original(record, state)
        if state == boundary:
            raise KeyboardInterrupt("simulated power loss")

    replacement._write = crash
    with pytest.raises(KeyboardInterrupt):
        replacement.rollback(restore_data=engine._restore, allow_accepted=True)
    assert engine.run()["state"] == "rolled_back"
    assert values(database) == ["original"]
    assert (installation / "release").read_text() == "old"
    assert not engine.service.gate.blocked


def test_terminal_success_resumes_without_revalidating_or_releasing_another_owner(tmp_path):
    engine, database, installation = transaction(tmp_path)
    result = engine.run()
    assert engine.run() == result
    assert values(database) == ["original", "migrated"]


def test_browser_mutation_with_prepared_swap_restores_complete_checkpoint(tmp_path):
    engine, database, installation = transaction(tmp_path)
    engine.begin_browser_install()
    (tmp_path / "profiles" / "profile").write_text("new browser state")
    engine.browser_installed()
    engine._replacement().prepare(
        Path(engine.service.status()["package"]),
        verify_package=engine.verify_tree,
        transaction_id=engine.service.status()["transaction_id"],
    )
    engine.rollback(engine.service.status()["checkpoint_timestamp"])
    assert (tmp_path / "profiles" / "profile").read_text() == "original"
    assert (installation / "release").read_text() == "old"


def test_retained_binary_tamper_blocks_rollback(tmp_path):
    from camoufox_pm.core.update_helper import ReplacementError

    engine, database, installation = transaction(tmp_path)
    result = engine.run()
    replacement = engine._replacement()
    _, old, _ = replacement._paths(replacement._read())
    (old / "release").write_text("tampered")
    with pytest.raises(ReplacementError, match="Retained application changed"):
        engine.rollback(result["checkpoint_timestamp"])
    assert engine.service.gate.blocked
    assert (installation / "manager").is_file()


def test_rollback_invalidates_sync_before_database_restore(tmp_path, monkeypatch):
    from camoufox_pm.core import update_transaction
    from camoufox_pm.sync.control import ControlStore

    engine, database, _ = transaction(tmp_path)
    result = engine.run()
    control = ControlStore(database.parent / "sync-control")
    control.create(
        "profile/local",
        {
            "phase": "held",
            "installed_generation": 7,
            "checkpoint": {"revision_id": "kept-high-water"},
        },
    )
    actual_restore = update_transaction.restore_checkpoint

    def guarded_restore(*args, **kwargs):
        binding = control.get("profile/local").payload
        assert binding["phase"] == "unknown"
        assert binding["installed_generation"] is None
        assert binding["checkpoint"]["revision_id"] == "kept-high-water"
        return actual_restore(*args, **kwargs)

    monkeypatch.setattr(update_transaction, "restore_checkpoint", guarded_restore)
    engine.rollback(result["checkpoint_timestamp"])
    assert values(database) == ["original"]
