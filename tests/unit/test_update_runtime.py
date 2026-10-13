"""Runtime control requests cannot supply executable or trust capabilities."""

import json
from pathlib import Path

import pytest

from camoufox_pm.core import update_runtime as runtime
from camoufox_pm.core.maintenance import MaintenanceBlocked, MaintenanceGate
from camoufox_pm.core.update_service import UpdateService


@pytest.fixture
def local(monkeypatch, tmp_path):
    service = UpdateService(tmp_path, MaintenanceGate(tmp_path / "gate.sqlite3"))
    monkeypatch.setattr(runtime, "service", lambda: service)
    return service


def test_default_runtime_fails_closed(local, monkeypatch):
    monkeypatch.delenv("CPM_UPDATE_POLICY", raising=False)
    value = runtime.status()
    assert not value["automatic_install_available"]
    assert not value["can_download"]
    assert "profiles.db" not in json.dumps(value)


def test_action_requires_durable_id_and_checkpoint_confirmation(local, monkeypatch):
    monkeypatch.setattr(runtime, "deployment", lambda: ({}, None))
    runtime._save({"transaction_id": "stage", "state": "staged"})
    with pytest.raises(ValueError):
        runtime.request("apply", "other")
    value = runtime.request("apply", "stage")
    assert value["state"] == "handoff_requested"
    assert runtime._record()["action"] == "apply"


def test_unknown_channel_never_reads_trust(local, monkeypatch):
    with pytest.raises(ValueError):
        runtime.download("../../root")


def test_resume_and_rollback_refuse_idle(local, monkeypatch):
    monkeypatch.setattr(runtime, "deployment", lambda: ({}, None))
    for action in ("resume", "rollback"):
        with pytest.raises(MaintenanceBlocked):
            runtime.request(action, None)


def test_api_rejects_path_and_policy_injection():
    from pydantic import ValidationError

    from camoufox_pm.api.routes.system import UpdateActionRequest, UpdateDownloadRequest

    with pytest.raises(ValidationError):
        UpdateDownloadRequest(channel="stable", trusted_root="/tmp/attacker")
    with pytest.raises(ValidationError):
        UpdateActionRequest(transaction_id="stage", command="evil")


@pytest.mark.parametrize("native,expected", [("darwin", "darwin"), ("win32", "windows")])
def test_target_platform_contract(monkeypatch, native, expected):
    monkeypatch.setattr(runtime.sys, "platform", native)
    monkeypatch.setattr(runtime.platform, "machine", lambda: "AMD64")
    assert runtime._target_platform() == expected
    assert runtime._architecture() == "x86_64"


def test_rollback_factory_does_not_require_new_target_metadata(local, monkeypatch, tmp_path):
    from camoufox_pm.core import update_recovery
    from camoufox_pm.core.update_publisher import PublisherPolicy

    monkeypatch.setattr(
        runtime,
        "deployment",
        lambda: (
            {"installation": str(tmp_path / "installed"), "entrypoint": "manager"},
            PublisherPolicy("release", "product"),
        ),
    )
    monkeypatch.setattr(
        update_recovery,
        "configured_layout",
        lambda **kw: (tmp_path / "missing.db", {}, (), "key-reference"),
    )

    def no_download(*args, **kwargs):
        raise AssertionError("Rollback must not require expired NEW target metadata")

    monkeypatch.setattr(runtime, "_package", no_download)
    monkeypatch.setattr(runtime, "_browser_package", no_download)
    transaction = runtime.configured_transaction(for_rollback=True)
    assert transaction.installation == tmp_path / "installed"


def test_missing_installation_reopens_retained_helper_recovery(local, monkeypatch, tmp_path):
    import subprocess

    from camoufox_pm.core.update_publisher import PublisherPolicy

    helper = local.root / "helpers" / "retained"
    helper.mkdir(parents=True)
    executable = helper / "manager"
    executable.write_text("retained signed helper")
    monkeypatch.setattr(runtime.sys, "executable", str(executable))
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(
        runtime,
        "deployment",
        lambda: (
            {"installation": str(tmp_path / "missing-install"), "entrypoint": "manager"},
            PublisherPolicy("release", "product"),
        ),
    )
    checked = []
    monkeypatch.setattr(
        runtime, "verify_publisher", lambda root, manifest, policy: checked.append(root)
    )
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: launched.append(args))
    runtime.reopen_desktop()
    assert checked == [helper]
    assert launched == [[str(executable), "--update-recovery-window"]]


def test_handoff_retains_original_environment_checkpoint_path(local, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from camoufox_pm.core import update_recovery

    original = tmp_path / "original-config"
    helper = tmp_path / "helper-copy"
    original.mkdir()
    helper.mkdir()
    (original / ".env").write_text("CPM_DB_PATH=original/profiles.db\n")
    (helper / ".env").write_text("CPM_DB_PATH=wrong/profiles.db\n")
    monkeypatch.chdir(original)
    monkeypatch.setattr(runtime, "deployment", lambda: ({}, None))
    package = SimpleNamespace(
        path=tmp_path / "package", manifest={"version": "9.0", "browser_version": "156.0.1b36"}
    )
    target = SimpleNamespace(path=tmp_path / "archive")
    monkeypatch.setattr(runtime, "_package", lambda record: (target, package))
    monkeypatch.setattr(runtime, "_browser_package", lambda record, version: (target, package))
    runtime.download("stable")
    record = runtime._record()
    monkeypatch.chdir(helper)
    monkeypatch.setattr(
        update_recovery,
        "get_settings",
        lambda: SimpleNamespace(db_path=str(tmp_path / "profiles.db"), secret_key=None),
    )
    _, paths, required, _ = update_recovery.configured_layout(
        require_existing=False, configuration_directory=Path(record["configuration_directory"])
    )
    assert paths["environment"] == original / ".env"
    assert "environment" in required


def test_partial_checkpoint_restore_retains_retryable_rollback(local, monkeypatch):
    monkeypatch.setattr(runtime, "deployment", lambda: ({}, None))
    local._save(
        {"state": "restoring_data", "transaction_id": "checkpoint", "checkpoint_timestamp": "exact"}
    )
    local.gate.acquire("checkpoint")
    runtime._save(
        {
            "state": "handoff_requested",
            "transaction_id": "checkpoint",
            "action": "rollback",
            "confirm_timestamp": "exact",
            "error": "interrupted",
        }
    )
    assert runtime.status()["can_resume"]
    assert runtime.status()["can_rollback"]
    assert not runtime.status()["can_cancel"]
    runtime.request("resume", "checkpoint")
    assert runtime._record()["action"] == "rollback"
    assert runtime._record()["confirm_timestamp"] == "exact"
    assert "error" not in runtime._record()
