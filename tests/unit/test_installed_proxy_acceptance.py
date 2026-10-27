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


@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
def test_installed_browser_receipt_requires_a_separate_page_request(tmp_path, scheme, port):
    certs = certificates(tmp_path, dns_names=harness.DNS_NAMES)
    path = "/proof/" + "a" * 24 + "-https"
    with harness.FixtureService("http", certs) as service:
        fixture = service.fixture
        context = ssl.create_default_context(cafile=str(certs[0]))
        with httpx.Client(proxy=f"http://127.0.0.1:{fixture.proxy_port}", verify=context) as client:
            response = client.get(f"{scheme}://cpm-origin.invalid:{port}" + path)
            assert "fetch('/receipt/" in response.text
            events = service.snapshot()[0]
            assert harness.has_path(events, path)
            assert not harness.has_path(events, path.replace("/proof/", "/receipt/"))
            receipt = path.replace("/proof/", "/receipt/")
            client.get(f"{scheme}://cpm-origin.invalid:{port}" + receipt)
            assert harness.has_path(service.snapshot()[0], receipt)
        asyncio.run(fixture.tunnel(None, None, 1))  # Unrecognized ports never dial.


def test_observer_gate_requires_explicit_launch_acknowledgment_and_a_new_nonce(tmp_path):
    certs = certificates(tmp_path, dns_names=harness.DNS_NAMES)
    nonce = "a" * 24
    with harness.FixtureService("http", certs) as service:
        fixture = service.fixture
        service.monitor(
            [f"https://cpm-origin.invalid:{fixture.https_port}/proof/{nonce}-https"],
            nonce,
            frames=True,
        )
        with httpx.Client(trust_env=False) as client:
            base = f"http://127.0.0.1:{fixture.http_port}"
            assert client.get(base + f"/gate/{nonce}").text == "wait"
            page = client.get(base + f"/monitor/{nonce}")
            assert page.headers["access-control-allow-origin"] == "*"
            assert f"/gate/{nonce}" in page.text
            service.open_gate(nonce)
            assert client.get(base + f"/gate/{nonce}").text == "go"
            assert client.get(base + "/gate/" + "b" * 24).text == "wait"


def test_new_protocol_profiles_finish_public_backup_before_any_launch(tmp_path, monkeypatch):
    calls = []

    def call(path, body):
        calls.append(path)
        if path == "/profiles":
            return {"id": "target", "storage_path": str(tmp_path / "data" / "target")}
        assert path == "/system/backups" and body == {"profile_ids": ["target"]}
        return {"data": {"backups": [{"profile_id": "target"}]}}

    monkeypatch.setattr(harness, "trust_profile", lambda *_args: calls.append("trust"))
    fixture = SimpleNamespace(protocol="http", proxy_port=8080, auth=False)
    device = SimpleNamespace(call=call, work=tmp_path)
    assert harness.profile_for(device, fixture, Path("unused")) == "target"
    assert calls == ["/profiles", "trust", "/system/backups"]


def test_workflow_preparation_retries_only_additive_busy_backup(monkeypatch):
    calls = []
    wait = harness.wait_for

    def bounded_wait(check, seconds):
        assert seconds == 360  # Crash lease TTL (300) plus scheduler interval.
        return wait(check, seconds=3)

    monkeypatch.setattr(harness, "wait_for", bounded_wait)

    def call(path, body):
        calls.append((path, body))
        if len(calls) == 1:
            raise harness.InstalledAPIError(409)
        return {"data": {"backups": [{"profile_id": "target"}]}}

    report = {}
    harness.prepare_profile_backup(SimpleNamespace(call=call), "target", report)
    assert calls == [("/system/backups", {"profile_ids": ["target"]})] * 2
    assert report == {"backup_preparation": {"attempts": 2, "busy_responses": 1, "prepared": True}}

    def forbidden(path, body):
        raise harness.InstalledAPIError(403)

    with pytest.raises(harness.InstalledAPIError):
        harness.prepare_profile_backup(SimpleNamespace(call=forbidden), "target")

    def skipped(path, body):
        return {"data": {"backups": [], "skipped": [{"reason": "private secret file"}]}}

    report = {}
    with pytest.raises(RuntimeError):
        harness.prepare_profile_backup(SimpleNamespace(call=skipped), "target", report)
    assert "private secret file" not in json.dumps(report)
    assert report["backup_preparation"]["skipped"] is True


@pytest.mark.parametrize("holder", [None, "ci-host:123:9d0f5ae4-12ba-4fc4-b6cf-bf5aae58bd84"])
def test_backup_preparation_recognizes_partial_busy_response_without_exporting_holder(
    monkeypatch, holder
):
    from camoufox_pm.core.leases import ProfileLocked

    calls = []

    def call(path, body):
        assert path == "/system/backups" and body == {"profile_ids": ["target"]}
        calls.append(path)
        if len(calls) == 1:
            return {
                "data": {
                    "backups": [],
                    "skipped": [
                        {"profile_id": "target", "reason": str(ProfileLocked("target", holder))}
                    ],
                }
            }
        return {"data": {"backups": [{"profile_id": "target"}]}}

    def bounded_wait(check, seconds):
        assert seconds == 360
        assert check() is False
        assert check() is True

    monkeypatch.setattr(harness, "wait_for", bounded_wait)
    report = {}
    harness.prepare_profile_backup(SimpleNamespace(call=call), "target", report)
    assert len(calls) == 2
    assert report["backup_preparation"] == {
        "attempts": 2,
        "busy_responses": 1,
        "prepared": True,
        "last_skip_code": "busy",
    }
    assert "ci-host" not in json.dumps(report)


def test_backup_skip_diagnostics_do_not_retry_or_export_unknown_private_reason(monkeypatch):
    calls = []
    secret = "Profile target is leased by another holder (private-user:password@host/path)"

    def call(*_args):
        calls.append(True)
        return {"data": {"backups": [], "skipped": [{"profile_id": "target", "reason": secret}]}}

    report = {}
    with pytest.raises(RuntimeError):
        harness.prepare_profile_backup(SimpleNamespace(call=call), "target", report)
    assert len(calls) == 1
    assert report["backup_preparation"]["last_skip_code"] == "other"
    assert "private-" not in json.dumps(report)


def test_backup_filesystem_failure_retains_only_fixed_code_and_errno():
    def call(*_args):
        return {
            "data": {
                "backups": [],
                "skipped": [{"profile_id": "target", "reason": "[Errno 13] private-path-token"}],
            }
        }

    report = {}
    with pytest.raises(RuntimeError):
        harness.prepare_profile_backup(SimpleNamespace(call=call), "target", report)
    assert report["backup_preparation"]["last_skip_code"] == "filesystem"
    assert report["backup_preparation"]["errno"] == 13
    assert "private-" not in json.dumps(report)


def test_workflow_diagnostics_allowlist_states_without_identity_or_error_text():
    private = "private-password-and-destination"
    result = harness.workflow_diagnostics(
        {
            "id": private,
            "state": "failed",
            "request": private,
            "targets": [
                {
                    "profile_id": private,
                    "state": "failed",
                    "message": private,
                    "error_code": private,
                    "steps": [{"state": private, "name": private, "url": private}],
                },
                {
                    "state": "skipped",
                    "message": "Profile is already open or busy. Close it before retrying.",
                    "steps": [],
                },
            ],
        }
    )
    assert private not in json.dumps(result)
    assert result["targets"][0]["steps"] == ["unknown"]
    assert result["targets"][0]["error_code"] is None
    assert result["targets"][1]["category"] == "busy"


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
