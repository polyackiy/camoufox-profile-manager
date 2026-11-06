"""Disposable runner only: real published package to exact candidate artifact."""

import argparse
import contextlib
import ctypes
import hashlib
import http.server
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from scripts.installed_sync_acceptance import (
    Device,
    InstalledAPIError,
    Probe,
    prepare_profile_backup,
    terminate_tree,
    wait_for,
)


def checked_digest(package):
    expected, name = package.with_name(package.name + ".sha256").read_text().split()
    with package.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if name.lstrip("*") != package.name or expected.lower() != actual:
        raise RuntimeError("Package checksum mismatch")
    return actual


def checked_run(args, *, timeout=240):
    return subprocess.run(args, capture_output=True, check=True, timeout=timeout, text=True).stdout


class Installer:
    def __init__(self, work):
        self.target = work / "application"
        if sys.platform == "win32":
            import winreg

            self.registration = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.registration):
                    raise RuntimeError("The Windows runner already has a manager installation")
            except FileNotFoundError:
                pass
            self.executable = self.target / "camoufox-pm.exe"
        elif sys.platform.startswith("linux"):
            if (
                Path("/opt/camoufox-pm").exists()
                or subprocess.run(
                    ["dpkg-query", "-W", "camoufox-pm"], capture_output=True, check=False
                ).returncode
                == 0
            ):
                raise RuntimeError("The Linux runner already has a manager installation")
            self.executable = Path("/usr/bin/camoufox-pm")
        elif sys.platform == "darwin":
            self.target.mkdir()
            self.bundle = self.target / "Camoufox Profile Manager.app"
            self.executable = self.bundle / "Contents" / "MacOS" / "camoufox-pm"
        else:
            raise RuntimeError("Unsupported runner for this trial")

    def install(self, package, *, rollback=False):
        if sys.platform == "win32":
            # NSIS requires the absolute /D switch to remain last and unquoted.
            command = subprocess.list2cmdline([str(package), "/S"]) + f" /D={self.target}"
            subprocess.run(command, capture_output=True, check=True, timeout=180)
        elif sys.platform == "darwin":
            mount = self.target / "mount"
            checked_run(
                [
                    "hdiutil",
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-mountpoint",
                    str(mount),
                    str(package),
                ]
            )
            try:
                if self.bundle.exists():
                    shutil.rmtree(self.bundle)
                checked_run(["ditto", str(mount / self.bundle.name), str(self.bundle)])
            finally:
                checked_run(["hdiutil", "detach", str(mount)], timeout=60)
        else:
            command = ["sudo", "-n", "apt-get", "install", "-y"]
            if rollback:
                command.append("--allow-downgrades")
            checked_run([*command, str(package)])
            expected = (
                "0.6.0"
                if rollback
                else checked_run(["dpkg-deb", "-f", str(package), "Version"]).strip()
            )
            assert checked_run(["dpkg-query", "-W", "-f=${Version}", "camoufox-pm"]) == expected
        assert self.executable.is_file()

    def cleanup(self, work):
        if sys.platform == "win32":
            uninstaller = self.target / "Uninstall.exe"
            if uninstaller.is_file():
                runner = work / "uninstall-trial.exe"
                shutil.copy2(uninstaller, runner)
                subprocess.run(
                    subprocess.list2cmdline([str(runner), "/S"]) + f" _?={self.target}",
                    check=True,
                    capture_output=True,
                    timeout=180,
                )
        elif sys.platform.startswith("linux") and self.executable.is_file():
            checked_run(["sudo", "-n", "apt-get", "purge", "-y", "camoufox-pm"])


def close_native_window(pid):
    if sys.platform == "darwin":
        script = """
ObjC.import("AppKit");
function run(args) {
    const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(Number(args[0]));
    if (!app.terminate) throw Error("The exact desktop process refused Quit");
}
"""
        checked_run(["osascript", "-l", "JavaScript", "-e", script, str(pid)], timeout=15)
        return
    if sys.platform != "win32":

        def window():
            for line in checked_run(["wmctrl", "-lp"], timeout=10).splitlines():
                fields = line.split(maxsplit=4)
                if (
                    len(fields) == 5
                    and fields[2] == str(pid)
                    and fields[4] == "Camoufox Profile Manager"
                ):
                    return fields[0]
            return None

        checked_run(["wmctrl", "-i", "-c", wait_for(window, seconds=20)], timeout=10)
        return

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL

    def window():
        matched = []

        @callback
        def inspect(handle, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(handle):
                title = ctypes.create_unicode_buffer(128)
                user32.GetWindowTextW(handle, title, len(title))
                if title.value == "Camoufox Profile Manager":
                    matched.append(handle)
            return True

        if not user32.EnumWindows(inspect, 0):
            raise RuntimeError("Could not enumerate the exact desktop window")
        return matched[0] if len(matched) == 1 else None

    if not user32.PostMessageW(wait_for(window, seconds=20), 0x0010, 0, 0):  # WM_CLOSE
        raise RuntimeError("Could not close the exact desktop window")


class UpgradeDevice(Device):
    def __init__(self, work, browser, executable, data):
        self.work, self.browser, self.executable = work, browser, executable
        self.password = str(uuid4())
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CPM_") and key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        self.environment.update(CPM_DB_PATH=str(data / "profiles.db"), PYTHONIOENCODING="utf-8")

    def check_version(self, expected):
        expect(self.page.get_by_role("button", name="New profile", exact=True)).to_be_visible()
        assert self.call("/system/info")["data"]["version"] == expected

    def stop(self):
        self.context.close()
        close_native_window(self.process.pid)
        assert self.process.wait(timeout=40) == 0

    def recovery(self, report, *arguments):
        log = Path(self.environment["CPM_DB_PATH"]).parent / "update-control/recovery-command.log"
        offset = log.stat().st_size if log.is_file() else 0
        result = subprocess.run(
            [str(self.executable), "--desktop", "update-recovery", *arguments],
            cwd=self.work,
            env=self.environment,
            capture_output=True,
            timeout=420,
            check=False,
        )
        output = result.stdout + result.stderr
        if log.is_file():
            with log.open("rb") as stream:
                stream.seek(max(offset, log.stat().st_size - 65536))
                output += stream.read(65536)
        if result.returncode:
            report["recovery_markers"] = [
                marker.decode()
                for marker in (
                    b"PermissionProtectionError",
                    b"MaintenanceBlocked",
                    b"CheckpointError",
                )
                if marker in output
            ]
            raise RuntimeError("Frozen recovery command failed")
        return output.decode("utf-8", errors="replace")


def checkpoint_metadata(output):
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except ValueError:
            continue
        if (
            isinstance(value, dict)
            and value.get("manager_version") == "0.6.0"
            and {"checkpoint_id", "created_at"} <= value.keys()
        ):
            return value
    raise RuntimeError("The old installed manager did not emit checkpoint metadata")


def schema(data):
    with contextlib.closing(
        sqlite3.connect((data / "profiles.db").as_uri() + "?mode=ro", uri=True)
    ) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        return connection.execute("PRAGMA user_version").fetchone()[0]


def profile_state(device, profile_id, site, stage, report):
    report["profile_stage"] = stage
    backup = {}
    report.setdefault("backups", {})[stage] = backup
    log = Path(device.environment["CPM_DB_PATH"]).parent / "desktop.log"
    offset = log.stat().st_size if log.is_file() else 0
    try:
        prepare_profile_backup(device, profile_id, backup)
    except InstalledAPIError as error:
        backup["http_status"] = error.status
        # Only the new fixed markers from this disposable app may leave the
        # worker. Never attach raw paths, exception text or the desktop log.
        if log.is_file():
            with log.open("rb") as stream:
                stream.seek(max(offset, log.stat().st_size - 65536))
                observed = stream.read(65536)
            markers = {
                "sqlite_locked": b"database is locked",
                "sqlite_io": b"disk I/O error",
                "sqlite_open": b"unable to open database file",
                "profile_busy": b"ProfileLocked",
                "private_acl": b"PermissionProtectionError",
                "permission": b"PermissionError",
                "sharing_violation": b"WinError 32",
                "maintenance": b"MaintenanceBlocked",
            }
            backup["markers"] = [name for name, marker in markers.items() if marker in observed]
        raise
    device.call(
        f"/profiles/{profile_id}/launch",
        {
            "headless": True,
            "additional_options": {
                "geoip": "8.8.8.8",
                "args": ["-new-tab", f"{site}/account?stage={stage}"],
            },
        },
    )
    try:
        result = wait_for(lambda: Probe.reports.get(stage), seconds=120)
        assert result["authenticated"] and result["httpOnly"] and not result.get("error")
        return result
    finally:
        device.call("/browsers/close-all", {})
        assert not device.call("/browsers/active")["active_browsers"]


def exercise(work, browser, installer, old, new, site, report):
    data = work / "data"
    device = UpgradeDevice(work, browser, installer.executable, data)
    rollback = None
    try:
        report["phase"] = "baseline"
        installer.install(old)
        device.start()
        device.call(
            "/access/users",
            {"username": "ci-admin", "password": device.password, "role": "administrator"},
        )
        device.login()
        device.check_version("0.6.0")
        profile_id = device.call(
            "/profiles",
            {
                "name": "Disposable retained profile",
                "generate_fingerprint": False,
                "browser_settings": {"stable_canvas": True},
            },
        )["id"]
        before = profile_state(device, profile_id, site, "first", report)
        fingerprint = device.call(f"/profiles/{profile_id}")["fingerprint"]
        assert fingerprint
        device.stop()
        assert schema(data) == 4
        checkpoint = work / "checkpoint"
        windows_recovery = None
        report["phase"] = "checkpoint"
        if sys.platform == "win32":
            windows_recovery = checkpoint_metadata(device.recovery(report, "checkpoint"))
            report["checkpoint_mode"] = "packaged-recovery"
        else:
            shutil.copytree(data, checkpoint, symlinks=True)
            report["checkpoint_mode"] = "cold-copy"
        key = (data / "secret.key").read_bytes()
        db = (data / "profiles.db").read_bytes()

        report["phase"] = "upgrade"
        installer.install(new)
        assert (data / "profiles.db").read_bytes() == db and (
            data / "secret.key"
        ).read_bytes() == key
        device.start()
        device.login()
        device.check_version("0.7.0")
        assert schema(data) == 5 and (data / "secret.key").read_bytes() == key
        assert device.call("/sync/status") == {"phase": "not_enrolled"}
        assert device.call(f"/profiles/{profile_id}")["fingerprint"] == fingerprint
        after = profile_state(device, profile_id, site, "return", report)
        assert (
            after["state"] == before["state"] == "first" and after["identity"] == before["identity"]
        )
        # Canvas runtime stability is a separate physical Windows acceptance gate.
        report["canvas_match"] = before["canvas"] == after["canvas"]
        device.stop()
        report["upgrade_passed"] = True

        report["phase"] = "manual-rollback"
        installer.install(old, rollback=True)
        if windows_recovery is not None:
            checkpoint_id = windows_recovery["checkpoint_id"]
            device.recovery(
                report,
                "restore",
                "--checkpoint-id",
                checkpoint_id,
                "--confirm-timestamp",
                windows_recovery["created_at"],
            )
            assert (data / f"profiles.db.failed-{checkpoint_id}").is_file()
        else:
            # Existing profile rows carry absolute storage paths. Restore the
            # matching checkpoint at that same root and retain the upgraded data.
            data.rename(work / "post-upgrade")
            shutil.copytree(checkpoint, data, symlinks=True)
        rollback = UpgradeDevice(work, browser, installer.executable, data)
        rollback.password = device.password
        rollback.start()
        rollback.login()
        rollback.check_version("0.6.0")
        assert schema(data) == 4 and (data / "secret.key").read_bytes() == key
        original = profile_state(rollback, profile_id, site, "restored", report)
        assert original["state"] == "first" and original["identity"] == before["identity"]
        assert rollback.call(f"/profiles/{profile_id}")["fingerprint"] == fingerprint
        rollback.stop()
    finally:
        for instance in (device, rollback):
            if instance is not None and hasattr(instance, "process"):
                terminate_tree(instance.process)
        installer.cleanup(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        parser.error("Run on a disposable GitHub-hosted Actions runner")
    report = {
        "platform": sys.platform,
        "arch": platform.machine(),
        "passed": False,
        "phase": "packages",
    }
    try:
        old, new = args.previous.resolve(), args.candidate.resolve()
        report.update(previous_sha256=checked_digest(old), candidate_sha256=checked_digest(new))
        with (
            tempfile.TemporaryDirectory(prefix="cpm-upgrade-") as scratch,
            http.server.ThreadingHTTPServer(("127.0.0.1", 0), Probe) as server,
            sync_playwright() as playwright,
        ):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = playwright.chromium.launch()
            try:
                work = Path(scratch).resolve()
                exercise(
                    work,
                    browser,
                    Installer(work),
                    old,
                    new,
                    f"http://127.0.0.1:{server.server_address[1]}",
                    report,
                )
            finally:
                browser.close()
                server.shutdown()
        report.update(passed=True, phase="complete")
    except BaseException as error:
        report["error"] = type(error).__name__
        report["line"] = __import__("traceback").extract_tb(error.__traceback__)[-1].lineno
        raise RuntimeError(f"Package transition failed during {report['phase']}") from None
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
