"""CI setup uses the real installer and rejects an incomplete or wrong pair."""

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
    from camoufox import browser_pin

    from camoufox_pm.core import browser_install

    installer = Mock()
    installer.status.return_value = browser_install.BrowserStatus(
        installed=True, state="ready", version="158.0.1-beta.41"
    )
    monkeypatch.setattr(browser_install, "BrowserInstaller", lambda: installer)
    monkeypatch.setattr(browser_pin, "load_pin", lambda: SimpleNamespace(spec="158.0.1-beta.41"))
    return installer


def test_helper_checks_readiness_after_manager_install(setup, capsys):
    assert helper.main() == 0
    assert [call[0] for call in setup.mock_calls] == ["_run", "status"]
    assert "158.0.1-beta.41" in capsys.readouterr().out


def test_sensitive_exception_fails_ci_without_logging_token(setup, capsys):
    setup._run.side_effect = RuntimeError(f"missing browser; Authorization: Bearer {TOKEN}")
    assert helper.main() == 1
    captured = capsys.readouterr()
    assert "setup failed" in captured.err
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize("installed,version", [(False, None), (True, "152.0.4-beta.29")])
def test_wrong_or_missing_browser_never_passes_pair_verification(setup, installed, version):
    setup.status.return_value.installed = installed
    setup.status.return_value.version = version
    assert helper.main() == 1


def test_failed_install_does_not_pass_on_an_existing_cache(setup):
    setup.status.return_value.state = "error"
    assert helper.main() == 1


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
