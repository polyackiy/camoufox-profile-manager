import json
import sqlite3
from contextlib import closing

import pytest

from camoufox_pm.core import update_recovery as recovery
from camoufox_pm.core.maintenance import MaintenanceGate


@pytest.fixture
def layout(tmp_path, monkeypatch):
    database = tmp_path / "data" / "profiles.db"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE profiles (id TEXT, locked_by TEXT)")
        db.execute("INSERT INTO profiles VALUES ('profile', NULL)")
    profiles = database.parent / "profiles"
    profiles.mkdir()
    (profiles / "cookie").write_text("old-cookie")
    browser = tmp_path / "browser"
    browser.mkdir()
    (browser / "config.json").write_text('{"active_version":"old"}')
    paths = {"profiles": profiles, "browser": browser, "secret_key": database.parent / "secret.key"}
    gate = MaintenanceGate(database.parent / "update-control" / "maintenance.sqlite3")
    monkeypatch.setattr(recovery, "configured_gate", lambda: gate)
    monkeypatch.setattr(recovery, "_legacy_processes_stopped", lambda: True)
    monkeypatch.setattr(
        recovery,
        "configured_layout",
        lambda **kwargs: (database, paths, ("profiles", "browser"), "reference"),
    )
    return database, paths, gate


def test_complete_offline_recovery_after_missing_live_database_and_browser(layout):
    database, paths, gate = layout
    checkpoint = recovery.offline_checkpoint()
    root = gate.path.parent / "checkpoints" / checkpoint
    timestamp = json.loads((root / "journal.json").read_text())["created_at"]
    database.rename(database.with_suffix(".failed"))
    paths["browser"].rename(paths["browser"].with_name("browser.failed"))
    (paths["profiles"] / "cookie").write_text("new-cookie")
    recovery.offline_restore(checkpoint, timestamp)
    assert database.exists()
    assert (paths["browser"] / "config.json").exists()
    assert (paths["profiles"] / "cookie").read_text() == "old-cookie"
    assert not gate.blocked


def test_checkpoint_refuses_orphan_browser_lease(layout):
    database, _, gate = layout
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE profiles SET locked_by='dead-manager'")
    with pytest.raises(RuntimeError, match="Close all"):
        recovery.offline_checkpoint()
    assert not gate.blocked


def test_restore_requires_exact_loss_acknowledgement(layout):
    _, _, gate = layout
    checkpoint = recovery.offline_checkpoint()
    with pytest.raises(ValueError, match="timestamp"):
        recovery.offline_restore(checkpoint, "wrong")
    assert not gate.blocked


def test_checkpoint_records_versions_and_refuses_wrong_manager(layout, monkeypatch):
    _, _, gate = layout
    checkpoint = recovery.offline_checkpoint()
    root = gate.path.parent / "checkpoints" / checkpoint
    metadata = json.loads((root / "key-reference.json").read_text())
    assert metadata["manager_version"] == recovery.__version__
    assert metadata["wrapper_version"]
    assert metadata["browser_active"] == "old"
    timestamp = json.loads((root / "journal.json").read_text())["created_at"]
    monkeypatch.setattr(recovery, "__version__", "999.0.0")
    with pytest.raises(ValueError, match="matching manager"):
        recovery.offline_restore(checkpoint, timestamp)
    assert not gate.blocked


def test_desktop_recovery_never_bootstraps_or_generates_key(tmp_path, monkeypatch, capsys):
    import sys

    from camoufox_pm import cli, config

    gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
    monkeypatch.setattr(cli, "configured_gate", lambda: gate)
    monkeypatch.setattr(config, "desktop_data_dir", lambda: tmp_path / "desktop")

    def forbidden_bootstrap():
        raise AssertionError("Recovery must never create a replacement key")

    monkeypatch.setattr(config, "bootstrap_desktop", forbidden_bootstrap)
    monkeypatch.setattr(sys, "argv", ["camoufox-pm", "--desktop", "update-recovery", "status"])
    cli.main()
    assert "idle" in capsys.readouterr().out
    assert not (tmp_path / "desktop" / "secret.key").exists()
    config.get_settings.cache_clear()


def test_legacy_scan_allows_known_uv_recovery_ancestor(monkeypatch):
    import os
    from types import SimpleNamespace

    import psutil

    launcher = SimpleNamespace(
        pid=123,
        name=lambda: "uv",
        cmdline=lambda: ["uv", "run", "camoufox-pm", "update-recovery", "checkpoint"],
        username=lambda: "user",
    )
    current = SimpleNamespace(pid=os.getpid(), username=lambda: "user", parents=lambda: [launcher])
    monkeypatch.setattr(psutil, "Process", lambda: current)
    monkeypatch.setattr(psutil, "process_iter", lambda: [launcher])
    assert recovery._legacy_processes_stopped()
    launcher.name = lambda: "Camoufox Profile Manager"
    assert not recovery._legacy_processes_stopped()


def test_restore_local_key_still_rejects_changed_external_override(layout, monkeypatch):
    from types import SimpleNamespace

    _, paths, gate = layout
    paths["secret_key"].write_text("retained-local-key")
    checkpoint = recovery.offline_checkpoint()
    root = gate.path.parent / "checkpoints" / checkpoint
    timestamp = json.loads((root / "journal.json").read_text())["created_at"]
    monkeypatch.setattr(
        recovery, "get_settings", lambda: SimpleNamespace(secret_key="changed-external-key")
    )
    monkeypatch.setattr(
        recovery, "configured_layout", lambda **kwargs: (None, paths, (), "changed-reference")
    )
    with pytest.raises(ValueError, match="original key"):
        recovery.offline_restore(checkpoint, timestamp)
    assert not gate.blocked


@pytest.mark.parametrize("source", ["profile", "inventory"])
def test_encrypted_credential_validation_requires_actual_original_key(tmp_path, source):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    token = "enc:" + Fernet(key).encrypt(b"private-password").decode()
    database = tmp_path / "encrypted.db"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE profiles (proxy_config TEXT)")
        db.execute("CREATE TABLE proxy_inventory (password TEXT)")
        if source == "profile":
            db.execute("INSERT INTO profiles VALUES (?)", (json.dumps({"password": token}),))
        else:
            db.execute("INSERT INTO proxy_inventory VALUES (?)", (token,))
    with pytest.raises(ValueError, match="original encryption key"):
        recovery._validate_encrypted_credentials(database, "")
    with pytest.raises(ValueError, match="cannot read"):
        recovery._validate_encrypted_credentials(database, Fernet.generate_key().decode())
    recovery._validate_encrypted_credentials(database, key.decode())


def test_legacy_scan_ignores_unrelated_protected_process_but_not_candidate(monkeypatch):
    import os
    from types import SimpleNamespace

    import psutil

    def denied():
        raise psutil.AccessDenied(123)

    process = SimpleNamespace(pid=123, name=lambda: "csrss.exe", username=denied, cmdline=denied)
    current = SimpleNamespace(pid=os.getpid(), username=lambda: "user", parents=lambda: [])
    monkeypatch.setattr(psutil, "Process", lambda: current)
    monkeypatch.setattr(psutil, "process_iter", lambda: [process])
    assert recovery._legacy_processes_stopped()
    process.name = lambda: "camoufox.exe"
    assert not recovery._legacy_processes_stopped()
