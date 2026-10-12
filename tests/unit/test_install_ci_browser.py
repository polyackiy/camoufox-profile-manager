"""CI setup uses upstream auth and fails closed without launching a browser."""

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from requests.adapters import BaseAdapter

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_ci_browser.py"
spec = importlib.util.spec_from_file_location("install_ci_browser", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
TOKEN = "synthetic-ci-token"


@pytest.fixture
def setup(monkeypatch):
    from camoufox import __main__, browser_pin

    from camoufox_pm.core import browser_install

    events = []
    fetch = Mock(side_effect=lambda **kwargs: events.append("fetch"))
    ready = Mock(side_effect=lambda: events.append("readiness"))
    monkeypatch.setattr(__main__.cli, "main", fetch)
    monkeypatch.setattr(browser_install, "ensure_browser_ready", ready)
    monkeypatch.setattr(browser_install, "installed_browser", lambda: (True, "156.0.1-beta.36"))
    monkeypatch.setattr(browser_pin, "load_pin", lambda: SimpleNamespace(spec="156.0.1-beta.36"))
    return SimpleNamespace(fetch=fetch, ready=ready, events=events, install=browser_install)


def test_helper_checks_readiness_after_upstream_returns_zero(setup, capsys):
    assert helper.main() == 0
    setup.fetch.assert_called_once_with(args=["fetch"], prog_name="camoufox", standalone_mode=False)
    assert setup.events == ["fetch", "readiness"]
    assert "156.0.1-beta.36" in capsys.readouterr().out


def test_upstream_false_success_and_sensitive_exception_fail_ci_without_logging_token(
    setup, capsys
):
    setup.ready.side_effect = RuntimeError(f"missing browser; Authorization: Bearer {TOKEN}")
    assert helper.main() == 1
    setup.fetch.assert_called_once()
    captured = capsys.readouterr()
    assert "setup failed" in captured.err
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize("installed,version", [(False, None), (True, "152.0.4-beta.29")])
def test_wrong_or_missing_browser_never_passes_pair_verification(
    setup, monkeypatch, installed, version
):
    monkeypatch.setattr(setup.install, "installed_browser", lambda: (installed, version))
    assert helper.main() == 1


def test_explicit_fetch_failure_does_not_pass_on_an_existing_cache(setup):
    setup.fetch.side_effect = None
    setup.fetch.return_value = 1
    assert helper.main() == 1
    setup.ready.assert_not_called()


class RecordingAdapter(BaseAdapter):
    """Exercise Requests' real redirect/auth policy without network transport."""

    def __init__(self, locations=()):
        self.locations = list(locations)
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append((request.method, request.url, dict(request.headers)))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 302 if self.locations else 200
        if self.locations:
            response.headers["Location"] = self.locations.pop(0)
        response._content = b"[]"
        response._content_consumed = True
        response.raw = io.BytesIO(b"[]")
        return response

    def close(self):
        pass


@pytest.fixture
def auth_transport(monkeypatch):
    from camoufox import pkgman

    # The real CLI process reads this CI environment hook during module import.
    # Our test process imported Camoufox earlier through shared fixtures.
    monkeypatch.setattr(pkgman, "GITHUB_TOKEN", TOKEN)
    session = requests.Session()
    session.trust_env = False
    adapter = RecordingAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    monkeypatch.setattr(requests.sessions, "Session", lambda: session)
    yield SimpleNamespace(pkgman=pkgman, adapter=adapter)
    session.close()


def test_upstream_default_release_api_is_authenticated(auth_transport):
    assert auth_transport.pkgman.list_available_versions() == []
    method, url, headers = auth_transport.adapter.calls[0]
    assert method == "GET" and url.startswith("https://api.github.com/repos/")
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/camoufox/camoufox/releases/download/v156/browser.zip",
        "https://release-assets.githubusercontent.com/browser.zip",
        "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi",
        "https://raw.githubusercontent.com/geoip/source/main/database.mmdb",
    ],
)
def test_upstream_default_external_downloads_do_not_receive_ci_token(auth_transport, url):
    auth_transport.pkgman.webdl(url, bar=False)
    method, requested, headers = auth_transport.adapter.calls[0]
    assert method == "GET" and requested == url
    assert TOKEN not in str(headers)


@pytest.mark.parametrize(
    "location",
    [
        "https://release-assets.githubusercontent.com/browser.zip",
        "https://github.com/camoufox/camoufox/releases/download/v156/browser.zip",
        "http://api.github.com/repos/camoufox/camoufox/releases",
        "https://api.github.com:8443/repos/camoufox/camoufox/releases",
    ],
)
def test_actual_requests_redirects_strip_ci_auth_on_host_or_scheme_or_port_change(
    auth_transport, location
):
    auth_transport.adapter.locations = [location]
    auth_transport.pkgman.list_available_versions()
    assert auth_transport.adapter.calls[0][2]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(auth_transport.adapter.calls[1][2])
