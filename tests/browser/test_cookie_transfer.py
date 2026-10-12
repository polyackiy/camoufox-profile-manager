"""Actual Firefox cookie behavior using synthetic local origins only."""

import json
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from camoufox_pm.core.backups import ProfileBackupManager
from camoufox_pm.core.cookie_transfer import CookieTransfer, parse_cookies


@contextmanager
def cookie_site():
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers.get("Cookie", "")))
            body = b"<!doctype html><title>Cookie proof</title><p>Synthetic localhost proof</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def launch_page(manager, profile_id, url):
    await manager.launch_browser(
        profile_id,
        headless=True,
        geoip="8.8.8.8",
        # Synthetic DNS names resolve to our loopback HTTP server. A domain
        # cookie for single-label localhost is invalid in Firefox.
        firefox_user_prefs={"network.dns.localDomains": "cookie.test,sub.cookie.test"},
    )
    context = manager.browser_sessions.active_sessions[profile_id].camoufox.browser
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    return context, page


@pytest.mark.browser
@pytest.mark.asyncio
async def test_cookie_import_is_sent_with_scope_flags_and_sessions_end_on_close(profile_manager):
    manager = profile_manager
    transfer = CookieTransfer(manager, ProfileBackupManager(manager))
    profile = await manager.create_profile("Cookie wire proof", generate_fingerprint=False)
    expiry = int(time.time()) + 7200
    base = {
        "value": "synthetic",
        "domain": "cookie.test",
        "path": "/",
        "expires": expiry,
        "httpOnly": False,
        "secure": False,
        "sameSite": "Lax",
    }
    records = [
        {**base, "name": "host-proof"},
        {**base, "name": "domain-proof", "domain": ".cookie.test"},
        {**base, "name": "path-proof", "path": "/restricted", "sameSite": "Strict"},
        {**base, "name": "http-only-proof", "httpOnly": True, "sameSite": "Strict"},
        {**base, "name": "secure-proof", "secure": True, "sameSite": "None"},
        {**base, "name": "expired-proof", "expires": 1},
        {**base, "name": "session-proof", "expires": -1},
    ]
    imported = await transfer.import_cookies(profile.id, json.dumps(records), "json", "merge", 0)
    assert imported["imported"] == 6 and imported["session_count"] == 1
    assert imported["skipped"] == [{"row": 6, "reason": "Expired cookie"}]
    portable = await transfer.export_cookies(profile.id, "json", imported["row_version"])
    assert len(parse_cookies(portable, "json").cookies) == 6
    # Export did not consume deferred sessions on the original profile.
    with cookie_site() as (port, observed):
        context, page = await launch_page(manager, profile.id, f"http://cookie.test:{port}/")
        try:
            stored = {cookie["name"]: cookie for cookie in await context.cookies()}
            assert stored["host-proof"]["domain"] == "cookie.test"
            assert stored["domain-proof"]["domain"] == ".cookie.test"
            assert stored["path-proof"]["path"] == "/restricted"
            assert stored["path-proof"]["sameSite"] == "Strict"
            assert stored["http-only-proof"]["httpOnly"] is True
            assert stored["secure-proof"]["secure"] is True
            assert stored["secure-proof"]["sameSite"] == "None"
            assert abs(stored["host-proof"]["expires"] - expiry) <= 1
            assert stored["session-proof"]["expires"] == -1
            assert "expired-proof" not in stored
            root_header = next(header for path, header in observed if path == "/")
            assert "host-proof=synthetic" in root_header
            assert "domain-proof=synthetic" in root_header
            assert "http-only-proof=synthetic" in root_header
            assert "session-proof=synthetic" in root_header
            assert "path-proof=" not in root_header
            document = await page.evaluate("document.cookie")
            assert "host-proof=synthetic" in document and "http-only-proof=" not in document
            await page.goto(
                f"http://cookie.test:{port}/restricted/proof",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            assert "path-proof=synthetic" in next(
                header for path, header in observed if path == "/restricted/proof"
            )
            # Fulfill a synthetic subdomain locally before DNS/network. Browser
            # domain matching still runs against the subdomain document origin.
            await context.route(
                f"http://sub.cookie.test:{port}/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<!doctype html><p>Subdomain proof</p>",
                ),
            )
            await page.goto(
                f"http://sub.cookie.test:{port}/", wait_until="domcontentloaded", timeout=20000
            )
            subdomain = await page.evaluate("document.cookie")
            assert "domain-proof=synthetic" in subdomain and "host-proof=" not in subdomain
        finally:
            await manager.close_browser(profile.id)
        # Acknowledged imported session state is excluded from portable exports.
        exported_after = await transfer.export_cookies(profile.id, "json", imported["row_version"])
        assert "session-proof" not in {cookie["name"] for cookie in json.loads(exported_after)}
        context, _ = await launch_page(manager, profile.id, f"http://cookie.test:{port}/")
        try:
            names = {cookie["name"] for cookie in await context.cookies()}
            assert "host-proof" in names and "session-proof" not in names
        finally:
            await manager.close_browser(profile.id)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_netscape_portable_cookie_import_and_confirmed_replace(profile_manager):
    manager = profile_manager
    transfer = CookieTransfer(manager, ProfileBackupManager(manager))
    profile = await manager.create_profile("Netscape proof", generate_fingerprint=False)
    expiry = int(time.time()) + 7200
    text = f"# Netscape HTTP Cookie File\n#HttpOnly_localhost\tFALSE\t/\tFALSE\t{expiry}\tprivate-proof\tsynthetic\n"
    imported = await transfer.import_cookies(profile.id, text, "netscape", "merge", 0)
    exported = await transfer.export_cookies(profile.id, "netscape", imported["row_version"])
    roundtrip = parse_cookies(exported, "netscape").cookies
    assert roundtrip[0]["name"] == "private-proof" and roundtrip[0]["httpOnly"]
    replaced = await transfer.import_cookies(profile.id, "[]", "json", "replace", 1, True)
    assert replaced["backup_id"]
    with cookie_site() as (port, _):
        context, _ = await launch_page(manager, profile.id, f"http://localhost:{port}/")
        try:
            assert await context.cookies() == []
        finally:
            await manager.close_browser(profile.id)
    recovered = await transfer.backups.restore_backup(replaced["backup_id"])
    exported = await transfer.export_cookies(recovered.id, "json", recovered.row_version)
    assert json.loads(exported)[0]["name"] == "private-proof"
