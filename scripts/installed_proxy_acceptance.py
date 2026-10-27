#!/usr/bin/env python3
"""Disposable CI: installed manager API/UI and native proxy/TLS browser proof.

No test hooks or manager imports enter the frozen process. A CA is added only to
the disposable installed copy and synthetic browser profiles. No OS trust store,
ordinary application, user profile, upstream service or provider account is used.
Only an allowlisted summary is exported, never raw API results or desktop logs.
Run as ``python -m scripts.installed_proxy_acceptance`` from the repository root.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psutil
from playwright.sync_api import sync_playwright
from scripts.installed_sync_acceptance import (
    _PROCESSES,
    Device,
    InstalledAPIError,
    terminate_tree,
    wait_for,
)
from tests.support.proxy_protocols import ProxyFixture, certificates

HEALTH_NAMES = ("api.ipify.org", "checkip.amazonaws.com", "icanhazip.com")
DNS_NAMES = ("cpm-origin.invalid", *HEALTH_NAMES)
EXIT_IP = "8.8.8.8"  # Known by the paired browser's local GeoIP database.
CA_ALERTS = {
    "TLSV1_ALERT_UNKNOWN_CA",
    "SSLV3_ALERT_CERTIFICATE_UNKNOWN",
    "SSLV3_ALERT_BAD_CERTIFICATE",
}
CASES = (
    ("http", False),
    ("http", True),
    ("https", False),
    ("https", True),
    ("socks4", False),
    ("socks5", False),
    ("socks5", True),
)


class InstalledFixture(ProxyFixture):
    """Intercept fixed health names at standard ports, dial loopback only."""

    async def tunnel(self, reader, writer, port, initial=b""):
        port = {80: self.http_port, 443: self.https_port}.get(port, port)
        await super().tunnel(reader, writer, port, initial)

    async def origin(self, reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        line = headers.split(b"\r\n", 1)[0]
        self.events.append(("origin", line))
        target = urlsplit(line.split()[1].decode("ascii"))
        path = target.path
        host = next(
            (
                item[5:].strip().split(b":", 1)[0].decode("ascii")
                for item in headers.split(b"\r\n")
                if item.lower().startswith(b"host:")
            ),
            "",
        )
        if host in HEALTH_NAMES:
            body = EXIT_IP.encode("ascii")
        elif path.startswith("/gate/"):
            body = b"go" if path[6:] in getattr(self, "open_gates", set()) else b"wait"
        elif path.startswith("/monitor/") and getattr(self, "monitor", None):
            urls, nonce, frames = self.monitor
            if frames:
                probe = (
                    f"for(const url of {json.dumps(urls)}){{"
                    "const frame=document.createElement('iframe');frame.src=url;document.body.append(frame)}"
                )
            else:
                probe = (
                    f"fetch({json.dumps(urls[0])},{{cache:'no-store'}}).then(async response=>{{"
                    "if(!response.ok || await response.text()!=='CPM_PROXY_PROTOCOL_PROOF') throw Error();"
                    "}).then("
                    f"()=>fetch('/result/{nonce}/accepted'),"
                    f"()=>fetch('/result/{nonce}/rejected'));"
                )
            body = (
                "<!doctype html><meta charset=utf-8><title>CPM protocol observer</title><body><script>"
                "async function start(){"
                f"while(await (await fetch('/gate/{nonce}',{{cache:'no-store'}})).text()!=='go')"
                "await new Promise(resolve=>setTimeout(resolve,100));"
                + probe
                + "}start();</script>"
            ).encode("ascii")
        elif re.fullmatch(r"/proof/[a-f0-9]{24}-(?:http|https)", path):
            # A second, same-origin request proves the page body executed in
            # Camoufox; an API health request or a mere TLS connection cannot pass.
            receipt = path.replace("/proof/", "/receipt/")
            body = (
                "<!doctype html><meta charset=utf-8><title>CPM protocol proof</title>"
                f"<script>fetch('{receipt}',{{cache:'no-store'}}).catch(()=>{{}})</script>"
            ).encode("ascii")
        else:
            body = b"CPM_PROXY_PROTOCOL_PROOF"
        writer.write(
            b"HTTP/1.1 200 OK\r\nConnection: close\r\nCache-Control: no-store\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"Content-Type: text/html; charset=utf-8\r\nContent-Length: "
            + str(len(body)).encode("ascii")
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()


class FixtureService:
    """Own asynchronous fixtures on a separate loop from synchronous UI calls."""

    def __init__(self, protocol, certs, *, auth=False):
        self.fixture = InstalledFixture(protocol, certs, auth=auth)
        self.loop = asyncio.new_event_loop()
        self.loop.set_debug(True)  # asyncio reports failed inbound TLS handshakes.
        self.loop.slow_callback_duration = 5
        self.loop.set_exception_handler(self._exception)
        self.tls_failures = 0
        self.unexpected_errors = []
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def _exception(self, loop, context):
        error = context.get("exception")
        if isinstance(error, ssl.SSLError) and getattr(error, "reason", None) in CA_ALERTS:
            self.tls_failures += 1
        elif isinstance(error, (ConnectionResetError, BrokenPipeError)):
            pass  # A browser closes speculative sockets during normal shutdown.
        else:
            self.unexpected_errors.append(type(error).__name__)

    def run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=30)

    def __enter__(self):
        self.thread.start()
        try:
            self.run(self.fixture.start())
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def snapshot(self):
        async def read():
            return list(self.fixture.events), self.tls_failures

        return self.run(read())

    def reject(self):
        async def change():
            self.fixture.reject = True

        self.run(change())

    def monitor(self, urls, nonce, *, frames=False):
        async def change():
            self.fixture.monitor = (urls, nonce, frames)

        self.run(change())

    def open_gate(self, nonce):
        async def change():
            self.fixture.open_gates = {*getattr(self.fixture, "open_gates", set()), nonce}

        self.run(change())

    def __exit__(self, *_):
        async def close():
            try:
                await self.fixture.close()
            finally:
                # TLS handshakes can exist before a stream reaches the fixture's
                # handler registry. Drain those tasks on this dedicated loop too.
                tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            self.run(close())
            if self.unexpected_errors:
                raise RuntimeError("Unexpected fixture event-loop error")
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("Fixture loop did not stop")
            self.loop.close()


def trust_profile(directory, ca, data):
    """NSS trust must never escape this disposable manager's data directory."""
    directory = Path(directory).resolve()
    if not directory.is_relative_to(data.resolve()) or directory == data.resolve():
        raise RuntimeError("Browser trust path escaped disposable data")
    configured = os.environ.get("CPM_TEST_NSS_CERTUTIL")
    tool = configured or shutil.which("certutil")
    if not tool or (os.name == "nt" and not configured):
        raise RuntimeError("Explicit NSS certutil is required for installed TLS acceptance")
    directory.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": str(Path(tool).parent) + os.pathsep + os.environ.get("PATH", "")}
    for args in (
        ["-N", "-d", f"sql:{directory}", "--empty-password"],
        [
            "-A",
            "-d",
            f"sql:{directory}",
            "-n",
            "CPM disposable protocol CA",
            "-t",
            "C,,",
            "-i",
            str(ca),
        ],
    ):
        subprocess.run([tool, *args], env=env, check=True, capture_output=True, timeout=20)


def listener_ports(pid):
    if sys.platform == "darwin":
        result = subprocess.run(
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return {
            int(port) for port in re.findall(r"^n127\.0\.0\.1:(\d+)$", result.stdout, re.MULTILINE)
        }
    return {
        item.laddr.port
        for item in psutil.Process(pid).net_connections(kind="tcp")
        if item.status == psutil.CONN_LISTEN and item.laddr.ip == "127.0.0.1"
    }


@contextmanager
def launched(device, profile_id, arguments, *, fixed_geoip=False, monitor_proxy=None):
    before = listener_ports(device.process.pid)
    options = {
        "args": arguments,
        "firefox_user_prefs": {"network.proxy.allow_hijacking_localhost": False},
    }
    if fixed_geoip:
        # Refusal/certificate tests must reach the browser even when the earlier
        # manager health lookup would block it. Positive launches use the normal
        # fresh, verified exit-IP path without overriding geoip.
        options["geoip"] = EXIT_IP
    if monitor_proxy:
        # The observer page/result is a controlled loopback connection. The TLS
        # target still uses its synthetic hostname and must traverse the proxy.
        options["proxy"] = {"server": monitor_proxy, "bypass": "127.0.0.1"}
    try:
        device.call(
            f"/profiles/{profile_id}/launch", {"headless": True, "additional_options": options}
        )
        yield listener_ports(device.process.pid) - before
    finally:
        device.call("/browsers/close-all", {})
        wait_for(lambda: not device.call("/browsers/active")["active_browsers"])
        wait_for(lambda: listener_ports(device.process.pid) == before)


def profile_for(device, fixture, ca, report=None):
    profile = device.call(
        "/profiles",
        {
            "name": "Disposable protocol acceptance",
            "proxy_config": {
                "type": fixture.protocol,
                "server": f"127.0.0.1:{fixture.proxy_port}",
                "username": fixture.username if fixture.auth else None,
                "password": fixture.password if fixture.auth else None,
            },
        },
    )
    trust_profile(profile["storage_path"], ca, device.work / "data")
    # Every synthetic profile is backed up before any browser mutation. This
    # keeps the normal automatic scheduler enabled without a timing race at
    # an arbitrary minute boundary during protocol/refusal acceptance.
    prepare_profile_backup(device, profile["id"], report)
    return profile["id"]


def has_path(events, path):
    return any(
        event[0] == "origin" and urlsplit(event[1].split()[1].decode("ascii")).path == path
        for event in events
    )


def public_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,63}", value):
        raise RuntimeError("Installed version is not a public version identifier")
    return value


def package_identity():
    root = Path(__file__).resolve().parents[1] / "dist"
    pattern = (
        "*.dmg"
        if sys.platform == "darwin"
        else "*-setup.exe"
        if sys.platform == "win32"
        else "*.tar.gz"
    )
    packages = list(root.glob(pattern))
    if len(packages) != 1:
        raise RuntimeError("Expected exactly one native acceptance package")
    digest = hashlib.sha256()
    with packages[0].open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"format": packages[0].suffix, "sha256": digest.hexdigest()}


def positive_case(device, protocol, auth, certs, *, restart=False, report=None):
    with FixtureService(protocol, certs, auth=auth) as service:
        fixture = service.fixture
        profile_id = profile_for(device, fixture, certs[0], report)
        health = device.call(f"/profiles/{profile_id}/check-proxy", {})
        assert health["reachable"] and health["location"]["ip"] == EXIT_IP, (
            "Installed health check failed"
        )
        nonce = secrets.token_hex(12)
        paths = [f"/proof/{nonce}-{scheme}" for scheme in ("http", "https")]
        urls = [
            f"{scheme}://cpm-origin.invalid:{port}{path}"
            for scheme, port, path in zip(
                ("http", "https"), (fixture.http_port, fixture.https_port), paths, strict=True
            )
        ]
        service.monitor(urls, nonce, frames=True)
        arguments = ["-new-tab", f"http://127.0.0.1:{fixture.http_port}/monitor/{nonce}"]
        with launched(device, profile_id, arguments) as listeners:
            service.open_gate(nonce)
            wait_for(
                lambda: all(
                    has_path(service.snapshot()[0], path.replace("/proof/", "/receipt/"))
                    for path in paths
                )
            )
            if protocol == "socks4" or (protocol == "socks5" and auth):
                assert len(listeners) == 1, "Expected one session-owned relay"
        pinned = device.call(f"/profiles/{profile_id}")
        if restart:
            print(
                "Installed proxy acceptance: completing backup before restart",
                flush=True,
            )
            prepare_profile_backup(device, profile_id, report)
            device.close()
            device.start()
            device.login()
            persisted = device.call(f"/profiles/{profile_id}")
            assert persisted["fingerprint"] == pinned["fingerprint"], "Restart changed hardware"
            assert persisted["browser_settings"] == pinned["browser_settings"], (
                "Restart changed settings"
            )
            health = device.call(f"/profiles/{profile_id}/check-proxy", {})
            assert health["reachable"] and health["location"]["ip"] == EXIT_IP, (
                "Stored credentials did not survive restart"
            )
            # New receipts are essential: evidence from the first browser must
            # never satisfy the restarted profile's acceptance condition.
            nonce = secrets.token_hex(12)
            restarted_path = f"/proof/{nonce}-https"
            service.monitor(
                [f"https://cpm-origin.invalid:{fixture.https_port}{restarted_path}"],
                nonce,
                frames=True,
            )
            with launched(
                device,
                profile_id,
                ["-new-tab", f"http://127.0.0.1:{fixture.http_port}/monitor/{nonce}"],
            ) as listeners:
                service.open_gate(nonce)
                wait_for(
                    lambda: has_path(
                        service.snapshot()[0], restarted_path.replace("/proof/", "/receipt/")
                    )
                )
                assert len(listeners) == 1, "Restarted authenticated SOCKS5 relay is missing"
        service.reject()
        health = device.call(f"/profiles/{profile_id}/check-proxy", {})
        assert not health["reachable"], "Refused upstream was reported healthy"
        marker = len(service.snapshot()[0])
        nonce = secrets.token_hex(12)
        refused = f"/proof/{nonce}-http"
        service.monitor(
            [f"http://cpm-origin.invalid:{fixture.http_port}{refused}"], nonce, frames=True
        )
        with launched(
            device,
            profile_id,
            ["-new-tab", f"http://127.0.0.1:{fixture.http_port}/monitor/{nonce}"],
            fixed_geoip=True,
        ):
            service.open_gate(nonce)
            wait_for(
                lambda: any(
                    event[0] in {"rejected", "auth", "connect"}
                    for event in service.snapshot()[0][marker:]
                )
            )
        assert not has_path(service.snapshot()[0], refused), "Refused browser reached the origin"
        current = device.call(f"/profiles/{profile_id}")
        assert current["fingerprint"] == pinned["fingerprint"], "Proxy retry changed hardware"
        assert current["browser_settings"] == pinned["browser_settings"], (
            "Proxy retry changed settings"
        )
        return {
            "protocol": protocol,
            "authentication": "password" if auth else "none",
            "passed": True,
            "same_profile_restart": restart,
        }


def backup_skip_code(skipped, profile_id):
    """Fixed report codes from public backup failures, never their private text."""
    if not isinstance(skipped, list) or len(skipped) != 1:
        return "other"
    item = skipped[0]
    if not isinstance(item, dict) or item.get("profile_id") != profile_id:
        return "other"
    reason = item.get("reason")
    if not isinstance(reason, str):
        return "other"
    if re.fullmatch(
        rf"Profile {re.escape(profile_id)} is leased by another holder"
        r"(?: \([A-Za-z0-9_.:-]{1,512}\))?",
        reason,
    ):
        return "busy"
    if reason == "Profile data directory is missing; backup was not created":
        return "missing_data"
    if reason.startswith("Stored proxy credentials could not be decrypted. "):
        return "credentials"
    if reason.startswith("Cannot archive symbolic link: "):
        return "symbolic_link"
    if re.match(r"^\[Errno [0-9]{1,3}\] ", reason):
        return "filesystem"
    return "other"


def prepare_profile_backup(device, profile_id, report=None):
    # Use the public manual operation instead of waiting for the next automatic
    # scheduler tick. A successful response also means its profile lease has
    # been released. Only this additive preparation may retry a busy response;
    # accepted workflow/launch mutations are never replayed.
    diagnostics = {"attempts": 0, "busy_responses": 0, "prepared": False}
    if report is not None:
        report["backup_preparation"] = diagnostics

    def create():
        diagnostics["attempts"] += 1
        try:
            result = device.call("/system/backups", {"profile_ids": [profile_id]})["data"]
        except InstalledAPIError as error:
            if error.status == 409:
                diagnostics["busy_responses"] += 1
                return False
            raise
        diagnostics["prepared"] = any(
            item["profile_id"] == profile_id for item in result["backups"]
        )
        if not diagnostics["prepared"]:
            code = backup_skip_code(result.get("skipped"), profile_id)
            diagnostics["last_skip_code"] = code
            if code == "filesystem":
                match = re.match(r"^\[Errno ([0-9]{1,3})\] ", result["skipped"][0]["reason"])
                diagnostics["errno"] = int(match[1]) if match else None
            if code == "busy":
                # Per-profile admission can be returned as a partial 200 batch,
                # while whole-backup admission is a 409. Both are additive-only
                # preparation retries; never repeat a browser/workflow launch.
                diagnostics["busy_responses"] += 1
                return False
            # Export no arbitrary skip/error text, which may contain file paths
            # or secrets. A non-busy backup failure requires investigation.
            diagnostics["skipped"] = bool(result.get("skipped"))
            raise RuntimeError("Installed workflow backup preparation was skipped")
        return True

    # The preceding restart deliberately terminates the installed process. If
    # its automatic backup batch still owns the recovery lease, its normal
    # five-minute crash expiry must elapse. Observe public admission for up to
    # that TTL plus one scheduler interval; never clear/shorten a live lease.
    wait_for(create, seconds=360)


def workflow_diagnostics(run):
    states = {
        "queued",
        "running",
        "completed",
        "cancelled",
        "failed",
        "skipped",
        "denied",
        "interrupted",
        "missed",
    }
    categories = {
        "Profile is already open or busy. Close it before retrying.": "busy",
        "A selected profile changed. Reload and review the targets.": "changed",
        "Workflow access was revoked. Remaining steps were stopped.": "access",
        "Step failed. Check the profile, proxy and destination before retrying.": "execution",
        "Browser cleanup failed. Stop this profile before retrying.": "cleanup",
    }
    failure_codes = {
        "timeout",
        "browser_startup",
        "proxy_connection",
        "configuration",
        "filesystem",
        "session_unavailable",
        "operation_conflict",
        "unexpected",
    }

    def state(value):
        return value if isinstance(value, str) and value in states else "unknown"

    return {
        "state": state(run.get("state")),
        "targets": [
            {
                "state": state(row.get("state")),
                "category": categories.get(row.get("message"), "unknown"),
                "error_code": (
                    row.get("error_code") if row.get("error_code") in failure_codes else None
                ),
                "steps": [state(step.get("state")) for step in row.get("steps", [])[:12]],
            }
            for row in run.get("targets", [])[:100]
        ],
    }


def workflow_case(device, certs, report):
    """The installed workflow navigates only after authenticated launch readiness."""
    with FixtureService("https", certs, auth=True) as service:
        fixture = service.fixture
        profile_id = profile_for(device, fixture, certs[0], report)
        baseline = listener_ports(device.process.pid)
        nonce = secrets.token_hex(12)
        paths = [f"/proof/{nonce}-{scheme}" for scheme in ("http", "https")]
        steps = [{"name": "Start", "action": "launch"}]
        for scheme, port, path in zip(
            ("http", "https"), (fixture.http_port, fixture.https_port), paths, strict=True
        ):
            steps.extend(
                [
                    {
                        "name": "Controlled navigation",
                        "action": "navigate",
                        "url": f"{scheme}://cpm-origin.invalid:{port}{path}",
                    },
                    {"name": "Receipt window", "action": "wait", "seconds": 60},
                ]
            )
        steps.append({"name": "Finish", "action": "close"})
        workflow = device.call(
            "/workflows",
            {"name": "Disposable installed workflow", "steps": steps, "concurrency": 1},
        )
        profile = device.call(f"/profiles/{profile_id}")
        run = device.call(
            f"/workflows/{workflow['id']}/runs",
            {
                "request_id": str(uuid4()),
                "revision": workflow["revision"],
                "targets": [{"profile_id": profile_id, "row_version": profile["row_version"]}],
            },
        )
        route = f"/workflows/{workflow['id']}/runs/{run['id']}"
        try:

            def finished():
                current = device.call(route)
                report["workflow"] = workflow_diagnostics(current)
                return current["state"] not in {"queued", "running"}

            wait_for(finished, seconds=400)
            result = device.call(route)
            assert result["state"] == "completed", "Installed workflow did not complete"
            assert all(step["state"] == "completed" for step in result["targets"][0]["steps"])
            wait_for(
                lambda: all(
                    has_path(service.snapshot()[0], path.replace("/proof/", "/receipt/"))
                    for path in paths
                )
            )
            assert not device.call("/browsers/active")["active_browsers"]
            wait_for(lambda: listener_ports(device.process.pid) == baseline)
            history = json.dumps(device.call(f"/workflows/{workflow['id']}/runs"))
            assert fixture.password not in history and "cpm-origin.invalid" not in history
            return {
                "protocol": "https",
                "authentication": True,
                "completed_steps": len(steps),
                "http_receipt": True,
                "https_receipt": True,
                "sanitized_history": True,
                "relay_cleanup": True,
            }
        finally:
            device.call(f"/workflows/{workflow['id']}/runs/{run['id']}/cancel", {})
            device.call("/browsers/close-all", {})


def browser_tls_probe(device, protocol, certs, trusted_ca, *, accepted, report=None):
    with FixtureService(protocol, certs) as service:
        fixture = service.fixture
        profile_id = profile_for(device, fixture, trusted_ca, report)
        nonce = secrets.token_hex(12)
        path = f"/tls-probe/{nonce}"
        service.monitor([f"https://cpm-origin.invalid:{fixture.https_port}{path}"], nonce)
        # Start on a local observer page, not on an untrusted initial URL that
        # can obstruct Firefox startup before its automation channel is ready.
        # Both accepted and rejected TLS use the identical CORS/observer path.
        with launched(
            device,
            profile_id,
            ["-new-tab", f"http://127.0.0.1:{fixture.http_port}/monitor/{nonce}"],
            fixed_geoip=True,
            monitor_proxy=f"{protocol}://127.0.0.1:{fixture.proxy_port}",
        ):
            service.open_gate(nonce)
            result = f"/result/{nonce}/" + ("accepted" if accepted else "rejected")
            opposite = f"/result/{nonce}/" + ("rejected" if accepted else "accepted")
            wait_for(lambda: has_path(service.snapshot()[0], result))
            assert not has_path(service.snapshot()[0], opposite), (
                "TLS observer contradicted its result"
            )
        assert has_path(service.snapshot()[0], path) is accepted, (
            "TLS origin access contradicted browser result"
        )


def untrusted_case(device, protocol, certs, trusted_certs, *, report=None):
    with FixtureService(protocol, certs) as service:
        fixture = service.fixture
        profile_id = profile_for(device, fixture, trusted_certs[0], report)
        health = device.call(f"/profiles/{profile_id}/check-proxy", {})
        assert not health["reachable"], "Untrusted health TLS was accepted"

    browser_tls_probe(
        device, protocol, trusted_certs, trusted_certs[0], accepted=True, report=report
    )
    browser_tls_probe(device, protocol, certs, trusted_certs[0], accepted=False, report=report)
    return {
        "protocol": protocol,
        "untrusted_peer": "proxy" if protocol == "https" else "destination",
        "positive_control": True,
        "passed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("installed-proxy-results.json"))
    arguments = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Run only on disposable GitHub Actions workers")
    result = {
        "platform": sys.platform,
        "architecture": platform.machine(),
        "revision": os.environ.get("GITHUB_SHA", "unknown"),
        "passed": False,
        "manager": "installed-desktop-process",
        "ui": "bundled-web-via-chromium",
        "same_host": True,
        "same_os_account": True,
        "trust": "disposable-copy-and-synthetic-profile-only",
        "cases": [],
        "scope": "TCP-health-browser-TLS-refusal-manager-close-and-restart",
    }
    phase = "setup"
    device = None
    try:
        result["package"] = package_identity()
        result["build_wrapper"] = public_version(version("camoufox"))
        result["build_playwright"] = public_version(version("playwright"))
        with tempfile.TemporaryDirectory(prefix="installed-proxy-") as temporary:
            work = Path(temporary).resolve()
            certs = certificates(work, dns_names=DNS_NAMES)
            untrusted = work / "untrusted"
            untrusted.mkdir()
            other_certs = certificates(untrusted, dns_names=DNS_NAMES)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    phase = "installed-startup"
                    device = Device(work / "manager", browser, certs[0])
                    result["manager_version"] = public_version(
                        device.call("/system/info")["data"]["version"]
                    )
                    browser_status = device.call("/system/browser")["data"]
                    result["browser_version"] = public_version(browser_status["version"])
                    # Assert this is the packaged UI, not a dev server.
                    device.page.get_by_role("button", name="New profile", exact=True).wait_for()
                    for protocol, auth in CASES:
                        phase = protocol + ("-password" if auth else "-none")
                        print(f"Installed proxy acceptance: {phase}", flush=True)
                        result["cases"].append(
                            positive_case(
                                device,
                                protocol,
                                auth,
                                certs,
                                restart=protocol == "socks5" and auth,
                                report=result,
                            )
                        )
                    for protocol in ("http", "https"):
                        phase = protocol + "-untrusted"
                        result["cases"].append(
                            untrusted_case(device, protocol, other_certs, certs, report=result)
                        )
                    phase = "workflow-https-password"
                    print("Installed workflow acceptance: authenticated HTTPS proxy", flush=True)
                    result["workflow"] = workflow_case(device, certs, result)
                    result["passed"] = True
                finally:
                    with contextlib.ExitStack() as cleanup:
                        cleanup.callback(browser.close)
                        for process in _PROCESSES:
                            cleanup.callback(terminate_tree, process)
                        if device:
                            cleanup.callback(device.close)
    except BaseException as error:
        # No exception messages, API bodies, user IDs, paths or private logs.
        frames = []
        traceback = error.__traceback__
        while traceback:
            frames.append(
                {
                    "file": Path(traceback.tb_frame.f_code.co_filename).name,
                    "function": traceback.tb_frame.f_code.co_name,
                    "line": traceback.tb_lineno,
                }
            )
            traceback = traceback.tb_next
        result.update(
            passed=False, phase=phase, error_type=type(error).__name__, frames=frames[-8:]
        )
        if isinstance(error, InstalledAPIError):
            result["http_status"] = error.status
        raise RuntimeError(
            f"Installed proxy acceptance failed at {phase} ({type(error).__name__})"
        ) from None
    finally:
        # Device.start registers before login/account setup, so even a partially
        # constructed device is cleaned up. These are only our child processes.
        for process in reversed(_PROCESSES):
            terminate_tree(process)
        arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
