"""Locally provisioned deployment capabilities and durable desktop update requests.

HTTP callers can choose a channel or action, never trust roots or executable paths.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import sys
import uuid
from dataclasses import replace
from pathlib import Path

from camoufox_pm import __version__

from .maintenance import MaintenanceBlocked
from .update_package import extract_verified_package
from .update_permissions import create_private_directory, create_private_file, verify_private_path
from .update_publisher import PublisherPolicy, verify_publisher
from .update_service import UpdateService
from .update_targets import UpdatePolicy, UpdateVerificationError, VerifiedTargetClient


def service():
    from .maintenance import configured_gate

    gate = configured_gate()
    return UpdateService(gate.path.parent, gate)


def deployment():
    filename = os.environ.get("CPM_UPDATE_POLICY")
    if not filename or not getattr(sys, "frozen", False) or sys.platform not in ("darwin", "win32"):
        raise UpdateVerificationError(
            "Automatic updates require a provisioned signed desktop installation"
        )
    path = Path(filename).absolute()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o022):
        raise UpdateVerificationError("Unsafe local update policy")
    if hasattr(os, "getuid") and info.st_uid not in (0, os.getuid()):
        raise UpdateVerificationError("Unsafe local update policy owner")
    if os.name == "nt":
        verify_private_path(path)
    value = json.loads(path.read_text())
    required = {
        "installation",
        "entrypoint",
        "trusted_root",
        "metadata_url",
        "targets_url",
        "channels",
        "publisher",
        "schema",
        "updater_version",
        "browser",
    }
    if set(value) != required or set(value["channels"]) != {"stable", "preview"}:
        raise UpdateVerificationError("Incomplete trusted deployment policy")
    from .update_helper import validate_installation_parent

    installation = Path(value["installation"]).absolute()
    validate_installation_parent(installation.parent)
    if not (
        Path(sys.executable).resolve().is_relative_to(installation)
        or (
            ("--update-helper" in sys.argv or "--update-recovery-window" in sys.argv)
            and Path(sys.executable).resolve().is_relative_to(service().root / "helpers")
        )
    ):
        raise UpdateVerificationError("Policy does not describe this installed desktop")
    publisher = PublisherPolicy(**value["publisher"])
    if (sys.platform == "darwin" and not publisher.macos_team_id) or (
        sys.platform == "win32" and not publisher.windows_thumbprints
    ):
        raise UpdateVerificationError("No trusted platform publisher is configured")
    if not Path(value["trusted_root"]).is_file():
        raise UpdateVerificationError("No trusted TUF root is configured")
    return value, publisher


def _record():
    path = service().root / "request.json"
    return json.loads(path.read_text()) if path.exists() else None


def _save(record):
    root = service().root
    temporary = root / f"request-{uuid.uuid4().hex}.tmp"
    with os.fdopen(create_private_file(temporary), "w") as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, root / "request.json")


def _target_platform():
    return {"darwin": "darwin", "win32": "windows"}[sys.platform]


def _architecture():
    machine = platform.machine().lower()
    value = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine
    )
    if value is None:
        raise UpdateVerificationError("Unsupported desktop architecture")
    return value


def _policy(config, publisher, channel):
    return UpdatePolicy(
        "manager",
        __version__,
        _target_platform(),
        _architecture(),
        channel,
        config["schema"],
        config["updater_version"],
        "0",
        publisher.identifier,
        publisher.product_id,
        platform.mac_ver()[0] if sys.platform == "darwin" else platform.version(),
    )


def _package(record, *, offline=False):
    config, publisher = deployment()
    client = VerifiedTargetClient(
        service().root / "metadata",
        service().root / "downloads",
        config["metadata_url"],
        config["targets_url"],
        Path(config["trusted_root"]),
    )
    target = client.download(
        config["channels"][record["channel"]],
        _policy(config, publisher, record["channel"]),
        discover=True,
        offline=offline,
        cached_target=Path(record["archive"]) if offline else None,
    )
    staging = service().root / "packages"
    if not staging.exists():
        create_private_directory(staging)
    package = extract_verified_package(target, staging)
    verify_publisher(package.path, package.manifest, publisher)
    return target, package


def status():
    srv = service()
    current = srv.status()
    record = _record()
    disabled = None
    try:
        deployment()
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        disabled = (
            str(exc)
            if isinstance(exc, UpdateVerificationError)
            else "Trusted deployment policy is unavailable or invalid"
        )
    value = {
        key: current[key]
        for key in (
            "state",
            "transaction_id",
            "created_at",
            "target_version",
            "browser_version",
            "checkpoint_timestamp",
        )
        if key in current
    }
    if (
        record
        and record.get("state") in ("staged", "handoff_requested", "downloading", "failed")
        and current["state"] in ("idle", "cancelled", "committed", "rolled_back")
    ):
        value.update(
            {
                key: record[key]
                for key in ("state", "transaction_id", "target_version", "browser_version")
                if key in record
            }
        )
    value.update(
        maintenance_active=srv.gate.blocked,
        automatic_install_available=disabled is None,
        disabled_reason=disabled,
        installed_version=__version__,
        rollback_transaction_id=current.get("transaction_id"),
        can_download=disabled is None and not srv.gate.blocked and not (record or {}).get("action"),
        can_apply=disabled is None and value["state"] == "staged",
        can_rollback=disabled is None
        and current["state"]
        in (
            "committed",
            "prepared",
            "installing_browser",
            "browser_ready",
            "rolling_back",
            "restoring_data",
            "replacing",
        )
        and bool(current.get("checkpoint_timestamp")),
        can_cancel=current["state"] in ("preparing", "prepared", "checkpoint_interrupted")
        and not current.get("browser_install_started"),
        can_resume=disabled is None
        and (
            current["state"]
            in (
                "prepared",
                "installing_browser",
                "browser_ready",
                "replacing",
                "rolling_back",
                "restoring_data",
            )
            or (
                (record or {}).get("action") == "apply"
                and current["state"] in ("idle", "cancelled", "committed", "rolled_back")
            )
        ),
        request_pending=bool((record or {}).get("action")),
        phase=value["state"],
        error=(record or {}).get("error"),
    )
    return value


def download(channel):
    if channel not in ("stable", "preview"):
        raise ValueError("Unsupported update channel")
    srv = service()
    with srv._control():
        if srv.gate.blocked or (_record() or {}).get("action"):
            raise MaintenanceBlocked("Resolve the pending update first")
        record = {
            "channel": channel,
            "transaction_id": uuid.uuid4().hex,
            "state": "downloading",
            "configuration_directory": str(Path.cwd().resolve()),
        }
        _save(record)
        try:
            target, package = _package(record)
            browser_target, browser_package = _browser_package(
                record, package.manifest["browser_version"]
            )
            record.update(
                browser_archive=str(browser_target.path), browser_package=str(browser_package.path)
            )
            record.update(
                state="staged",
                archive=str(target.path),
                package=str(package.path),
                target_version=package.manifest["version"],
                browser_version=package.manifest["browser_version"],
            )
            _save(record)
        except Exception:
            record.update(state="failed", error="Update verification failed")
            _save(record)
            raise
    return status()


def request(action, transaction_id, confirm_timestamp=None):
    deployment()
    srv = service()
    with srv._control():
        record = _record() or {}
        current = srv.status()
        pending_apply = record.get("action") == "apply" and current["state"] in (
            "idle",
            "cancelled",
            "committed",
            "rolled_back",
        )
        expected = (
            record.get("transaction_id")
            if action == "apply" or (action == "resume" and pending_apply)
            else current.get("transaction_id")
        )
        if transaction_id != expected or action not in ("apply", "resume", "rollback"):
            raise ValueError("Invalid update request")
        if action == "apply" and (
            record.get("state") != "staged"
            or srv.gate.blocked
            or current["state"] not in ("idle", "cancelled", "committed", "rolled_back")
        ):
            raise MaintenanceBlocked("No verified staged update is available")
        if (
            action == "resume"
            and not pending_apply
            and current["state"]
            not in (
                "prepared",
                "installing_browser",
                "browser_ready",
                "replacing",
                "rolling_back",
                "restoring_data",
            )
        ):
            raise MaintenanceBlocked("This update cannot be resumed")
        if action == "rollback" and current["state"] not in (
            "committed",
            "prepared",
            "installing_browser",
            "browser_ready",
            "replacing",
            "rolling_back",
            "restoring_data",
        ):
            raise MaintenanceBlocked("No recoverable checkpoint is available")
        if action == "rollback" and (
            not confirm_timestamp or confirm_timestamp != current.get("checkpoint_timestamp")
        ):
            raise ValueError("Confirm the exact checkpoint timestamp")
        if action == "resume" and record.get("action") == "rollback":
            action = "rollback"
            confirm_timestamp = record.get("confirm_timestamp")
        elif action == "resume" and pending_apply:
            action = "apply"
        if action == "rollback":
            record["transaction_id"] = current["transaction_id"]
        record.pop("error", None)
        record.update(action=action, confirm_timestamp=confirm_timestamp, state="handoff_requested")
        _save(record)
    return status()


def configured_transaction(*, for_rollback=False):
    from .update_recovery import _legacy_processes_stopped, _profiles_stopped, configured_layout
    from .update_transaction import UpdateTransaction

    config, publisher = deployment()
    record = _record()
    package = None
    if not for_rollback:
        if not record or not record.get("archive"):
            raise UpdateVerificationError("No authenticated update archive is retained")
        _, package = _package(record, offline=True)

    def verify_tree(path):
        if package is not None:
            package.verify_tree(path)
            manifest = package.manifest
        else:
            manifest = {
                "publisher_policy": publisher.identifier,
                "product_id": publisher.product_id,
                "platform": _target_platform(),
                "architecture": _architecture(),
            }
        verify_publisher(path, manifest, publisher)

    def verify_previous(path):
        manifest = {
            "publisher_policy": publisher.identifier,
            "product_id": publisher.product_id,
            "platform": _target_platform(),
            "architecture": _architecture(),
        }
        verify_publisher(path, manifest, publisher)

    srv = service()
    database, _, _, _ = configured_layout(
        require_existing=False,
        configuration_directory=Path(record["configuration_directory"])
        if record and record.get("configuration_directory")
        else None,
    )
    transaction = UpdateTransaction(
        srv,
        Path(config["installation"]),
        config["entrypoint"],
        verify_tree=verify_tree,
        verify_previous=verify_previous,
        offline_check=lambda: (
            srv.gate.managers_stopped()
            and _legacy_processes_stopped()
            and (not database.exists() or _profiles_stopped(database))
        ),
    )
    if package is not None:
        transaction.staged_package = package
        transaction.browser_target, transaction.browser_package = _browser_package(
            record, package.manifest["browser_version"], offline=True
        )
    return transaction


def _execute_request(action=None, confirmed_timestamp=None):
    from .update_recovery import configured_layout

    record = _record()
    if action is not None and record.get("action") != action:
        raise ValueError("Helper action does not match durable request")
    transaction = configured_transaction(for_rollback=record["action"] == "rollback")
    if record["action"] == "apply" and transaction.service.status()["state"] in (
        "idle",
        "cancelled",
        "committed",
        "rolled_back",
    ):
        database, paths, required, _ = configured_layout(
            configuration_directory=Path(record["configuration_directory"])
            if record.get("configuration_directory")
            else None
        )
        transaction.prepare(
            transaction.staged_package,
            db_path=database,
            paths=paths,
            required_paths=required,
            current_version=__version__,
        )
    if record["action"] in ("apply", "resume") and transaction.service.status()["state"] in (
        "prepared",
        "installing_browser",
    ):
        paths = {key: Path(value) for key, value in transaction.service.status()["paths"].items()}
        from .update_browser_pair import install_paired_browser

        transaction.begin_browser_install()
        paired_path = install_paired_browser(
            transaction.browser_target, paths["browser"], offline_check=transaction.offline_check
        )
        transaction.browser_installed(paired_path)
    result = (
        transaction.rollback(record["confirm_timestamp"])
        if record["action"] == "rollback"
        else transaction.run()
    )
    record.update(state=result["state"], transaction_id=result["transaction_id"])
    record.pop("action", None)
    _save(record)
    return result


def spawn_helper():
    """Copy all loaded executable dependencies outside replacement before quitting."""
    import hashlib
    import shutil
    import subprocess

    import psutil

    config, publisher = deployment()
    installation = Path(config["installation"])
    destination = service().root / "helpers" / uuid.uuid4().hex
    destination.parent.mkdir(mode=0o700, exist_ok=True)

    def inventory(root):
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    if not installation.exists():
        helper_executable = Path(sys.executable).resolve()
        if not helper_executable.is_relative_to(service().root / "helpers"):
            raise UpdateVerificationError(
                "A retained helper is required for interrupted replacement"
            )
        destination = helper_executable
        for _ in Path(config["entrypoint"]).parts:
            destination = destination.parent
    else:
        before = inventory(installation)
        shutil.copytree(installation, destination, symlinks=True)
        if before != inventory(destination) or before != inventory(installation):
            raise UpdateVerificationError("Offline helper copy changed during handoff")
    record = _record()
    # Verify native trust independently of a future manager version's manifest.
    manifest = {
        "publisher_policy": publisher.identifier,
        "product_id": publisher.product_id,
        "platform": _target_platform(),
        "architecture": _architecture(),
    }
    verify_publisher(destination, manifest, publisher)
    action = record["action"]
    args = [
        str(destination / config["entrypoint"]),
        "--update-helper",
        action,
        "--wait-pid",
        str(os.getpid()),
        "--wait-created",
        str(psutil.Process().create_time()),
    ]
    if action == "rollback":
        args += ["--confirm-timestamp", record["confirm_timestamp"]]
    from camoufox_pm.config import get_settings

    settings = get_settings()
    env = dict(os.environ)
    for field, value in settings.model_dump().items():
        if value is not None:
            env["CPM_" + field.upper()] = ",".join(value) if isinstance(value, list) else str(value)
    if settings.webui_dir:
        env["CPM_WEBUI_DIR"] = str(Path(settings.webui_dir).resolve())
    env["CPM_DB_PATH"] = str(Path(settings.db_path).resolve())
    env["CPM_UPDATE_POLICY"] = str(Path(env["CPM_UPDATE_POLICY"]).resolve())
    if settings.secret_key:
        env["CPM_SECRET_KEY"] = settings.secret_key
    subprocess.Popen(args, cwd=destination, env=env, close_fds=True)


def _browser_package(record, browser_version, *, offline=False):
    from .update_package import extract_verified_browser_archive

    config, publisher = deployment()
    browser = config["browser"]
    if set(browser) != {"metadata_url", "targets_url", "trusted_root", "artifact_template"}:
        raise UpdateVerificationError("Incomplete authenticated browser repository policy")
    artifact = browser["artifact_template"].format(version=browser_version)
    client = VerifiedTargetClient(
        service().root / "browser-metadata",
        service().root / "downloads",
        browser["metadata_url"],
        browser["targets_url"],
        Path(browser["trusted_root"]),
    )
    policy = replace(
        _policy(config, publisher, record["channel"]),
        component="camoufox",
        version=browser_version,
        browser_version=browser_version,
    )
    target = client.download(
        artifact,
        policy,
        offline=offline,
        cached_target=Path(record["browser_archive"]) if offline else None,
    )
    staging = service().root / "browser-packages"
    if not staging.exists():
        create_private_directory(staging)
    return target, extract_verified_browser_archive(target, staging)


def reopen_desktop():
    """Relaunch the fixed trusted deployment entrypoint after offline work."""
    import subprocess

    config, publisher = deployment()
    installation = Path(config["installation"])
    executable = installation / config["entrypoint"]
    args = [str(executable)]
    if not executable.is_file() or executable.is_symlink():
        helper_executable = Path(sys.executable).resolve()
        if not helper_executable.is_relative_to(service().root / "helpers"):
            raise UpdateVerificationError(
                "The retained helper is required for interrupted replacement recovery"
            )
        executable = helper_executable
        installation = helper_executable
        for _ in Path(config["entrypoint"]).parts:
            installation = installation.parent
        args = [str(executable), "--update-recovery-window"]
    manifest = {
        "publisher_policy": publisher.identifier,
        "product_id": publisher.product_id,
        "platform": _target_platform(),
        "architecture": _architecture(),
    }
    verify_publisher(installation, manifest, publisher)
    subprocess.Popen(args, cwd=installation, close_fds=True)


def execute_request(action=None, confirmed_timestamp=None):
    try:
        return _execute_request(action, confirmed_timestamp)
    except Exception:
        record = _record()
        if record:
            record["error"] = "Offline update interrupted; review recovery options"
            _save(record)
        raise


def cancel():
    srv = service()
    if srv.status()["state"] == "checkpoint_interrupted":
        srv.cancel_abandoned_checkpoint()
    else:
        srv.cancel_preparation()
    record = _record()
    if record:
        record.pop("action", None)
        record.pop("error", None)
        record["state"] = "cancelled"
        _save(record)
    return status()
