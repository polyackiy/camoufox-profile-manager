"""Offline UI contract smoke tests for the desktop static export.

Run after `NEXT_EXPORT=1 npm run build:static`:
  .venv/bin/python web/tests/stage_one_ui.py
Requires Playwright Chromium (`playwright install chromium`).
"""
import ast
import json
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser navigation may cancel a still-streaming font or script.


class DesktopUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "out"
        if not (root / "index.html").exists():
            raise RuntimeError("Build the static UI first: NEXT_EXPORT=1 npm run build:static")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self.calls = []
        self.auth_required = False
        self.running_profile_ids = []
        self.library = [{"id": "library-one", "name": "Office", "type": "http", "server": "proxy.example:3128", "username": "proxy-user", "country": "DE", "has_password": True, "row_version": 4, "last_check": None, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}]
        self.proxy_stale_once = False
        self.proxy_failure_once = False
        self.import_failure = False
        self.assignment_skips = []
        self.browser_state = {"installed": False, "version": None, "state": "idle", "progress": None, "message": "Browser not installed", "error": None}
        self.profiles = []
        self.backups = []
        self.update_recovery = {"state": "idle", "maintenance_active": False, "automatic_install_available": False, "can_cancel": False}
        self.update_action_failure = False
        self.trash = []
        self.config = {"version": "0.5.0", "host": "127.0.0.1", "port": 8000, "database_path": "/local/profiles.db", "api_key_set": False, "user_auth_enabled": False, "encryption_enabled": True, "cors_origins": [], "camoufox_available": True, "uptime_seconds": 60}
        self.page.route("**/api/v1/**", self.route)

    def tearDown(self):
        self.context.close()

    def route(self, route):
        request = route.request
        path = request.url.split("/api/v1", 1)[1]
        self.calls.append((request.method, path, request.post_data))
        data = {}
        envelope = False
        # New sync UI performs a best-effort profile-state read on the profile
        # page. These legacy workflows deliberately have no sync enrollment.
        # Match the cookie-only API boundary rather than silently enabling sync.
        if path in {"/sync/profiles", "/sync/status", "/sync/pairing/draft"} and request.method == "GET":
            sync_headers = {"Cache-Control": "no-store"}
            if getattr(self, "role", None) != "administrator":
                return route.fulfill(status=401, json={"detail": "Administrator cookie required"}, headers=sync_headers)
            if path == "/sync/profiles":
                return route.fulfill(status=409, json={"detail": "Synchronization is not enrolled"}, headers=sync_headers)
            if path == "/sync/status":
                return route.fulfill(json={"phase": "not_enrolled"}, headers=sync_headers)
            return route.fulfill(body="null", content_type="application/json", headers=sync_headers)
        if path == "/auth/session":
            data = {"user_auth_enabled": self.auth_required, "authenticated": not self.auth_required, "username": None, "role": "administrator", "actor_kind": "anonymous_local", "can_manage_access": True}
        elif path == "/system/config":
            data, envelope = self.config, True
        elif path == "/system/status":
            data = {"total_profiles": len(self.profiles), "active_profiles": 0, "running_browsers": 0, "total_groups": 0, "system_load": 0, "memory_usage": 0, "disk_usage": 0, "uptime_seconds": 60}
        elif path.startswith("/profiles?"):
            data = {"profiles": self.profiles, "total": len(self.profiles), "page": 1, "per_page": 100, "has_next": False, "has_prev": False}
        elif path == "/profiles" and request.method == "POST":
            data = {"id": "new-profile", **request.post_data_json}
        elif path == "/groups":
            data = {"groups": [], "total": 0}
        elif path == "/saved-filters":
            data, envelope = {"filters": []}, True
        elif path == "/schedules":
            data = {"schedules": [], "total": 0}
        elif path == "/browsers/active":
            data = {"active_browsers": [{"profile_id": item} for item in self.running_profile_ids], "count": len(self.running_profile_ids)}
        elif path == "/fingerprints/presets":
            data, envelope = {"presets": []}, True
        elif path == "/system/browser":
            data, envelope = self.browser_state, True
        elif path == "/system/browser/install":
            self.browser_state = {**self.browser_state, "state": "downloading", "progress": 35, "message": "Downloading browser"}
            data, envelope = self.browser_state, True
        elif path == "/system/update-recovery":
            data = self.update_recovery
        elif path == "/system/update-recovery/download":
            self.update_recovery = {**self.update_recovery, "state": "staged", "can_apply": True, "transaction_id": "verified-transaction", "target_version": "0.7.0", "browser_version": "156.0.1-beta.36"}
            data = self.update_recovery
        elif path in {"/system/update-recovery/apply", "/system/update-recovery/rollback", "/system/update-recovery/resume"}:
            if self.update_action_failure:
                route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "Recovery request was rejected. Refresh and try again."}))
                return
            self.update_recovery = {**self.update_recovery, "state": "handoff_requested", "request_pending": True}
            data = self.update_recovery
        elif path == "/system/update-recovery/cancel":
            self.update_recovery = {**self.update_recovery, "state": "cancelled", "maintenance_active": False, "can_cancel": False}
            data = self.update_recovery
        elif path == "/system/updates?channel=preview":
            data, envelope = {"current_version": "0.6.0rc1", "latest_version": "0.6.0-rc.2", "available": True, "release_url": "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0-rc.2", "assets": []}, True
        elif path == "/system/updates/prepare?channel=preview":
            data, envelope = {"release_url": "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0-rc.2", "backup_count": 1, "message": "Ready"}, True
        elif path == "/system/updates":
            data, envelope = {"current_version": "0.5.0", "latest_version": "0.6.0", "available": True, "release_url": "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0", "assets": []}, True
        elif path == "/system/updates/prepare":
            data, envelope = {"release_url": "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0", "backup_count": 1, "message": "1 backup created. Release ready."}, True
        elif path == "/system/backups":
            data, envelope = {"backups": self.backups}, True
        elif path.startswith("/system/backups/") and path.endswith("/restore"):
            data, envelope = {"id": "restored-copy", "name": "Recovered profile"}, True
        elif path == "/proxies" and request.method == "GET":
            data, envelope = {"proxies": self.library}, True
        elif path == "/proxies" and request.method == "POST":
            body = request.post_data_json
            proxy = {**self.library[0], **{key: value for key, value in body.items() if key != "password"}, "id": "library-new", "has_password": bool(body.get("password")), "row_version": 0}
            self.library.append(proxy)
            data, envelope = proxy, True
        elif path.startswith("/proxies/") and request.method in {"PATCH", "DELETE"}:
            if self.proxy_stale_once:
                self.proxy_stale_once = False
                self.library[0] = {**self.library[0], "row_version": 5, "server": "changed.example:3128"}
                route.fulfill(status=409, content_type="application/json", body=json.dumps({"error": {"code": "stale_write", "message": "Proxy changed"}}))
                return
            if self.proxy_failure_once:
                self.proxy_failure_once = False
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"error": {"code": "unavailable", "message": "Service unavailable"}}))
                return
            if request.method == "DELETE":
                self.library = []
                data, envelope = None, True
            else:
                body = request.post_data_json
                password = body.get("password", "preserve")
                self.library[0] = {**self.library[0], **{key: value for key, value in body.items() if key not in {"password", "row_version"}}, "row_version": self.library[0]["row_version"] + 1}
                if password != "preserve":
                    self.library[0]["has_password"] = bool(password)
                data, envelope = self.library[0], True
        elif path == "/proxies/library-one/check":
            self.library[0] = {**self.library[0], "last_check": {"reachable": True, "error": None, "latency_ms": 18, "ip": "203.0.113.4", "country": "DE", "timezone": "Europe/Berlin", "findings": [], "checked_at": "2026-10-12T10:00:00Z"}}
            data, envelope = self.library[0], True
        elif path == "/proxies/library-one/assign":
            body = request.post_data_json
            skipped_ids = {item["id"] for item in self.assignment_skips}
            data, envelope = {"assigned": [item["id"] for item in body["profiles"] if item["id"] not in skipped_ids], "skipped": self.assignment_skips}, True
        elif path == "/proxies/import":
            if self.import_failure:
                route.fulfill(status=422, content_type="application/json", body=json.dumps({"error": {"code": "validation_error", "message": "Line 2: input includes import-secret-value"}}))
                return
            proxy = {**self.library[0], "id": "imported", "name": "Imported proxy", "server": "imported.example:8080"}
            self.library.append(proxy)
            data, envelope = {"created": [proxy], "skipped": [{"line": 2, "reason": "Proxy already exists"}]}, True
        elif path == "/trash/profiles":
            data, envelope = {"profiles": self.trash}, True
        elif path.endswith("?confirm=true"):
            self.trash = []
            data, envelope = None, True
        else:
            raise AssertionError(f"Unexpected API call: {request.method} {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"success": True, "data": data} if envelope else data))

    def test_explicit_browser_install_and_simple_profile(self):
        self.page.goto(self.origin)
        expect(self.page.get_by_role("heading", name="Set up your browser")).to_be_visible()
        self.assertFalse(any(path == "/system/browser/install" for _, path, _ in self.calls))
        self.page.get_by_role("button", name="Install browser", exact=True).click()
        expect(self.page.get_by_role("progressbar")).to_have_attribute("value", "35")
        self.browser_state = {**self.browser_state, "installed": True, "state": "ready", "version": "135", "progress": 100, "message": "Ready"}
        expect(self.page.get_by_role("heading", name="Ready for your first profile")).to_be_visible()
        self.page.get_by_role("button", name="Create first profile", exact=True).click()
        expect(self.page.get_by_label("Browser interface", exact=True)).not_to_be_visible()
        self.page.get_by_text("Advanced browser and fingerprint settings", exact=True).click()
        self.page.get_by_label("Browser interface", exact=True).select_option("camoufox")
        self.page.get_by_label("Name", exact=True).fill("First profile")
        self.page.get_by_role("button", name="Create profile", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        saved = next(json.loads(body) for method, path, body in self.calls if method == "POST" and path == "/profiles")
        self.assertEqual(saved["browser_settings"]["browser_ui"], "camoufox")
        self.assertEqual(sum(path == "/system/browser/install" for _, path, _ in self.calls), 1)

    def test_returning_user_is_not_prompted(self):
        self.browser_state = {**self.browser_state, "installed": True, "state": "ready", "version": "135"}
        self.profiles = [{"id": "existing", "name": "Existing", "status": "active", "browser_settings": {"os": "windows", "screen": "1920x1080"}, "created_at": "2026-01-01T00:00:00Z", "row_version": 0}]
        self.page.goto(self.origin)
        expect(self.page.get_by_role("cell", name="Existing", exact=True)).to_be_visible()
        expect(self.page.get_by_role("region", name="Browser setup")).not_to_be_visible()

    def test_release_download_requires_backup_preparation(self):
        self.page.goto(f"{self.origin}/settings/")
        self.page.get_by_role("button", name="Check for updates", exact=True).click()
        expect(self.page.get_by_text("Version 0.6.0 is available. You have 0.5.0.")).to_be_visible()
        expect(self.page.get_by_role("link", name="Open release downloads")).not_to_be_visible()
        self.page.get_by_role("button", name="Back up and prepare update").click()
        expect(self.page.get_by_role("link", name="Open release downloads")).to_have_attribute("href", "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0")
        self.assertEqual(sum(path == "/system/updates/prepare" for _, path, _ in self.calls), 1)

    def test_backup_restore_creates_a_separate_profile(self):
        self.backups = [{"id": "saved", "profile_id": "original", "profile_name": "Original profile", "created_at": "2026-01-01T00:00:00Z", "reason": "manual", "size_bytes": 1024}]
        self.page.goto(f"{self.origin}/settings/")
        self.page.get_by_role("button", name="Restore backup of Original profile", exact=False).click()
        expect(self.page.get_by_role("dialog")).to_contain_text("Your current profiles and this backup are preserved.")
        self.page.get_by_role("button", name="Restore as new profile", exact=True).click()
        expect(self.page.get_by_text("Backup restored as a new profile", exact=True)).to_be_visible()
        self.assertTrue(any(method == "POST" and path == "/system/backups/saved/restore" for method, path, _ in self.calls))
        self.assertFalse(any(method in {"PUT", "DELETE"} for method, _, _ in self.calls))

    def test_preview_channel_is_opt_in_and_preserved_during_preparation(self):
        self.page.goto(f"{self.origin}/settings/")
        choice = self.page.get_by_label("Include preview releases (opt-in)")
        expect(choice).not_to_be_checked()
        choice.check()
        self.page.get_by_role("button", name="Check for updates", exact=True).click()
        expect(self.page.get_by_text("Version 0.6.0-rc.2 is available. You have 0.6.0rc1.")).to_be_visible()
        self.page.get_by_role("button", name="Back up and prepare update").click()
        expect(self.page.get_by_role("link", name="Open release downloads")).to_have_attribute("href", "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0-rc.2")
        choice.uncheck()
        expect(self.page.get_by_role("link", name="Open release downloads")).not_to_be_visible()

    def test_prepared_update_can_be_cancelled_without_restoring_data(self):
        self.update_recovery = {"state": "prepared", "maintenance_active": True, "automatic_install_available": False, "can_cancel": True, "target_version": "0.7.0", "browser_version": "156.0.1-beta.36"}
        self.page.goto(f"{self.origin}/settings/")
        self.page.get_by_text("Update recovery", exact=True).click()
        self.page.get_by_role("button", name="Check recovery status").click()
        expect(self.page.get_by_text("Update maintenance is active. Profile changes and launches are paused.")).to_be_visible()
        self.page.get_by_role("button", name="Cancel update preparation").click()
        expect(self.page.get_by_text("No update maintenance is active.")).to_be_visible()
        expect(self.page.get_by_role("button", name="Cancel update preparation")).not_to_be_visible()
        self.assertFalse(any("/restore" in path for _, path, _ in self.calls))

    def open_update_recovery(self, **changes):
        self.update_recovery = {"state": "idle", "maintenance_active": False, "automatic_install_available": True, "can_cancel": False, "can_download": True, **changes}
        self.page.goto(f"{self.origin}/settings/")
        self.page.get_by_text("Update recovery", exact=True).click()
        expect(self.page.get_by_role("button", name="Check recovery status", exact=True)).to_be_enabled()

    def test_verified_policy_disabled_keeps_manual_download_available(self):
        self.open_update_recovery(can_download=False, automatic_install_available=False, disabled_reason="No trusted TUF root is configured")
        expect(self.page.get_by_text("No trusted TUF root is configured", exact=False)).to_be_visible()
        expect(self.page.get_by_role("button", name="Download verified update", exact=True)).not_to_be_visible()
        self.page.get_by_role("button", name="Check for updates", exact=True).click()
        self.page.get_by_role("button", name="Back up and prepare update", exact=True).click()
        expect(self.page.get_by_role("link", name="Open release downloads")).to_be_visible()
        self.assertFalse(any(path.endswith("/download") for _, path, _ in self.calls))

    def test_verified_preview_download_requires_restart_acknowledgement(self):
        self.open_update_recovery()
        self.page.get_by_label("Include preview releases (opt-in)").check()
        self.page.get_by_role("button", name="Download verified update", exact=True).click()
        expect(self.page.get_by_text("The verified update is ready to install.", exact=True)).to_be_visible()
        body = next(json.loads(body) for method, path, body in self.calls if method == "POST" and path.endswith("/download"))
        self.assertEqual(body, {"channel": "preview"})
        self.page.get_by_role("button", name="Restart and install update", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Restart and install update", exact=True)
        expect(dialog).to_contain_text("A complete recovery point is created")
        confirm = dialog.get_by_role("button", name="Confirm restart", exact=True)
        expect(confirm).to_be_disabled()
        self.assertFalse(any(path.endswith("/apply") for _, path, _ in self.calls))
        dialog.get_by_label("I understand and want to continue.").check()
        confirm.click()
        expect(dialog).not_to_be_visible()
        expect(self.page.get_by_text("The manager is closing for the update.", exact=False)).to_be_visible()
        body = next(json.loads(body) for method, path, body in self.calls if method == "POST" and path.endswith("/apply"))
        self.assertEqual(body, {"transaction_id": "verified-transaction"})
        expect(self.page.get_by_role("button", name="Restart and install update", exact=True)).to_be_disabled()

    def test_verified_rollback_confirms_exact_timestamp(self):
        checkpoint = "2026-10-13T10:23:45.123456+00:00"
        self.open_update_recovery(state="staged", can_apply=True, can_rollback=True, transaction_id="new-staged-transaction", rollback_transaction_id="rollback-transaction", checkpoint_timestamp=checkpoint)
        self.page.get_by_role("button", name="Restore previous version", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Restore previous version", exact=True)
        expect(dialog).to_contain_text("The newer state is retained for recovery.")
        confirm = dialog.get_by_role("button", name="Confirm restore", exact=True)
        expect(confirm).to_be_disabled()
        dialog.get_by_label("I understand and want to continue.").check()
        confirm.click()
        body = next(json.loads(body) for method, path, body in self.calls if method == "POST" and path.endswith("/rollback"))
        self.assertEqual(body, {"transaction_id": "rollback-transaction", "confirm_timestamp": checkpoint})

    def test_verified_rollback_rejects_changed_checkpoint(self):
        self.open_update_recovery(state="replacing", maintenance_active=True, can_download=False, can_rollback=True, transaction_id="rollback-transaction", checkpoint_timestamp="2026-10-13T10:00:00Z")
        self.page.get_by_role("button", name="Restore previous version", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Restore previous version", exact=True)
        dialog.get_by_label("I understand and want to continue.").check()
        self.update_recovery["checkpoint_timestamp"] = "2026-10-13T11:00:00Z"
        expect(dialog.get_by_role("alert")).to_contain_text("The recovery state changed.")
        expect(dialog.get_by_role("button", name="Confirm restore", exact=True)).to_be_disabled()
        self.assertFalse(any(path.endswith("/rollback") for _, path, _ in self.calls))

    def test_verified_action_failure_preserves_confirmation(self):
        self.update_action_failure = True
        self.open_update_recovery(state="staged", can_apply=True, transaction_id="verified-transaction")
        self.page.get_by_role("button", name="Restart and install update", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Restart and install update", exact=True)
        dialog.get_by_label("I understand and want to continue.").check()
        dialog.get_by_role("button", name="Confirm restart", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("Recovery request was rejected.")
        expect(dialog.get_by_role("button", name="Confirm restart", exact=True)).to_be_enabled()
        expect(dialog.get_by_label("I understand and want to continue.")).to_be_checked()

    def test_verified_invalid_timestamp_hides_rollback(self):
        self.open_update_recovery(state="committed", can_rollback=True, transaction_id="rollback-transaction", checkpoint_timestamp="invalid-date")
        expect(self.page.get_by_role("button", name="Restore previous version", exact=True)).not_to_be_visible()
        self.assertNotIn("Invalid Date", self.page.locator("body").inner_text())

    def test_verified_update_status_is_localized(self):
        self.open_update_recovery(state="staged", can_apply=True, transaction_id="verified-transaction")
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        expect(self.page.get_by_role("heading", name="已验证更新与回滚", exact=True)).to_be_visible()
        expect(self.page.get_by_text("已验证更新已准备就绪，可以安装。", exact=True)).to_be_visible()
        self.page.get_by_role("button", name="重启并安装更新", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="重启并安装更新", exact=True)
        expect(dialog.get_by_role("button", name="确认重启", exact=True)).to_be_disabled()
        expect(dialog.get_by_label("我已了解并希望继续。")).to_be_visible()

    def test_verified_failed_handoff_can_be_retried(self):
        self.open_update_recovery(state="prepared", can_resume=True, maintenance_active=True, can_download=False, request_pending=True, transaction_id="retry-transaction", error="Offline update interrupted; review recovery options")
        self.page.get_by_role("button", name="Retry interrupted update", exact=True).click()
        body = next(json.loads(body) for method, path, body in self.calls if method == "POST" and path.endswith("/resume"))
        self.assertEqual(body, {"transaction_id": "retry-transaction"})

    def open_native_recovery(self, **changes):
        status = {"state": "committed", "can_rollback": True, "can_resume": False, "can_cancel": False, "maintenance_active": False, "transaction_id": "new-staged-transaction", "rollback_transaction_id": "retained-transaction", "checkpoint_timestamp": "2026-10-13T10:23:45.123456+00:00", **changes}
        source = Path(__file__).resolve().parents[2] / "src/camoufox_pm/desktop_recovery.py"
        module = ast.parse(source.read_text())
        html = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RECOVERY_HTML" for target in node.targets))
        self.page.evaluate("""s => {
          window.nativeStatus=s;window.nativeCalls=[];window.nativeOpenCount=0;
          window.pywebview={api:{status:async()=>window.nativeStatus,
            action:async(...args)=>{window.nativeCalls.push(args);window.nativeStatus={...window.nativeStatus,state:'handoff_requested',request_pending:true,error:null};return window.nativeStatus;},
            open_manager:async()=>{window.nativeOpenCount++;}}};
        }""", status)
        self.page.set_content(html)
        self.page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        expect(self.page.locator("#state")).not_to_have_text("Reading recovery status…")
        return status

    def test_native_rollback_confirms_retained_id_and_exact_timestamp(self):
        status = self.open_native_recovery()
        self.page.get_by_role("button", name="Restore previous version", exact=True).click()
        confirm = self.page.get_by_role("button", name="Confirm restore", exact=True)
        expect(confirm).to_be_disabled()
        expect(self.page.locator("#acknowledge")).to_be_focused()
        self.assertEqual(self.page.evaluate("window.nativeCalls"), [])
        self.page.get_by_label("I understand that later changes will no longer be active.").check()
        confirm.click()
        self.assertEqual(self.page.evaluate("window.nativeCalls"), [["rollback", "retained-transaction", status["checkpoint_timestamp"]]])

    def check_native_stale_confirmation(self, key, value):
        self.open_native_recovery()
        self.page.get_by_role("button", name="Restore previous version", exact=True).click()
        self.page.get_by_label("I understand that later changes will no longer be active.").check()
        self.page.evaluate("([key,value])=>{window.nativeStatus[key]=value}", [key, value])
        self.page.get_by_role("button", name="Refresh status", exact=True).click()
        expect(self.page.get_by_role("alert")).to_contain_text("The recovery state changed.")
        expect(self.page.get_by_role("button", name="Confirm restore", exact=True)).to_be_disabled()
        self.assertEqual(self.page.evaluate("window.nativeCalls"), [])
        self.page.get_by_role("button", name="Cancel restore", exact=True).click()
        self.page.get_by_role("button", name="Restore previous version", exact=True).click()
        expect(self.page.get_by_role("button", name="Confirm restore", exact=True)).to_be_disabled()
        expect(self.page.locator("#acknowledge")).not_to_be_checked()

    def test_native_rollback_rejects_changed_transaction(self):
        self.check_native_stale_confirmation("rollback_transaction_id", "changed-transaction")

    def test_native_rollback_rejects_changed_timestamp(self):
        self.check_native_stale_confirmation("checkpoint_timestamp", "2026-10-13T11:00:00Z")

    def test_native_unknown_state_and_invalid_timestamp_hide_unsafe_actions(self):
        self.open_native_recovery(state="unknown-future-state", checkpoint_timestamp="invalid-date")
        expect(self.page.get_by_role("button", name="Open manager", exact=True)).not_to_be_visible()
        expect(self.page.get_by_role("button", name="Restore previous version", exact=True)).not_to_be_visible()
        self.assertNotIn("Invalid Date", self.page.locator("body").inner_text())

    def test_native_failed_handoff_offers_retry_but_keeps_manager_closed(self):
        self.open_native_recovery(state="prepared", can_resume=True, maintenance_active=True, request_pending=True, error="Offline update interrupted; review recovery options")
        expect(self.page.get_by_role("button", name="Open manager", exact=True)).not_to_be_visible()
        expect(self.page.get_by_role("alert")).to_contain_text("Offline update interrupted")
        self.page.get_by_role("button", name="Retry interrupted update", exact=True).click()
        self.assertEqual(self.page.evaluate("window.nativeCalls"), [["resume", "new-staged-transaction", None]])

    def test_permanent_delete_requires_name_confirmation(self):
        self.trash = [{"id": "trashed", "name": "Recoverable", "deleted_at": "2026-01-01T00:00:00Z"}]
        self.page.goto(f"{self.origin}/trash/")
        self.page.get_by_role("button", name="Permanently delete Recoverable").click()
        confirm = self.page.get_by_role("button", name="Delete permanently", exact=True).last
        expect(confirm).to_be_disabled()
        self.page.get_by_label("Type Recoverable to confirm").fill("Recoverable")
        expect(confirm).to_be_enabled()
        confirm.click()
        expect(self.page.get_by_role("heading", name="Trash is empty")).to_be_visible()
        self.assertEqual(sum(method == "DELETE" and path == "/trash/profiles/trashed?confirm=true" for method, path, _ in self.calls), 1)

    def test_chinese_ui_preserves_profile_settings_and_survives_reload(self):
        self.context.close()
        self.context = self.browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 900})
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self.page.route("**/api/v1/**", self.route)
        errors = []
        self.page.on("pageerror", lambda error: errors.append(str(error)))
        self.page.goto(self.origin)
        expect(self.page.locator("html")).to_have_attribute("lang", "zh-CN")
        self.page.get_by_role("button", name="新建配置", exact=True).click()
        self.page.get_by_label("名称", exact=True).fill("中文配置")
        self.page.get_by_text("高级浏览器与指纹设置", exact=True).click()
        self.page.get_by_label("指纹语言", exact=True).fill("en-US, en")
        self.page.get_by_label("时区", exact=True).fill("Europe/Berlin")
        self.page.get_by_role("button", name="创建配置", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        saved = next(json.loads(body) for method, path, body in self.calls if method == "POST" and path == "/profiles")
        self.assertEqual(saved["name"], "中文配置")
        self.assertEqual(saved["browser_settings"]["languages"], ["en-US", "en"])
        self.assertEqual(saved["browser_settings"]["timezone"], "Europe/Berlin")
        self.assertEqual(saved["browser_settings"]["os"], "windows")

        for title in ["分组", "定时任务", "回收站", "设置", "浏览器配置"]:
            self.page.get_by_role("link", name=title, exact=True).click()
            expect(self.page.get_by_role("heading", name=title, exact=True)).to_be_visible()
        self.page.get_by_label("界面语言", exact=True).select_option("en")
        self.page.reload()
        expect(self.page.get_by_role("heading", name="Profiles", exact=True)).to_be_visible()
        expect(self.page.locator("html")).to_have_attribute("lang", "en")
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        self.page.reload()
        expect(self.page.get_by_role("heading", name="浏览器配置", exact=True)).to_be_visible()
        self.assertEqual(errors, [])

    def test_language_changes_reach_another_open_tab(self):
        self.page.goto(self.origin)
        other = self.context.new_page()
        other.route("**/api/v1/**", self.route)
        other.goto(self.origin)
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        expect(other.get_by_role("heading", name="浏览器配置", exact=True)).to_be_visible()
        other.get_by_label("界面语言", exact=True).select_option("en")
        expect(self.page.get_by_role("heading", name="Profiles", exact=True)).to_be_visible()

    def test_language_selector_is_available_before_login(self):
        self.auth_required = True
        self.page.goto(self.origin)
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        expect(self.page.get_by_role("heading", name="登录", exact=True)).to_be_visible()
        expect(self.page.get_by_label("用户名", exact=True)).to_be_visible()
        expect(self.page.get_by_label("密码", exact=True)).to_be_visible()
        self.page.reload()
        expect(self.page.get_by_role("heading", name="登录", exact=True)).to_be_visible()

    def test_language_switch_works_when_local_storage_is_unavailable(self):
        self.page.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new Error('Storage disabled') }})")
        self.page.goto(self.origin)
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        expect(self.page.get_by_role("heading", name="浏览器配置", exact=True)).to_be_visible()
        self.page.get_by_role("link", name="分组", exact=True).click()
        expect(self.page.get_by_role("heading", name="分组", exact=True)).to_be_visible()

    def open_proxy_library(self):
        self.page.goto(f"{self.origin}/proxies/")
        expect(self.page.get_by_text("Office", exact=True)).to_be_visible()

    def test_proxy_library_passwords_are_write_only_and_checks_are_explicit(self):
        self.open_proxy_library()
        expect(self.page.get_by_text("Password stored", exact=True)).to_be_visible()
        self.assertFalse(any(path.endswith("/check") for _, path, _ in self.calls))
        self.page.get_by_role("button", name="Edit Office", exact=True).click()
        expect(self.page.get_by_label("Replace password", exact=True)).to_have_value("")
        self.page.get_by_role("button", name="Save changes", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        saved = next(json.loads(body) for method, _, body in self.calls if method == "PATCH")
        self.assertNotIn("password", saved)
        self.assertEqual(saved["row_version"], 4)
        self.page.get_by_role("button", name="Check Office", exact=True).click()
        expect(self.page.get_by_text("Healthy", exact=True)).to_be_visible()
        self.assertEqual(sum(path.endswith("/check") for _, path, _ in self.calls), 1)

    def test_proxy_create_keeps_credentials_out_of_the_saved_record(self):
        self.open_proxy_library()
        self.page.get_by_role("button", name="New proxy", exact=True).click()
        self.page.get_by_label("Name", exact=True).fill("Authenticated SOCKS5")
        self.page.get_by_label("Type", exact=True).select_option("socks5")
        self.page.get_by_label("Server", exact=True).fill("socks.example:1080")
        self.page.get_by_label("Username", exact=True).fill("account")
        self.page.get_by_label("Password (optional)", exact=True).fill("new-secret-value")
        self.page.get_by_role("button", name="Create proxy", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        expect(self.page.get_by_role("table").get_by_text("Authenticated SOCKS5", exact=True)).to_be_visible()
        self.assertNotIn("new-secret-value", self.page.locator("body").inner_text())
        created = next(json.loads(body) for method, path, body in self.calls if method == "POST" and path == "/proxies")
        self.assertEqual(created["type"], "socks5")
        self.assertEqual(created["password"], "new-secret-value")
        self.assertFalse(any(path.endswith("/check") for _, path, _ in self.calls))

    def test_proxy_edit_stale_preserves_draft_and_requires_explicit_reload(self):
        self.open_proxy_library()
        self.page.get_by_role("button", name="Edit Office", exact=True).click()
        self.page.get_by_label("Name", exact=True).fill("Draft name")
        self.page.get_by_label("Replace password", exact=True).fill("draft-secret-value")
        self.proxy_stale_once = True
        self.page.get_by_role("button", name="Save changes", exact=True).click()
        expect(self.page.get_by_text("This proxy changed elsewhere. Your draft is preserved. Reload the saved proxy before editing again.", exact=True)).to_be_visible()
        expect(self.page.get_by_label("Name", exact=True)).to_have_value("Draft name")
        expect(self.page.get_by_label("Replace password", exact=True)).to_have_value("draft-secret-value")
        expect(self.page.get_by_role("button", name="Save changes", exact=True)).to_be_disabled()
        self.assertNotIn("draft-secret-value", self.page.locator("body").inner_text())
        self.page.get_by_role("button", name="Reload saved proxy", exact=True).click()
        expect(self.page.get_by_label("Server", exact=True)).to_have_value("changed.example:3128")
        expect(self.page.get_by_label("Replace password", exact=True)).to_have_value("")
        self.page.get_by_label("Remove stored password", exact=True).check()
        self.page.get_by_role("button", name="Save changes", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        updates = [json.loads(body) for method, _, body in self.calls if method == "PATCH"]
        self.assertIsNone(updates[-1]["password"])
        self.assertEqual(updates[-1]["row_version"], 5)

    def test_proxy_save_and_delete_failure_do_not_discard_records(self):
        self.open_proxy_library()
        self.page.get_by_role("button", name="Edit Office", exact=True).click()
        self.page.get_by_label("Name", exact=True).fill("Still my draft")
        self.proxy_failure_once = True
        self.page.get_by_role("button", name="Save changes", exact=True).click()
        expect(self.page.get_by_role("dialog").get_by_role("alert")).to_contain_text("Service unavailable")
        expect(self.page.get_by_label("Name", exact=True)).to_have_value("Still my draft")
        self.page.get_by_role("button", name="Cancel", exact=True).click()
        self.page.get_by_role("button", name="Delete Office", exact=True).click()
        self.proxy_stale_once = True
        self.page.get_by_role("button", name="Delete", exact=True).click()
        expect(self.page.get_by_role("dialog").get_by_role("alert")).to_contain_text("Reload and review")
        expect(self.page.get_by_role("button", name="Delete", exact=True)).to_be_disabled()
        self.page.get_by_role("button", name="Reload saved proxy", exact=True).click()
        expect(self.page.get_by_role("dialog")).to_contain_text("changed.example:3128")
        self.page.get_by_role("button", name="Delete", exact=True).click()
        expect(self.page.get_by_role("heading", name="No library proxies yet", exact=True)).to_be_visible()
        deletes = [path for method, path, _ in self.calls if method == "DELETE"]
        self.assertEqual(deletes, ["/proxies/library-one?row_version=4", "/proxies/library-one?row_version=5"])

    def test_proxy_assignment_confirms_replacement_and_reports_skipped_profiles(self):
        self.profiles = [{"id": "closed", "name": "Closed profile", "row_version": 7, "proxy_config": {"type": "http", "server": "http://legacy-user:legacy-secret@old.example:3128"}}, {"id": "changed", "name": "Changed profile", "row_version": 3, "proxy_config": None}, {"id": "running", "name": "Running profile", "row_version": 4, "proxy_config": None}]
        self.running_profile_ids = ["running"]
        self.assignment_skips = [{"id": "changed", "reason": "Profile changed; reload before assigning"}]
        self.open_proxy_library()
        self.page.get_by_role("button", name="Assign Office", exact=True).click()
        expect(self.page.get_by_role("checkbox", name="Select Running profile", exact=True)).to_be_disabled()
        self.page.get_by_role("checkbox", name="Select Closed profile", exact=True).check()
        self.page.get_by_role("checkbox", name="Select Changed profile", exact=True).check()
        self.assertNotIn("legacy-secret", self.page.locator("body").inner_text())
        confirm = self.page.get_by_role("button", name="Assign to 2 profiles", exact=True)
        expect(confirm).to_be_disabled()
        self.page.get_by_role("checkbox", name="Replace existing proxy settings for the selected profile.", exact=True).check()
        confirm.click()
        expect(self.page.get_by_text("Assigned to 1 profile.", exact=True)).to_be_visible()
        expect(self.page.get_by_text("1 profile skipped", exact=True)).to_be_visible()
        expect(self.page.get_by_role("dialog")).to_contain_text("Changed profile: Profile changed; reload before assigning")
        body = next(json.loads(body) for _, path, body in self.calls if path.endswith("/assign"))
        self.assertEqual(body, {"proxy_row_version": 4, "profiles": [{"id": "closed", "row_version": 7}, {"id": "changed", "row_version": 3}]})
        self.assertFalse(any(path.startswith("/profiles/") for _, path, _ in self.calls))

    def test_proxy_import_credentials_clear_on_result_and_close(self):
        self.open_proxy_library()
        self.page.get_by_role("button", name="Import", exact=True).click()
        field = self.page.get_by_label("One proxy per line (up to 100)", exact=True)
        expect(field).to_have_attribute("maxlength", "64000")
        field.fill("http://user:import-secret-value@imported.example:8080\nimported.example:8080")
        self.page.get_by_role("button", name="Import proxies", exact=True).click()
        expect(self.page.get_by_text("1 proxy imported.", exact=True)).to_be_visible()
        expect(self.page.get_by_role("dialog")).to_contain_text("Line 2: Proxy already exists")
        self.assertNotIn("import-secret-value", self.page.locator("body").inner_text())
        self.page.get_by_role("button", name="Done", exact=True).click()
        self.page.get_by_role("button", name="Import", exact=True).click()
        expect(field).to_have_value("")
        field.fill("http://user:close-secret@proxy.example:8080")
        self.page.get_by_role("button", name="Cancel", exact=True).click()
        self.page.get_by_role("button", name="Import", exact=True).click()
        expect(field).to_have_value("")

    def test_proxy_import_error_exposes_only_safe_line_number(self):
        self.open_proxy_library()
        self.page.get_by_role("button", name="Import", exact=True).click()
        self.page.get_by_label("One proxy per line (up to 100)", exact=True).fill("proxy.example:8080\nbad-secret-url")
        self.import_failure = True
        self.page.get_by_role("button", name="Import proxies", exact=True).click()
        expect(self.page.get_by_role("dialog").get_by_role("alert")).to_have_text("Import failed on line 2. Check its proxy format and try again.")
        self.assertNotIn("import-secret-value", self.page.locator("body").inner_text())

    def test_proxy_library_simplified_chinese_and_socks_support_copy(self):
        self.open_proxy_library()
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        expect(self.page.get_by_role("heading", name="代理库", exact=True)).to_be_visible()
        self.page.get_by_role("button", name="新建代理", exact=True).click()
        self.page.get_by_label("类型", exact=True).select_option("socks5")
        expect(self.page.get_by_text("SOCKS5 认证由本地中继自动处理，无需安装扩展或手动设置浏览器。", exact=True)).to_be_visible()
        self.page.get_by_label("类型", exact=True).select_option("socks4")
        self.page.get_by_label("用户名", exact=True).fill("user")
        expect(self.page.get_by_text("不支持 SOCKS4 凭据。需要认证时请使用 SOCKS5、HTTP 或 HTTPS 代理。", exact=True)).to_be_visible()


if __name__ == "__main__":
    unittest.main(verbosity=2)
