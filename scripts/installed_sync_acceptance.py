#!/usr/bin/env python3
"""Native CI only: two installed desktop processes, real UI and encrypted handoff.

Never imports manager code into either process. Only the synthetic relay runs in
this harness. Trust provisioning modifies disposable installed copies, never dist.
No secret-bearing traces, screenshots, request bodies or desktop logs are uploaded.
"""

import argparse
import contextlib
import datetime
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psutil
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.sync_api import expect, sync_playwright
from pydantic import SecretStr

from camoufox_pm.sync.relay import create_relay_app

ROOT = Path(__file__).resolve().parents[1]
NAME = "Installed sync acceptance"
_PROCESSES: list[subprocess.Popen] = []


class InstalledAPIError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__("Installed API rejected the operation")


def tls_fixture(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ephemeral native CI relay")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate, private = directory / "relay.pem", directory / "relay.key"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    return certificate, private


def install_copy(destination):
    destination.mkdir()
    if sys.platform == "darwin":
        image = next((ROOT / "dist").glob("*.dmg"))
        mount = destination / "mount"
        subprocess.run(
            ["hdiutil", "attach", str(image), "-nobrowse", "-mountpoint", str(mount)],
            check=True,
            capture_output=True,
        )
        try:
            subprocess.run(
                [
                    "ditto",
                    str(mount / "Camoufox Profile Manager.app"),
                    str(destination / "Manager.app"),
                ],
                check=True,
            )
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, capture_output=True)
        return destination / "Manager.app" / "Contents" / "MacOS" / "camoufox-pm"
    if sys.platform == "win32":
        installer = next((ROOT / "dist").glob("*-setup.exe"))
        subprocess.run([str(installer), "/S", f"/D={destination}"], check=True, timeout=180)
        return destination / "camoufox-pm.exe"
    archive = next((ROOT / "dist").glob("*.tar.gz"))
    # This is the artifact built in this job, not user-supplied profile input.
    with tarfile.open(archive) as package:
        package.extractall(destination, filter="data")
    return destination / "camoufox-pm" / "camoufox-pm"


def provision_test_trust(installation, certificate):
    stores = {
        path.resolve() for path in installation.rglob("cacert.pem") if path.parent.name == "certifi"
    }
    if not stores or any(not path.is_relative_to(installation.resolve()) for path in stores):
        raise RuntimeError("Installed TLS trust store could not be safely located")
    for path in stores:
        with path.open("ab") as stream:
            stream.write(b"\n" + certificate.read_bytes())
    if sys.platform == "darwin":
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(installation / "Manager.app")],
            check=True,
            capture_output=True,
        )


def terminate_tree(process):
    if process.poll() is not None:
        return
    with contextlib.suppress(psutil.NoSuchProcess):
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for item in [*reversed(children), parent]:
            with contextlib.suppress(psutil.NoSuchProcess):
                item.terminate()
        _, alive = psutil.wait_procs([*children, parent], timeout=10)
        for item in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                item.kill()


def wait_for(check, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError("Installed acceptance condition timed out")


def cleanup_native_slots(work):
    """Delete only UUID slots recorded by these disposable installations."""
    from camoufox_pm.sync.control import ControlStore

    if sys.platform == "darwin" and os.environ.get("CPM_CI_ISOLATED_KEYCHAIN") == "1":
        # The parent owns this dedicated keychain and deletes it as a whole after
        # every installed process has exited. No cross-executable secret access.
        return
    slots = set()
    for name in ("owner", "recipient"):
        directory = work / name / "data" / "sync-control"
        if not directory.is_dir():
            continue
        control = ControlStore(directory)
        record = control.get("installation")
        if record and record.payload.get("vault_kind") == "native":
            state = record.payload
            owner_id = state["root"]["owner_id"]
            slots.add((owner_id, state["device_id"]))
            if state.get("authority", True):
                slots.add((owner_id, owner_id))
        draft = control.get("ceremony/pairing")
        if draft and draft.payload.get("vault_kind") == "native":
            state = draft.payload
            slots.add((state["root"]["owner_id"], state["request"]["device"]["device_id"]))
    with contextlib.ExitStack() as cleanup:
        for owner_id, slot in slots:
            cleanup.callback(delete_native_slot, owner_id, slot)
    print("Installed acceptance: disposable native credential slots removed", flush=True)


def delete_native_slot(owner_id, slot):
    from camoufox_pm.sync.vault import OSVault

    OSVault(owner_id).delete(slot)


class Device:
    def __init__(self, work, browser, certificate, vault_kind="encrypted_file"):
        self.work = work
        self.browser = browser
        self.vault_kind = vault_kind
        self.password = secrets.token_urlsafe(24)
        work.mkdir(mode=0o700)
        install = work / "installed"
        executable = install_copy(install)
        provision_test_trust(install, certificate)
        data = work / "data"
        data.mkdir(mode=0o700)
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CPM_") and k not in {"PYTHONPATH", "PYTHONHOME"}
        }
        environment.update(CPM_DB_PATH=str(data / "profiles.db"), PYTHONIOENCODING="utf-8")
        self.executable = executable
        self.environment = environment
        self.start()
        self.call(
            "/access/users",
            {"username": "ci-admin", "password": self.password, "role": "administrator"},
        )
        self.login()

    def start(self):
        self.process = subprocess.Popen(
            [str(self.executable)],
            cwd=self.work,
            env=self.environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _PROCESSES.append(self.process)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_default_timeout(120000)

        def address():
            if self.process.poll() is not None:
                raise RuntimeError("Installed desktop exited before startup")
            if sys.platform == "darwin":
                # uvicorn omits the URL log when desktop supplies its socket.
                # lsof can inspect this same-user process without root on macOS.
                output = subprocess.run(
                    [
                        "lsof",
                        "-nP",
                        "-a",
                        "-p",
                        str(self.process.pid),
                        "-iTCP",
                        "-sTCP:LISTEN",
                        "-Fn",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout
                ports = re.findall(r"^n127\.0\.0\.1:(\d+)$", output, re.MULTILINE)
            else:
                ports = [
                    str(item.laddr.port)
                    for item in psutil.Process(self.process.pid).net_connections(kind="tcp")
                    if item.status == psutil.CONN_LISTEN and item.laddr.ip == "127.0.0.1"
                ]
            for port in ports:
                candidate = "http://127.0.0.1:" + port
                try:
                    response = self.context.request.get(
                        candidate + "/api/v1/auth/session", timeout=1000
                    )
                    if response.ok and "user_auth_enabled" in response.json():
                        return candidate
                except Exception:
                    pass
            return None

        self.origin = wait_for(address)

    def login(self):
        self.call("/auth/login", {"username": "ci-admin", "password": self.password})
        self.page.goto(self.origin + "/")

    def restart(self):
        before = self.call("/sync/status")
        self.close()
        self.start()
        self.login()
        after = self.call("/sync/status")
        assert after == before, "Enrollment changed across installed-manager restart"

    def check_unavailable_linux_vault(self):
        if self.vault_kind != "native" or sys.platform != "linux":
            return
        print("Installed acceptance: unavailable Secret Service blocks key access", flush=True)
        original = self.environment
        self.environment = {
            **original,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(self.work / "missing-bus"),
        }
        try:
            self.restart()
            # Stop bundled-UI refreshes before probing the deliberate failure.
            self.page.goto("about:blank")
            self.call("/sync/status")
            response = self.context.request.post(
                self.origin + "/api/v1/sync/recovery-package",
                data={},
                headers={"Origin": self.origin},
                timeout=20000,
            )
            assert response.status == 409
            assert response.json() == {
                "detail": "Synchronization ceremony failed; check setup and unlock credentials"
            }
            assert response.headers.get("cache-control") == "no-store"
            assert self.call("/sync/status")["vault_kind"] == "native"
            assert not list((self.work / "data").rglob("keys-*"))
        finally:
            self.environment = original
            self.restart()

    @property
    def unlock_body(self):
        return {"password": self.password} if self.vault_kind == "encrypted_file" else {}

    def unlock(self, dialog):
        field = dialog.get_by_label("Unlock password", exact=True)
        if self.vault_kind == "encrypted_file":
            field.fill(self.password)
        else:
            expect(field).to_have_count(0)

    def assert_password_cleared(self, dialog):
        field = dialog.get_by_label("Unlock password", exact=True)
        if self.vault_kind == "encrypted_file":
            expect(field).to_have_value("")
        else:
            expect(field).to_have_count(0)

    def complete_close_upload(self, profile_id):
        if self.vault_kind == "encrypted_file":
            self.action("Resume pending transfer", "Ready for another device")
            return
        # Native keys permit the ordinary background queue to finish without a
        # password or manual retry. Read public state, never invoke handoff here.
        wait_for(
            lambda: any(
                item["local_id"] == profile_id and item["phase"] == "released"
                for item in self.call("/sync/profiles")["profiles"]
            ),
            seconds=180,
        )
        expect(self.panel().get_by_text("Ready for another device", exact=True)).to_be_visible()

    def call(self, path, body=None):
        deadline = time.monotonic() + 10
        while True:
            response = self.context.request.fetch(
                self.origin + "/api/v1" + path,
                method="GET" if body is None else "POST",
                data=body,
                headers={"Origin": self.origin},
                timeout=180000,
            )
            # A bundled-UI status refresh may briefly hold ceremony admission.
            # Retry only this explicit busy response for read-only requests.
            # Never replay mutations or hide a credential/verification failure.
            if (
                body is None
                and response.status == 409
                and time.monotonic() < deadline
                and response.json().get("detail")
                == "Another synchronization ceremony is in progress"
            ):
                time.sleep(0.2)
                continue
            if not response.ok:
                raise InstalledAPIError(response.status)
            return response.json()

    def panel(self):
        self.page.goto(self.origin + "/")
        self.page.get_by_role("button", name="Actions for " + NAME, exact=True).click()
        self.page.get_by_role("menuitem", name="Device synchronization", exact=True).click()
        return self.page.get_by_role("dialog", name="Synchronize " + NAME, exact=True)

    def action(self, label, expected, *, enable=False):
        dialog = self.panel()
        self.unlock(dialog)
        if enable:
            dialog.get_by_label("Allow my trusted devices to receive", exact=False).check()
        if label == "Resume pending transfer":
            expect(dialog.get_by_text("Handoff in progress", exact=True)).to_be_visible()
        dialog.get_by_role("button", name=label, exact=True).click()
        if label == "Send to another device":
            self.page.get_by_role("dialog").filter(
                has_text="Send this profile to another device?"
            ).get_by_role("button", name="Send profile", exact=True).click()
        expect(dialog.get_by_text(expected, exact=True)).to_be_visible()

    def close(self):
        with contextlib.suppress(Exception):
            self.call("/browsers/close-all", {})
        self.context.close()
        terminate_tree(self.process)


class Probe(http.server.BaseHTTPRequestHandler):
    reports: dict[str, dict] = {}

    def log_message(self, *_args):
        pass

    def do_GET(self):
        stage = parse_qs(urlsplit(self.path).query).get("stage", [""])[0]
        if stage not in {"first", "second", "return", "restored"}:
            self.send_error(404)
            return
        authenticated = "installed-auth=synthetic" in self.headers.get("Cookie", "")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if stage == "first":
            self.send_header(
                "Set-Cookie",
                "installed-auth=synthetic; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400",
            )
        self.end_headers()
        page = """<!doctype html><script>
(async()=>{
const stage=STAGE;
if(stage==='first')localStorage.setItem('continuity','first');
const state=localStorage.getItem('continuity');
if(stage==='second')localStorage.setItem('continuity','second');
const c=document.createElement('canvas');c.width=240;c.height=60;
const x=c.getContext('2d');x.textBaseline='top';x.font='16px Arial';
x.fillStyle='#f60';x.fillRect(0,0,120,25);x.fillStyle='#069';x.fillText('canvas probe',2,18);
const png=c.toDataURL();const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(png)))).map(x=>x.toString(16).padStart(2,'0')).join('');
const gl=document.createElement('canvas').getContext('webgl');const ext=gl.getExtension('WEBGL_debug_renderer_info');
await fetch('/report?stage='+stage,{method:'POST',body:JSON.stringify({state,canvas:hash,identity:{ua:navigator.userAgent,platform:navigator.platform,cores:navigator.hardwareConcurrency,screen:screen.width+'x'+screen.height,gpu:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null},httpOnly:!document.cookie.includes('installed-auth'),authenticated:AUTH})});
})().catch(()=>fetch('/report?stage='+STAGE,{method:'POST',body:JSON.stringify({error:true})}));
</script>""".replace("STAGE", json.dumps(stage)).replace(
            "AUTH", json.dumps(authenticated or stage == "first")
        )
        self.wfile.write(page.encode())

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size < 16384:
            self.send_error(400)
            return
        stage = parse_qs(urlsplit(self.path).query).get("stage", [""])[0]
        value = json.loads(self.rfile.read(size))
        value["authenticated"] = "installed-auth=synthetic" in self.headers.get("Cookie", "")
        self.reports[stage] = value
        self.send_response(204)
        self.end_headers()


def observe(device, profile_id, site, stage):
    device.call(
        f"/profiles/{profile_id}/launch",
        {
            "headless": True,
            "additional_options": {
                "geoip": "8.8.8.8",
                "args": ["-new-tab", site + "/account?stage=" + stage],
            },
        },
    )
    try:
        result = wait_for(lambda: Probe.reports.get(stage), seconds=120)
        assert not result.get("error") and result["httpOnly"] and result["authenticated"]
        return result
    finally:
        device.call("/browsers/close-all", {})


def exercise(owner, recipient, relay_url, registration, site):
    print("Installed acceptance: owner recovery and relay registration", flush=True)
    password = owner.unlock_body
    owner.call(
        "/sync/enrollment",
        {
            "relay_url": relay_url,
            "device_label": "Installed owner",
            "vault_kind": owner.vault_kind,
            **password,
        },
    )
    package = owner.call("/sync/recovery-package", password)
    owner.call("/sync/confirm-recovery", {**package, **password})
    owner.call("/sync/relay/connect", {"registration_token": registration, **password})
    assert owner.call("/sync/status")["vault_kind"] == owner.vault_kind
    print("Installed acceptance: owner restart and persistent key access", flush=True)
    owner.restart()
    owner.check_unavailable_linux_vault()
    # The subsequent profile opt-in and pairing approval must load persisted keys.
    original = owner.call(
        "/profiles",
        {"name": NAME, "generate_fingerprint": False, "browser_settings": {"stable_canvas": True}},
    )
    local_id = original["id"]
    print("Installed acceptance: first native Camoufox launch", flush=True)
    before = observe(owner, local_id, site, "first")
    pinned = owner.call(f"/profiles/{local_id}")
    assert pinned["fingerprint"] and pinned["browser_settings"]["stable_canvas"]
    print("Installed acceptance: UI profile opt-in", flush=True)
    owner.action("Enable synchronization for this profile", "Ready on this device", enable=True)
    print("Installed acceptance: device pairing", flush=True)
    root = owner.call("/sync/pairing/owner-code")
    prepared = recipient.call(
        "/sync/pairing/prepare",
        {
            "root": root,
            "device_label": "Installed recipient",
            "vault_kind": recipient.vault_kind,
            **recipient.unlock_body,
        },
    )
    approval = owner.call(
        "/sync/pairing/approve",
        {
            "request": prepared["request"],
            "confirmed_fingerprint": prepared["fingerprint"],
            **password,
        },
    )
    recipient.call(
        "/sync/pairing/accept",
        {
            "approval": approval,
            "confirmed_fingerprint": prepared["fingerprint"],
            **recipient.unlock_body,
        },
    )
    assert recipient.call("/sync/status")["vault_kind"] == recipient.vault_kind
    print("Installed acceptance: recipient restart and persistent key access", flush=True)
    recipient.restart()
    print("Installed acceptance: owner UI handoff", flush=True)
    owner.action("Send to another device", "Ready for another device")
    recipient.page.goto(recipient.origin + "/")
    recipient.page.get_by_role("button", name="Profiles from trusted devices", exact=True).click()
    dialog = recipient.page.get_by_role("dialog", name="Profiles from trusted devices", exact=True)
    recipient.unlock(dialog)
    dialog.get_by_role("button", name="Add trusted profile to this device").click()
    expect(dialog.get_by_text("Sync status needs verification", exact=True)).to_be_visible()
    recipient.assert_password_cleared(dialog)
    recipient.unlock(dialog)
    dialog.get_by_role("button", name="Get latest profile", exact=True).click()
    expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()
    imported = recipient.call("/sync/profiles")["profiles"][0]["local_id"]
    assert imported != local_id
    transferred = recipient.call(f"/profiles/{imported}")
    assert transferred["fingerprint"] == pinned["fingerprint"]
    assert transferred["browser_settings"] == pinned["browser_settings"]
    assert transferred["storage_path"] != pinned["storage_path"]
    print("Installed acceptance: recipient native Camoufox reopen", flush=True)
    after = observe(recipient, imported, site, "second")
    assert before == after, "Installed profile session or identity changed during transfer"
    print("Installed acceptance: recipient UI handoff", flush=True)
    recipient.complete_close_upload(imported)
    owner.action("Get latest profile", "Ready on this device")
    print("Installed acceptance: owner native Camoufox return", flush=True)
    final = observe(owner, local_id, site, "return")
    assert final.pop("state") == "second"
    before.pop("state")
    assert final == before, "Installed profile identity changed on return"
    restored = owner.call(f"/profiles/{local_id}")
    assert restored["fingerprint"] == pinned["fingerprint"]
    assert restored["browser_settings"] == pinned["browser_settings"]

    print("Installed acceptance: synchronized Trash and restore UI", flush=True)
    owner.complete_close_upload(local_id)
    owner.action("Get latest profile", "Ready on this device")
    dialog = owner.panel()
    owner.unlock(dialog)
    dialog.get_by_role("button", name="Move synchronized profile to Trash", exact=True).click()
    owner.page.get_by_role(
        "dialog", name="Move synchronized profile to Trash?", exact=True
    ).get_by_role("button", name="Publish deletion", exact=True).click()
    expect(dialog.get_by_text("Synchronized profile is in Trash", exact=True)).to_be_visible()
    owner.assert_password_cleared(dialog)
    owner.page.goto(owner.origin + "/trash/")
    row = owner.page.get_by_role("row").filter(has_text=NAME)
    expect(row.get_by_role("button", name="Restore", exact=True)).to_be_disabled()
    expect(
        row.get_by_role("button", name="Permanently delete " + NAME, exact=True)
    ).to_be_disabled()
    row.get_by_role("button", name="Device synchronization", exact=True).click()
    dialog = owner.page.get_by_role("dialog", name="Synchronize " + NAME, exact=True)
    owner.unlock(dialog)
    dialog.get_by_role("button", name="Restore synchronized profile", exact=True).click()
    owner.page.get_by_role("dialog", name="Restore synchronized profile?", exact=True).get_by_role(
        "button", name="Restore and publish", exact=True
    ).click()
    expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()
    owner.assert_password_cleared(dialog)

    print("Installed acceptance: historical cleanup schedule and cancellation UI", flush=True)
    dialog = owner.panel()
    dialog.get_by_text("Historical storage cleanup", exact=True).click()
    owner.unlock(dialog)
    dialog.get_by_role("button", name="Load verified historical versions", exact=True).click()
    versions = dialog.get_by_role("group", name="Old encrypted versions", exact=True)
    expect(versions).to_be_visible()
    owner.assert_password_cleared(dialog)
    versions.get_by_role("checkbox").first.check()
    owner.unlock(dialog)
    dialog.get_by_role("button", name="Schedule selected cleanup", exact=True).click()
    owner.page.get_by_role(
        "dialog", name="Schedule permanent historical cleanup?", exact=True
    ).get_by_role("button", name="Sign cleanup decision", exact=True).click()
    expect(dialog.get_by_text("Cleanup scheduled", exact=True)).to_be_visible()
    owner.assert_password_cleared(dialog)
    owner.unlock(dialog)
    dialog.get_by_role("button", name="Cancel historical cleanup", exact=True).click()
    expect(dialog.get_by_text("Cleanup cancelled", exact=True)).to_be_visible()
    owner.assert_password_cleared(dialog)
    print("Installed acceptance: restored native Camoufox fourth launch", flush=True)
    recovered = observe(owner, local_id, site, "restored")
    assert recovered.pop("state") == "second"
    assert recovered == before, "Installed profile session or identity changed after Trash restore"
    restored = owner.call(f"/profiles/{local_id}")
    assert restored["fingerprint"] == pinned["fingerprint"]
    assert restored["browser_settings"] == pinned["browser_settings"]
    if owner.vault_kind == "native":
        owner.complete_close_upload(local_id)
        for device in (owner, recipient):
            assert not list((device.work / "data").rglob("keys-*")), (
                "Unexpected file-vault fallback"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("installed-sync-results.json"))
    parser.add_argument("--vault", choices=("native", "encrypted_file"), default="encrypted_file")
    arguments = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error(
            "This installer acceptance harness runs only on disposable GitHub Actions workers"
        )
    if (
        arguments.vault == "native"
        and sys.platform == "darwin"
        and os.environ.get("CPM_CI_ISOLATED_KEYCHAIN") != "1"
    ):
        parser.error("Use run_with_ci_keychain.py for disposable macOS native acceptance")
    phase = "setup"
    result = {
        "platform": sys.platform,
        "passed": False,
        "manager": "two-installed-desktop-processes",
        "ui": "bundled-web-via-chromium",
        "browser": "upstream-paired-camoufox",
        "vault": arguments.vault,
        "restart": "both-installed-processes-with-existing-data-and-keys",
        "close_upload": "automatic" if arguments.vault == "native" else "explicit-password-resume",
        "same_host": True,
        "same_os_account": True,
        "unavailable_vault": "tested"
        if arguments.vault == "native" and sys.platform == "linux"
        else "not-tested",
        "onboarding": "real-administrator-cookie-API",
        "handoff": "real-bundled-UI",
        "lifecycle": "UI-trash-restore-fourth-browser-launch",
        "retention": "UI-schedule-and-cancel-no-collection",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="installed-sync-") as temporary:
            work = Path(temporary).resolve()
            certificate, private = tls_fixture(work)
            registration = secrets.token_urlsafe(32)
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            relay_url = f"https://127.0.0.1:{listener.getsockname()[1]}"
            relay = uvicorn.Server(
                uvicorn.Config(
                    create_relay_app(
                        work / "relay",
                        relay_url=relay_url,
                        registration_token=SecretStr(registration),
                    ),
                    ssl_certfile=str(certificate),
                    ssl_keyfile=str(private),
                    log_level="error",
                    access_log=False,
                )
            )
            thread = threading.Thread(target=relay.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            wait_for(lambda: relay.started)
            site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Probe)
            website = threading.Thread(target=site.serve_forever, daemon=True)
            website.start()
            devices = []
            try:
                with sync_playwright() as playwright:
                    # Native KDF + profile capture/upload routinely exceeds the
                    # five-second assertion default used by mocked UI tests.
                    expect.set_options(timeout=120000)
                    browser = playwright.chromium.launch()
                    try:
                        phase = "installed-startup"
                        for name in ("owner", "recipient"):
                            devices.append(
                                Device(work / name, browser, certificate, arguments.vault)
                            )
                        phase = "UI-encrypted-roundtrip"
                        exercise(
                            *devices,
                            relay_url,
                            registration,
                            f"http://127.0.0.1:{site.server_port}",
                        )
                        result["passed"] = True
                    finally:
                        # Execute every cleanup even when another one fails.
                        with contextlib.ExitStack() as cleanup:
                            if arguments.vault == "native":
                                cleanup.callback(cleanup_native_slots, work)
                            cleanup.callback(browser.close)
                            for process in _PROCESSES:
                                cleanup.callback(terminate_tree, process)
                            for device in devices:
                                cleanup.callback(device.close)
            finally:
                site.shutdown()
                site.server_close()
                relay.should_exit = True
                thread.join(timeout=10)
                listener.close()
    except BaseException as exc:
        frames = []
        traceback = exc.__traceback__
        while traceback is not None:
            code = traceback.tb_frame.f_code
            # Coordinates only: never capture exception messages, locals,
            # request bodies, page contents or private desktop logs.
            frames.append(
                {
                    "file": Path(code.co_filename).name,
                    "function": code.co_name,
                    "line": traceback.tb_lineno,
                }
            )
            traceback = traceback.tb_next
        result.update(passed=False, phase=phase, error_type=type(exc).__name__, frames=frames[-12:])
        if isinstance(exc, InstalledAPIError):
            result["http_status"] = exc.status
        raise RuntimeError(
            f"Installed synchronization acceptance failed at {phase} ({type(exc).__name__}); private diagnostics not exported"
        ) from None
    finally:
        for process in reversed(_PROCESSES):
            terminate_tree(process)
        arguments.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
