"""Explicit, desktop-only native installer handoff for Debian and Windows.

The signed updater remains separate. No HTTP caller can choose executable paths,
URLs, commands, or trust policy. Installation uses the ordinary OS installer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

import httpx
import psutil

from camoufox_pm.config import get_settings

from .installer_download import INSTALLERS, download_installer
from .update_permissions import (
    create_private_directory,
    create_private_file,
    protect_path,
    verify_private_path,
)
from .updates import check_update

controller: NativeInstaller | None = None


def clean_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("_PYI")}
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def installation() -> Path:
    if sys.platform == "linux":
        return Path("/opt/camoufox-pm")
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM",
        ) as key:
            value, kind = winreg.QueryValueEx(key, "InstallLocation")
        if kind != winreg.REG_SZ or not Path(value).is_absolute():
            raise ValueError("Invalid installed application path")
        return Path(value)
    raise ValueError("Native installation is unavailable on this platform")


def available() -> bool:
    if (
        os.environ.get("CPM_UPDATE_POLICY")
        or not getattr(sys, "frozen", False)
        or platform.machine().lower() not in ("x86_64", "amd64")
    ):
        return False
    try:
        root = installation()
        if Path(sys.executable).resolve().parent != root.resolve():
            return False
        if sys.platform == "linux":
            if not all(
                Path(path).is_file()
                for path in ("/usr/bin/pkexec", "/usr/bin/apt-get", "/usr/bin/dpkg-deb")
            ):
                return False
            result = subprocess.run(
                ["/usr/bin/dpkg-query", "-W", "-f=${Status}", "camoufox-pm"],
                capture_output=True,
                text=True,
                timeout=10,
                env=clean_environment(),
            )
            return result.returncode == 0 and result.stdout == "install ok installed"
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def root_directory() -> Path:
    root = Path(get_settings().db_path).resolve().parent / "update-control" / "installers"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    protect_path(root)
    return root


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(uuid.uuid4().hex + ".tmp")
    with os.fdopen(create_private_file(temporary), "w", encoding="utf-8") as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class NativeInstaller:
    def __init__(self) -> None:
        self.supported = available()
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {
            "supported": self.supported,
            "state": "idle",
            "downloaded": 0,
            "total": None,
            "error": None,
        }
        self.request_dir: Path | None = None

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _set(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self, channel: Literal["stable", "preview"], expected_version: str) -> dict:
        if channel not in ("stable", "preview") or not re.fullmatch(
            r"\d+\.\d+\.\d+(?:-rc\.\d+)?", expected_version
        ):
            raise ValueError("Invalid update selection")
        with self.lock:
            if not self.supported or self.state["state"] not in ("idle", "error"):
                raise ValueError("An installer cannot start in the current state")
            self.state.update(state="downloading", error=None, downloaded=0, total=None)
        threading.Thread(
            target=self._download, args=(channel, expected_version), daemon=True
        ).start()
        return self.status()

    def _download(self, channel: Literal["stable", "preview"], expected_version: str) -> None:
        directory = None
        try:
            from .maintenance import configured_gate

            if configured_gate().blocked:
                raise ValueError("Resolve update maintenance before installing")
            update = asyncio.run(check_update(channel))
            if update.error or not update.available or update.latest_version != expected_version:
                raise ValueError("The selected release changed. Check for updates again.")
            directory = root_directory() / uuid.uuid4().hex
            create_private_directory(directory)
            package = directory / INSTALLERS[sys.platform]
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                digest = download_installer(
                    update,
                    sys.platform,
                    package,
                    lambda done, total: self._set(downloaded=done, total=total),
                    client=client,
                )
            self._set(state="preparing")
            # A separate copy can keep running while Windows replaces loaded DLLs.
            helper = directory / "helper"
            shutil.copytree(installation(), helper, symlinks=True)
            record = {
                "id": directory.name,
                "state": "ready",
                "platform": sys.platform,
                "version": expected_version,
                "sha256": digest,
                "installation": str(installation()),
                "entrypoint": Path(sys.executable).name,
                "parent_pid": os.getpid(),
                "parent_created": psutil.Process().create_time(),
            }
            _write(directory / "request.json", record)
            self.request_dir = directory
            self._set(state="ready")
        except Exception:
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)
            self._set(
                state="error",
                error="Could not download or verify the installer. Check your connection and free disk space, then try again.",
            )

    def handoff(self) -> None:
        if self.status()["state"] != "ready" or self.request_dir is None:
            return
        executable = self.request_dir / "helper" / Path(sys.executable).name
        with os.fdopen(create_private_file(self.request_dir / "install.log"), "w") as log:
            subprocess.Popen(
                [str(executable), "--native-installer-helper", self.request_dir.name],
                env=clean_environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self._set(state="restarting")


def installer_command(package: Path, target: Path) -> list[str] | str:
    if sys.platform == "linux":
        return [
            "/usr/bin/pkexec",
            "/usr/bin/apt-get",
            "--yes",
            "--no-remove",
            "install",
            str(package),
        ]
    if sys.platform == "win32":
        # NSIS requires the final /D remainder to be unquoted, even with spaces.
        # CreateProcess receives this command line directly; shell stays False.
        return subprocess.list2cmdline([str(package), "/S"]) + f" /D={target}"
    raise ValueError("Unsupported native installer platform")


def run_helper(identifier: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{32}", identifier) or not getattr(sys, "frozen", False):
        raise ValueError("Invalid installer handoff")
    directory = root_directory() / identifier
    verify_private_path(directory)
    # Serialize helpers across processes; a PID check alone has a start-up race.
    with closing(sqlite3.connect(directory / "helper.sqlite3", timeout=0.1)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _run_helper(identifier, directory)


def _run_helper(identifier: str, directory: Path) -> None:
    record = json.loads((directory / "request.json").read_text())
    target = installation()
    expected_helper = directory / "helper" / Path(sys.executable).name
    if (
        record["id"] != identifier
        or record["state"] not in ("ready", "installation_failed")
        or record["platform"] != sys.platform
        or Path(record["installation"]) != target
        or Path(sys.executable).resolve() != expected_helper.resolve()
    ):
        raise ValueError("Installer handoff does not match this installation")
    if record.get("helper_pid") and record["helper_pid"] != os.getpid():
        try:
            helper = psutil.Process(record["helper_pid"])
            if abs(helper.create_time() - record["helper_created"]) < 0.01:
                raise RuntimeError("The installer helper is still running")
        except psutil.NoSuchProcess:
            pass
    record.update(helper_pid=os.getpid(), helper_created=psutil.Process(os.getpid()).create_time())
    _write(directory / "request.json", record)
    try:
        parent = psutil.Process(record["parent_pid"])
        if abs(parent.create_time() - record["parent_created"]) < 0.01:
            parent.wait(timeout=60)
    except psutil.NoSuchProcess:
        pass
    package = directory / INSTALLERS[sys.platform]
    from .maintenance import configured_gate
    from .update_recovery import _legacy_processes_stopped, offline_checkpoint
    from .update_service import UpdateService

    gate = configured_gate()
    checkpoint = record.get("checkpoint_id")
    # A failed retry must never reopen data after an earlier partial install.
    installing = record["state"] == "installation_failed"
    try:
        verify_private_path(package)
        with package.open("rb") as source:
            sha = hashlib.sha256()
            for chunk in iter(lambda: source.read(256 * 1024), b""):
                sha.update(chunk)
        if sha.hexdigest() != record["sha256"]:
            raise ValueError("Installer changed after download")
        if sys.platform == "linux":
            fields = subprocess.run(
                [
                    "/usr/bin/dpkg-deb",
                    "--show",
                    "--showformat=${Package}\n${Architecture}\n${Version}",
                    str(package),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                env=clean_environment(),
            ).stdout.splitlines()
            from packaging.version import Version

            if (
                len(fields) != 3
                or fields[:2] != ["camoufox-pm", "amd64"]
                or Version(fields[2].replace("~rc", "rc")) != Version(record["version"])
            ):
                raise ValueError("Installer package identity does not match the release")
        record["state"] = "backing_up"
        _write(directory / "request.json", record)
        if checkpoint:
            if (
                not re.fullmatch(r"[a-f0-9]{32}", checkpoint)
                or gate.owner != f"checkpoint-{checkpoint}"
            ):
                raise ValueError("Installer recovery does not own maintenance")
        else:
            checkpoint = offline_checkpoint(retain_maintenance=True, allow_missing_browser=True)
        # Serialize the checkpoint-to-install handoff with recovery cancellation.
        # Recheck writers on retries as well as on a newly completed checkpoint.
        with UpdateService(gate.path.parent, gate)._control():
            if (
                gate.owner != f"checkpoint-{checkpoint}"
                or not gate.managers_stopped()
                or not _legacy_processes_stopped()
            ):
                raise ValueError("Close all managers and browsers before installing")
            record.update(state="installing", checkpoint_id=checkpoint)
            _write(directory / "request.json", record)
            _write(
                gate.path.parent / "native-install.json",
                {"id": identifier, "checkpoint_id": checkpoint},
            )
        installing = True
        if sys.platform == "win32":
            from .windows_installer import install

            exit_code = install(package, target)
        else:
            exit_code = subprocess.run(
                installer_command(package, target), env=clean_environment()
            ).returncode
        if exit_code:
            raise RuntimeError("The system installer did not complete")
        record["state"] = "installed"
        _write(directory / "request.json", record)
        (gate.path.parent / "native-install.json").unlink()
        gate.release(f"checkpoint-{checkpoint}")
    except Exception:
        # Do not reopen potentially mismatched binaries/data after a partial install.
        record["state"] = "installation_failed" if installing else "preparation_failed"
        _write(directory / "request.json", record)
        if checkpoint and not installing and gate.owner == f"checkpoint-{checkpoint}":
            gate.release(f"checkpoint-{checkpoint}")
        from camoufox_pm.desktop import _show_error

        _show_error(
            "The update could not finish. Your data and completed backups were kept. Review install.log in "
            + str(directory)
            + " (or update-control/recovery-command.log in windowed builds). If installation started, finish or repair the OS installation before cancelling update maintenance."
        )
        raise
    # Reopening is independent of installation. Do not mislabel a completed
    # installation (or reacquire maintenance) if the desktop cannot be launched.
    try:
        subprocess.Popen(
            [str(target / record["entrypoint"])], env=clean_environment(), start_new_session=True
        )
    except OSError:
        from camoufox_pm.desktop import _show_error

        _show_error(
            "The update was installed. Open Camoufox Profile Manager from your applications menu."
        )
        raise
