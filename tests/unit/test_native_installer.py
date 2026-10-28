import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from camoufox_pm.core import installer_download as download
from camoufox_pm.core import native_installer as native
from camoufox_pm.core.maintenance import MaintenanceBlocked, MaintenanceGate
from camoufox_pm.core.update_service import UpdateService
from camoufox_pm.core.updates import ReleaseAsset, UpdateStatus


def release(platform="linux"):
    name = download.INSTALLERS[platform]
    base = download.RELEASES + "/download/v0.7.0/"
    return UpdateStatus(
        available=True,
        latest_version="0.7.0",
        assets=[
            ReleaseAsset(name=name + suffix, url=base + name + suffix) for suffix in ("", ".sha256")
        ],
    )


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_download_checks_integrity_and_follows_only_allowed_redirects(tmp_path, platform):
    payload = b"installer-test-payload"
    checksum = hashlib.sha256(payload).hexdigest()
    name = download.INSTALLERS[platform]
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if str(request.url).endswith(".sha256"):
            return httpx.Response(200, text=f"{checksum}  {name}\n")
        if request.url.host == "github.com":
            return httpx.Response(
                302, headers={"Location": "https://release-assets.githubusercontent.com/file"}
            )
        return httpx.Response(200, content=payload)

    progress = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        digest = download.download_installer(
            release(platform),
            platform,
            tmp_path / name,
            lambda *args: progress.append(args),
            client=client,
        )
    assert (tmp_path / name).read_bytes() == payload
    assert digest == checksum and len(calls) == 3
    assert progress[-1][0] == len(payload)


@pytest.mark.parametrize("fault", ["digest", "filename", "redirect", "size"])
def test_download_failures_never_publish_an_installer(tmp_path, fault):
    name = download.INSTALLERS["linux"]

    def handler(request):
        if str(request.url).endswith(".sha256"):
            filename = "other.deb" if fault == "filename" else name
            return httpx.Response(200, text=f"{'a' * 64}  {filename}")
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "http://untrusted.invalid/installer"})
        return httpx.Response(
            200,
            content=b"wrong",
            headers={"content-length": str(download.MAX_INSTALLER_BYTES + 1)}
            if fault == "size"
            else {},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(ValueError):
        download.download_installer(
            release(), "linux", tmp_path / name, lambda *_: None, client=client
        )
    assert not (tmp_path / name).exists()
    assert not list(tmp_path.glob("*.part"))


def test_release_selection_cannot_supply_an_executable_url():
    selected = release()
    selected.assets[0].url = "https://evil.invalid/install.deb"
    with pytest.raises(ValueError):
        download.installer_url(selected, "linux")


def test_nsis_path_is_the_final_unquoted_remainder(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "win32")
    command = native.installer_command(Path("C:/Cache/installer.exe"), Path("C:/Programs/My App"))
    assert command.endswith(" /D=C:/Programs/My App")
    assert "/S " in command
    assert "Bypass" not in command


def test_frozen_loader_environment_is_not_passed_to_os_installer(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundled/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/lib")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/old/app")
    env = native.clean_environment()
    assert env["LD_LIBRARY_PATH"] == "/system/lib"
    assert not any(key.startswith("_PYI") for key in env)
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.parametrize(
    "failure",
    [None, "backup", "installer", "tamper", "retry_tamper", "retry_active", "cancelled", "reopen"],
)
def test_helper_backs_up_before_installing_and_keeps_failure_closed(tmp_path, monkeypatch, failure):
    from camoufox_pm import desktop
    from camoufox_pm.core import maintenance, update_recovery

    identifier = "a" * 32
    checkpoint = "b" * 32
    directory = tmp_path / identifier
    directory.mkdir(mode=0o700)
    helper = directory / "helper" / "camoufox-pm"
    helper.parent.mkdir()
    helper.touch()
    package = directory / download.INSTALLERS["linux"]
    package.write_bytes(b"payload")
    package.chmod(0o600)
    target = tmp_path / "installed"
    record = {
        "id": identifier,
        "state": "ready",
        "platform": "linux",
        "version": "0.7.0",
        "sha256": hashlib.sha256(b"payload").hexdigest(),
        "installation": str(target),
        "entrypoint": "camoufox-pm",
        "parent_pid": 123,
        "parent_created": 42,
    }
    (directory / "request.json").write_text(json.dumps(record))
    gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
    if failure in ("retry_tamper", "retry_active"):
        record.update(state="installation_failed", checkpoint_id=checkpoint)
        (directory / "request.json").write_text(json.dumps(record))
        gate.acquire("checkpoint-" + checkpoint)
        (gate.path.parent / "native-install.json").write_text(json.dumps({"id": identifier}))
    events = []
    monkeypatch.setattr(native, "root_directory", lambda: tmp_path)
    monkeypatch.setattr(native, "installation", lambda: target)
    monkeypatch.setattr(native.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native.sys, "executable", str(helper))
    monkeypatch.setattr(
        native.psutil,
        "Process",
        lambda _: SimpleNamespace(
            create_time=lambda: 42, wait=lambda **kw: events.append("parent exited")
        ),
    )
    monkeypatch.setattr(maintenance, "configured_gate", lambda: gate)
    monkeypatch.setattr(desktop, "_show_error", lambda _: events.append("error shown"))
    monkeypatch.setattr(
        update_recovery, "_legacy_processes_stopped", lambda: failure != "retry_active"
    )

    def backup(**kwargs):
        events.append("backup")
        assert kwargs == {"retain_maintenance": True, "allow_missing_browser": True}
        if failure == "backup":
            raise OSError("disk full")
        gate.acquire("checkpoint-" + checkpoint)
        if failure == "cancelled":
            gate.release("checkpoint-" + checkpoint)
        return checkpoint

    monkeypatch.setattr(update_recovery, "offline_checkpoint", backup)

    def run(command, **kwargs):
        if command[0] == "/usr/bin/dpkg-deb":
            return SimpleNamespace(stdout="camoufox-pm\namd64\n0.7.0")
        assert events == ["parent exited", "backup"]
        assert gate.blocked and (gate.path.parent / "native-install.json").exists()
        events.append("installer")
        return SimpleNamespace(returncode=1 if failure == "installer" else 0)

    monkeypatch.setattr(native.subprocess, "run", run)

    def reopen(*args, **kwargs):
        if failure == "reopen":
            raise OSError("desktop unavailable")
        events.append("reopened")

    monkeypatch.setattr(native.subprocess, "Popen", reopen)
    if failure in ("tamper", "retry_tamper"):
        package.write_bytes(b"tampered")
    if failure:
        with pytest.raises((OSError, RuntimeError, ValueError)):
            native.run_helper(identifier)
        assert "reopened" not in events
        assert gate.blocked == (failure in ("installer", "retry_tamper", "retry_active"))
        if failure == "reopen":
            assert json.loads((directory / "request.json").read_text())["state"] == "installed"
        elif failure not in ("installer", "retry_tamper", "retry_active"):
            assert "installer" not in events
        else:
            with pytest.raises(MaintenanceBlocked):
                UpdateService(gate.path.parent, gate).cancel_abandoned_checkpoint()
    else:
        native.run_helper(identifier)
        assert events == ["parent exited", "backup", "installer", "reopened"]
        assert not gate.blocked


def test_controller_rejects_duplicate_downloads_and_invalid_selection(monkeypatch):
    monkeypatch.setattr(native, "available", lambda: True)
    started = []
    monkeypatch.setattr(
        native.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw))
    )
    controller = native.NativeInstaller()
    assert controller.start("preview", "0.7.0-rc.1")["state"] == "downloading"
    assert started[0]["args"] == ("preview", "0.7.0-rc.1")
    with pytest.raises(ValueError):
        controller.start("stable", "0.7.0")
    controller._set(state="error")
    with pytest.raises(ValueError):
        controller.start("stable", "../installer")
    assert len(started) == 1
