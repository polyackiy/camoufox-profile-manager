"""Interruption tests for the offline helper; no installed application is touched."""

import json
from pathlib import Path

import pytest

from camoufox_pm.core.update_helper import DirectoryReplacement, ReplacementError
from camoufox_pm.core.update_permissions import create_private_directory, protect_path


@pytest.fixture
def replacement(tmp_path):
    protect_path(tmp_path)
    root = tmp_path / "journal"
    create_private_directory(root)
    app = tmp_path / "app"
    app.mkdir()
    (app / "version").write_text("old")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "version").write_text("new")
    helper = DirectoryReplacement(root, app, offline=lambda: None)
    return helper, stage, app


def verify(path):
    assert (path / "version").read_text() == "new"


def test_accept_keeps_previous_and_requires_confirmation_for_later_restore(replacement):
    helper, stage, app = replacement
    helper.prepare(stage, verify_package=verify)
    helper.apply(verify_package=verify)
    helper.accept(validate=verify)
    assert (app / "version").read_text() == "new"
    assert len(list(app.parent.glob(".*-old"))) == 1
    with pytest.raises(ReplacementError, match="confirmation"):
        helper.rollback(restore_data=lambda: None)
    restored = []
    helper.rollback(restore_data=lambda: restored.append(True), allow_accepted=True)
    assert restored == [True]
    assert (app / "version").read_text() == "old"
    assert len(list(app.parent.glob(".*-failed"))) == 1


@pytest.mark.parametrize("boundary", ["moving_old", "old_retained", "moving_new", "validating"])
def test_interrupted_apply_recovers_complete_old_pair(replacement, monkeypatch, boundary):
    helper, stage, app = replacement
    helper.prepare(stage, verify_package=verify)
    write = helper._write

    def fail(record, state):
        write(record, state)
        if state == boundary:
            raise OSError("power loss")

    monkeypatch.setattr(helper, "_write", fail)
    with pytest.raises(OSError):
        helper.apply(verify_package=verify)
    monkeypatch.setattr(helper, "_write", write)
    helper.rollback(restore_data=lambda: None)
    assert (app / "version").read_text() == "old"
    assert json.loads(helper.journal.read_text())["state"] == "rolled_back"


@pytest.mark.parametrize("new_was_installed", [False, True])
def test_crash_after_old_package_restored_can_resume(replacement, monkeypatch, new_was_installed):
    helper, stage, app = replacement
    helper.prepare(stage, verify_package=verify)
    if new_was_installed:
        helper.apply(verify_package=verify)
    else:
        record = helper._read()
        _, old, _ = helper._paths(record)
        helper._write(record, "moving_old")
        app.rename(old)
    write = helper._write

    def fail(record, state):
        if state == "restoring_data":
            raise OSError("power loss after rename")
        write(record, state)

    monkeypatch.setattr(helper, "_write", fail)
    with pytest.raises(OSError):
        helper.rollback(restore_data=lambda: None)
    monkeypatch.setattr(helper, "_write", write)
    helper.rollback(restore_data=lambda: None)
    assert (app / "version").read_text() == "old"


def test_failed_data_restore_is_retried_without_replacing_old_app(replacement):
    helper, stage, app = replacement
    helper.prepare(stage, verify_package=verify)
    helper.apply(verify_package=verify)

    def fail():
        raise OSError("disk full")

    with pytest.raises(OSError):
        helper.rollback(restore_data=fail)
    assert (app / "version").read_text() == "old"
    helper.rollback(restore_data=lambda: None)


def test_verification_failure_never_replaces_application(replacement):
    helper, stage, app = replacement
    helper.prepare(stage, verify_package=verify)

    def reject(path: Path):
        raise ValueError("publisher rejected")

    with pytest.raises(ValueError):
        helper.apply(verify_package=reject)
    assert (app / "version").read_text() == "old"


def test_offline_lock_loss_prevents_replacement(replacement):
    helper, stage, app = replacement
    helper.prepare(stage, verify_package=verify)

    def lost():
        raise ReplacementError("writer still running")

    helper.offline = lost
    with pytest.raises(ReplacementError, match="writer"):
        helper.apply(verify_package=verify)
    assert (app / "version").read_text() == "old"


def test_standard_user_installation_parent_is_allowed(tmp_path):
    import os

    from camoufox_pm.core.update_helper import validate_installation_parent

    if os.name != "nt":
        tmp_path.chmod(0o755)
    assert validate_installation_parent(tmp_path) == tmp_path.resolve()


def test_world_writable_installation_parent_refused(tmp_path):
    import os

    from camoufox_pm.core.update_helper import validate_installation_parent

    if os.name == "nt":
        pytest.skip("POSIX mode boundary; Windows ACL checks are separate")
    tmp_path.chmod(0o777)
    with pytest.raises(ReplacementError, match="trusted"):
        validate_installation_parent(tmp_path)


def test_unprivileged_group_writable_parent_refused(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from camoufox_pm.core.update_helper import validate_installation_parent

    if os.name == "nt":
        pytest.skip("POSIX group boundary")
    import grp

    monkeypatch.setattr(grp, "getgrgid", lambda gid: SimpleNamespace(gr_name="staff"))
    tmp_path.chmod(0o775)
    with pytest.raises(ReplacementError, match="untrusted writable group"):
        validate_installation_parent(tmp_path)


def test_privileged_group_writable_parent_allowed(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from camoufox_pm.core.update_helper import validate_installation_parent

    if os.name == "nt":
        pytest.skip("POSIX group boundary")
    import grp

    monkeypatch.setattr(grp, "getgrgid", lambda gid: SimpleNamespace(gr_name="admin"))
    tmp_path.chmod(0o775)
    assert validate_installation_parent(tmp_path) == tmp_path.resolve()
