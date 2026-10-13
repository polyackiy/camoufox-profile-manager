"""Desktop startup owns its socket, its data lock, and server shutdown."""

import os
import socket
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from camoufox_pm import desktop
from camoufox_pm.config import get_settings


@pytest.fixture
def desktop_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    monkeypatch.delenv("CPM_PORT", raising=False)
    monkeypatch.delenv("CPM_HOST", raising=False)

    class ClosingEvent:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    window = SimpleNamespace(events=SimpleNamespace(closing=ClosingEvent()))
    webview = SimpleNamespace(create_window=Mock(return_value=window), start=Mock())
    monkeypatch.setitem(sys.modules, "webview", webview)
    servers = []

    class Server:
        def __init__(self, config):
            self.config = config
            self.started = False
            self.should_exit = False
            self.force_exit = False
            self.stopped = False
            servers.append(self)

        def run(self, sockets):
            self.sockets = sockets
            self.started = True
            while not self.should_exit:
                time.sleep(0.001)
            self.stopped = True

    monkeypatch.setattr(desktop.uvicorn, "Server", Server)
    yield SimpleNamespace(webview=webview, servers=servers, directory=tmp_path)
    get_settings.cache_clear()


def test_desktop_chooses_owned_free_port_and_stops_server(desktop_runtime):
    desktop.run_desktop()
    runtime = desktop_runtime
    server = runtime.servers[0]
    url = runtime.webview.create_window.call_args.args[1]
    address = urlparse(url)
    assert address.hostname == "127.0.0.1"
    assert address.port > 0
    assert server.config.port == address.port
    assert get_settings().port == address.port
    assert server.stopped
    assert server.sockets[0].fileno() == -1
    assert (runtime.directory / "secret.key").exists()


def test_occupied_port_never_opens_unrelated_server(desktop_runtime):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(RuntimeError, match="Cannot start"):
            desktop.run_desktop(port=occupied.getsockname()[1])
    desktop_runtime.webview.create_window.assert_not_called()
    assert desktop_runtime.servers == []


def test_window_failure_still_stops_server_and_releases_lock(desktop_runtime):
    desktop_runtime.webview.start.side_effect = RuntimeError("window failed")
    with pytest.raises(RuntimeError, match="window failed"):
        desktop.run_desktop()
    assert desktop_runtime.servers[0].stopped
    with desktop._instance_lock(desktop_runtime.directory):
        pass


def test_missing_native_window_still_finishes_server_cleanup(desktop_runtime):
    desktop_runtime.webview.create_window.return_value = None
    with pytest.raises(RuntimeError, match="native window could not be created"):
        desktop.run_desktop()
    desktop_runtime.webview.start.assert_not_called()
    assert desktop_runtime.servers[0].stopped


def test_startup_failure_never_opens_window_and_stops_server(desktop_runtime, monkeypatch):
    monkeypatch.setattr(desktop, "_wait_until_serving", lambda server, thread: False)
    with pytest.raises(RuntimeError, match="could not start"):
        desktop.run_desktop()
    desktop_runtime.webview.create_window.assert_not_called()
    assert desktop_runtime.servers[0].stopped


def test_second_instance_is_rejected_until_first_releases_lock(tmp_path):
    with desktop._instance_lock(tmp_path):
        with pytest.raises(desktop.AlreadyRunningError, match="already open"):
            with desktop._instance_lock(tmp_path):
                pytest.fail("second instance was allowed")
    with desktop._instance_lock(tmp_path):
        pass


def test_dead_server_does_not_pass_readiness_for_occupied_port():
    assert not desktop._wait_until_serving(
        SimpleNamespace(started=False), SimpleNamespace(is_alive=lambda: False)
    )


def test_packaged_entrypoint_bootstraps_before_window(monkeypatch, tmp_path):
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "desktop-data" / "profiles.db"))
    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    observed = []
    monkeypatch.setattr(desktop, "run_desktop", lambda: observed.append(get_settings().db_path))
    desktop.main()
    assert observed == [str(tmp_path / "desktop-data" / "profiles.db")]
    assert (tmp_path / "desktop-data" / "secret.key").exists()
    get_settings.cache_clear()


@pytest.mark.parametrize("platform,frozen", [("darwin", False), ("linux", True), ("win32", True)])
def test_process_exit_is_only_for_frozen_macos(monkeypatch, platform, frozen):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    exit_process = Mock()
    monkeypatch.setattr(desktop.os, "_exit", exit_process)
    desktop.finish_packaged_desktop()
    exit_process.assert_not_called()


def test_frozen_macos_flushes_logs_before_process_exit(monkeypatch):
    import logging

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    events = []
    stream = SimpleNamespace(flush=lambda: events.append("flush"))
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    monkeypatch.setattr(logging, "shutdown", lambda: events.append("logging"))
    monkeypatch.setattr(desktop.os, "_exit", lambda code: events.append(("exit", code)))
    desktop.finish_packaged_desktop()
    assert events == ["logging", "flush", "flush", ("exit", 0)]


def test_incomplete_server_shutdown_is_not_reported_as_success(desktop_runtime, monkeypatch):
    class StuckThread:
        def __init__(self, target, kwargs, daemon):
            self.server = target.__self__

        def start(self):
            self.server.started = True

        def join(self, timeout):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(desktop.threading, "Thread", StuckThread)
    with pytest.raises(RuntimeError, match="did not finish shutting down"):
        desktop.run_desktop()
    assert desktop_runtime.servers[0].should_exit
    assert desktop_runtime.servers[0].force_exit


def test_macos_native_quit_cleans_server_before_permitting_close(desktop_runtime, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def native_quit():
        window = desktop_runtime.webview.create_window.return_value
        assert len(window.events.closing.handlers) == 1
        assert window.events.closing.handlers[0]() is True
        assert desktop_runtime.servers[0].stopped

    desktop_runtime.webview.start.side_effect = native_quit
    desktop.run_desktop()


def test_native_quit_is_cancelled_while_server_is_still_running():
    server = SimpleNamespace(should_exit=False, force_exit=False)
    thread = SimpleNamespace(join=Mock(), is_alive=lambda: True)
    assert desktop._stop_server(server, thread) is False
    assert server.should_exit
    assert server.force_exit


def test_forced_server_exit_cannot_authorize_successful_native_quit():
    server = SimpleNamespace(should_exit=False, force_exit=False)
    thread = SimpleNamespace(join=Mock(), is_alive=Mock(side_effect=[True, False]))
    assert desktop._stop_server(server, thread) is False
    assert server.force_exit


def test_packaged_recovery_dispatches_before_key_bootstrap(tmp_path, monkeypatch):
    import sys

    from camoufox_pm import cli, config, desktop
    from camoufox_pm.core import maintenance
    from camoufox_pm.core.maintenance import MaintenanceGate

    gate = MaintenanceGate(tmp_path / "update-control" / "maintenance.sqlite3")
    monkeypatch.setattr(maintenance, "configured_gate", lambda: gate)
    monkeypatch.setattr(config, "desktop_data_dir", lambda: tmp_path)
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "profiles.db"))

    def forbidden():
        raise AssertionError("Packaged recovery must not bootstrap keys or start the server")

    monkeypatch.setattr(config, "bootstrap_desktop", forbidden)
    monkeypatch.setattr(desktop, "run_desktop", forbidden)
    monkeypatch.setattr(sys, "argv", ["Camoufox Profile Manager", "update-recovery", "status"])
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    def recovery_command():
        assert "--desktop" in sys.argv
        print("checkpoint-id and timestamp")

    monkeypatch.setattr(cli, "main", recovery_command)
    with monkeypatch.context() as windowed:
        windowed.setattr(sys, "stdout", None)
        windowed.setattr(sys, "stderr", None)
        desktop.main()
    assert (
        gate.path.parent / "recovery-command.log"
    ).read_text() == "checkpoint-id and timestamp\n"
    assert not (tmp_path / "secret.key").exists()
    config.get_settings.cache_clear()


@pytest.mark.parametrize("failure", ["import", "command"])
def test_packaged_recovery_errors_log_and_exit_without_gui(tmp_path, monkeypatch, failure):
    import builtins
    import sys

    from camoufox_pm import cli, config, desktop

    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr(sys, "argv", ["bundle", "--desktop", "update-recovery", "status"])

    def forbidden_gui(*args):
        raise AssertionError("Recovery errors must not open a dialog")

    def fail_command():
        raise RuntimeError("diagnostic command failure")

    monkeypatch.setattr(desktop, "_show_error", forbidden_gui)
    monkeypatch.setattr(cli, "main", fail_command)
    original_import = builtins.__import__

    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if failure == "import" and name == "camoufox_pm" and "cli" in fromlist:
            raise ImportError("diagnostic early crypto loader failure")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", importing)
    with monkeypatch.context() as windowed:
        windowed.setattr(sys, "stdout", None)
        windowed.setattr(sys, "stderr", None)
        with pytest.raises(SystemExit) as result:
            desktop.main()
    assert result.value.code == 1
    log = (tmp_path / "update-control" / "recovery-command.log").read_text()
    assert "Traceback" in log and "diagnostic" in log
    assert not (tmp_path / "secret.key").exists()
    config.get_settings.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="POSIX early log protection")
def test_packaged_recovery_refuses_control_symlink_without_chmod_or_gui(tmp_path, monkeypatch):
    import sys

    from camoufox_pm import config, desktop

    target = tmp_path / "other-directory"
    target.mkdir(mode=0o755)
    original_mode = target.stat().st_mode
    (tmp_path / "update-control").symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr(sys, "argv", ["bundle", "--desktop", "update-recovery", "status"])
    monkeypatch.setattr(
        desktop, "_show_error", lambda *args: pytest.fail("No GUI on recovery failure")
    )
    with monkeypatch.context() as windowed:
        windowed.setattr(sys, "stdout", None)
        windowed.setattr(sys, "stderr", None)
        with pytest.raises(SystemExit) as result:
            desktop.main()
    assert result.value.code == 1
    assert target.stat().st_mode == original_mode
    assert not (target / "recovery-command.log").exists()
    config.get_settings.cache_clear()
