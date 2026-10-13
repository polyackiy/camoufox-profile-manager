import json
import sqlite3

import pytest

from camoufox_pm.core import update_recovery as recovery
from camoufox_pm.core.maintenance import MaintenanceGate


@pytest.fixture
def layout(tmp_path, monkeypatch):
    database = tmp_path / "data" / "profiles.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE profiles (id TEXT, locked_by TEXT)")
        db.execute("INSERT INTO profiles VALUES ('profile', NULL)")
    profiles = database.parent / "profiles"
    profiles.mkdir()
    (profiles / "cookie").write_text("old-cookie")
    browser = tmp_path / "browser"
    browser.mkdir()
    (browser / "config.json").write_text('{"active":"old"}')
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
    with sqlite3.connect(database) as db:
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
