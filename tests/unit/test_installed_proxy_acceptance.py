"""Real fixture TLS/containment and secret-free installed acceptance failures."""

import asyncio
import json
import ssl
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from scripts import installed_proxy_acceptance as harness

from camoufox_pm.core import proxy_check
from camoufox_pm.core.models import ProxyConfig
from tests.support.proxy_protocols import certificates


@pytest.mark.parametrize("protocol,auth", harness.CASES)
def test_installed_health_fixture_intercepts_standard_endpoints_without_external_network(
    tmp_path, monkeypatch, protocol, auth
):
    certs = certificates(tmp_path, dns_names=harness.DNS_NAMES)
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    with harness.FixtureService(protocol, certs, auth=auth) as service:
        fixture = service.fixture
        proxy = ProxyConfig(
            type=protocol,
            server=f"127.0.0.1:{fixture.proxy_port}",
            username=fixture.username if auth else None,
            password=fixture.password if auth else None,
        )
        ip, _ = asyncio.run(proxy_check.resolve_exit_ip(proxy, timeout=3))
        assert ip == harness.EXIT_IP
        assert any(event[0] == "origin" for event in service.snapshot()[0])
        service.reject()
        with pytest.raises(ConnectionError):
            asyncio.run(proxy_check.resolve_exit_ip(proxy, timeout=3))
    assert not service.thread.is_alive() and service.loop.is_closed()
    assert not fixture.tasks


@pytest.mark.parametrize("protocol", ["http", "https"])
def test_installed_fixture_records_untrusted_tls_alert_without_reading_an_origin(
    tmp_path, protocol
):
    certs = certificates(tmp_path, dns_names=harness.DNS_NAMES)
    with harness.FixtureService(protocol, certs) as service:
        fixture = service.fixture
        with httpx.Client(
            proxy=f"{protocol}://127.0.0.1:{fixture.proxy_port}", timeout=3
        ) as client:
            with pytest.raises((httpx.ConnectError, httpx.ProxyError)):
                client.get("https://cpm-origin.invalid:443/proof/" + "a" * 24 + "-https")
        harness.wait_for(lambda: service.snapshot()[1] > 0, seconds=3)
        assert not any(event[0] == "origin" for event in service.snapshot()[0])


def test_installed_browser_receipt_requires_a_separate_page_request(tmp_path):
    certs = certificates(tmp_path, dns_names=harness.DNS_NAMES)
    path = "/proof/" + "a" * 24 + "-https"
    with harness.FixtureService("http", certs) as service:
        fixture = service.fixture
        context = ssl.create_default_context(cafile=str(certs[0]))
        with httpx.Client(proxy=f"http://127.0.0.1:{fixture.proxy_port}", verify=context) as client:
            response = client.get("https://cpm-origin.invalid:443" + path)
            assert "fetch('/receipt/" in response.text
            events = service.snapshot()[0]
            assert harness.has_path(events, path)
            assert not harness.has_path(events, path.replace("/proof/", "/receipt/"))
        asyncio.run(fixture.tunnel(None, None, 1))  # Unrecognized ports never dial.


@pytest.mark.parametrize("relative", ["..", "../user-browser", "."])
def test_browser_trust_refuses_paths_outside_disposable_data(tmp_path, relative, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    run = Mock()
    monkeypatch.setattr(harness.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="escaped disposable data"):
        harness.trust_profile(data / relative, Path("unused"), data)
    run.assert_not_called()


@pytest.mark.parametrize("value", ["0.7.0", "156.0.1-beta.36", "0.6.0rc1"])
def test_public_versions_are_allowed(value):
    assert harness.public_version(value) == value


@pytest.mark.parametrize("value", [None, {"password": "private"}, "secret @ localhost", "x" * 90])
def test_private_or_unbounded_version_fields_never_enter_report(value):
    with pytest.raises(RuntimeError):
        harness.public_version(value)


def test_installed_failure_exports_only_fixed_phase_and_error_type(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(harness.sys, "argv", ["acceptance", "--output", str(output)])
    browser = Mock()
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda: browser))
    context = Mock()
    context.__enter__ = Mock(return_value=playwright)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(harness, "sync_playwright", lambda: context)
    monkeypatch.setattr(harness, "package_identity", lambda: {"format": ".dmg", "sha256": "0" * 64})

    def failed_device(*args):
        raise RuntimeError("password=synthetic-private-api-response")

    monkeypatch.setattr(harness, "Device", failed_device)
    monkeypatch.setattr(harness, "_PROCESSES", [])
    with pytest.raises(RuntimeError) as failure:
        harness.main()
    result = json.loads(output.read_text())
    assert result["passed"] is False and result["phase"] == "installed-startup"
    assert result["error_type"] == "RuntimeError" and result["cases"] == []
    assert "synthetic-private" not in str(failure.value) + output.read_text()
    browser.close.assert_called_once()


def test_installed_harness_refuses_normal_user_machines(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(harness.sys, "argv", ["acceptance"])
    launch = Mock()
    monkeypatch.setattr(harness, "sync_playwright", launch)
    with pytest.raises(SystemExit) as failure:
        harness.main()
    assert failure.value.code == 2
    launch.assert_not_called()
