"""Desktop startup owns its socket, its data lock, and server shutdown."""

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
    webview = SimpleNamespace(create_window=Mock(), start=Mock())
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
