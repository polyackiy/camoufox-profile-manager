"""Offline UI contract smoke tests for the desktop static export.

Run after `NEXT_EXPORT=1 npm run build:static`:
  .venv/bin/python web/tests/stage_one_ui.py
Requires Playwright Chromium (`playwright install chromium`).
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json
import unittest

from playwright.sync_api import sync_playwright, expect


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


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
        self.browser_state = {"installed": False, "version": None, "state": "idle", "progress": None, "message": "Browser not installed", "error": None}
        self.profiles = []
        self.backups = []
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
        if path == "/auth/session":
            data = {"user_auth_enabled": self.auth_required, "authenticated": not self.auth_required, "username": None}
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
        elif path == "/schedules":
            data = {"schedules": [], "total": 0}
        elif path == "/browsers/active":
            data = {"active_browsers": [], "count": 0}
        elif path == "/fingerprints/presets":
            data, envelope = {"presets": []}, True
        elif path == "/system/browser":
            data, envelope = self.browser_state, True
        elif path == "/system/browser/install":
            self.browser_state = {**self.browser_state, "state": "downloading", "progress": 35, "message": "Downloading browser"}
            data, envelope = self.browser_state, True
        elif path == "/system/updates":
            data, envelope = {"current_version": "0.5.0", "latest_version": "0.6.0", "available": True, "release_url": "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0", "assets": []}, True
        elif path == "/system/updates/prepare":
            data, envelope = {"release_url": "https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0", "backup_count": 1, "message": "1 backup created. Release ready."}, True
        elif path == "/system/backups":
            data, envelope = {"backups": self.backups}, True
        elif path.startswith("/system/backups/") and path.endswith("/restore"):
            data, envelope = {"id": "restored-copy", "name": "Recovered profile"}, True
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
