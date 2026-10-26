"""Russian/Chinese desktop workflows with fictional data and no external services.

Run after the static export: .venv/bin/python web/tests/localization_ui.py
Set CPM_LOCALIZATION_SCREENSHOTS to an output folder to retain visual evidence.
"""

import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from batch_operations_ui import BatchOperationsUI
from playwright.sync_api import expect
from stage_one_ui import DesktopUI
from stage_three_ui import StageThreeUI
from stage_two_ui import profile
from sync_ui import SyncUI
from template_batches_ui import TemplateBatchesUI


class Localized:
    ui_locale = "ru"

    def setUp(self):
        super().setUp()
        self.catalog = json.loads(
            (Path(__file__).parents[1] / "src/lib/locales" / f"{self.ui_locale}.json").read_text()
        )
        self.context.close()
        self.context = self.browser.new_context(
            locale=self.ui_locale, viewport={"width": 1280, "height": 900}
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self.page.route("**/api/v1/**", self.route)
        self.page_errors = []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.page_errors, [])
        super().tearDown()

    def tr(self, source, **values):
        value = self.catalog[source]
        if isinstance(value, dict):
            # Workflow tests use the count 2; grammatical boundaries have unit tests.
            value = value["few" if values.get("count") == 2 else "other"]
        for key, replacement in values.items():
            value = value.replace("{" + key + "}", str(replacement))
        return value

    def button(self, source, **values):
        return self.page.get_by_role("button", name=self.tr(source, **values), exact=True)

    def label(self, source):
        return self.page.get_by_label(self.tr(source), exact=True)

    def screenshot(self, name):
        destination = os.environ.get("CPM_LOCALIZATION_SCREENSHOTS")
        if destination:
            path = Path(destination)
            path.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(
                path=str(path / f"{self.ui_locale}-{name}.png"), animations="disabled"
            )


class OnboardingLocaleUI(Localized, DesktopUI):
    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.split("/api/v1", 1)[1]
        if path == "/profiles" and request.method == "POST":
            body = request.post_data_json
            self.calls.append((request.method, path, request.post_data))
            self.profiles = [profile(1, **body)]
            return route.fulfill(json=self.profiles[0])
        if path.endswith(("/launch", "/close")):
            self.calls.append((request.method, path, request.post_data))
            self.running_profile_ids = ["profile-1"] if path.endswith("/launch") else []
            return route.fulfill(json={"status": "ok"})
        super().route(route)

    def test_first_run_retries_then_creates_runs_and_stops(self):
        self.browser_state.update(
            state="error",
            error="Could not finish installing the browser. Check your connection and retry.",
            message="Could not finish installing the browser. Check your connection and retry.",
        )
        self.page.goto(self.origin)
        expect(self.page.locator("html")).to_have_attribute("lang", self.ui_locale)
        alert = self.page.get_by_role("region", name=self.tr("Browser setup")).get_by_role("alert")
        expect(alert).to_contain_text(self.tr(self.browser_state["error"]))
        expect(alert).to_contain_text(
            self.tr(
                "Check your internet connection, free disk space and permission to write browser files, then retry installation. Setup stays available in Settings."
            )
        )
        self.screenshot("first-run")
        self.assertFalse(any(method == "POST" for method, _, _ in self.calls))
        self.button("Retry installation").click()
        expect(self.page.get_by_role("progressbar")).to_have_attribute("value", "35")
        self.browser_state.update(
            installed=True, state="ready", error=None, version="test", message="Browser is ready."
        )
        self.button("Create first profile").click()
        self.label("Name").fill("Учебный профиль / 测试配置")
        self.button("Create profile").click()
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        self.button("Run").click()
        self.button("Stop").click()
        expect(self.button("Run")).to_be_enabled()
        saved = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "POST" and path == "/profiles"
        )
        self.assertIn(saved["browser_settings"].get("languages"), (None, []))
        self.assertIn(saved["browser_settings"].get("timezone"), (None, ""))
        self.assertIsNone(saved.get("proxy_config"))
        self.assertEqual(sum(path.endswith("/launch") for _, path, _ in self.calls), 1)
        self.assertEqual(sum(path.endswith("/close") for _, path, _ in self.calls), 1)

    def test_language_switch_preserves_draft_focus_and_fingerprint(self):
        self.page.goto(self.origin)
        self.button("New profile").click()
        self.label("Name").fill("Literal $& <name>")
        self.page.get_by_text(
            self.tr("Advanced browser and fingerprint settings"), exact=True
        ).click()
        self.label("Languages").fill("en-US, en")
        self.label("Timezone").fill("Europe/Berlin")
        other = self.context.new_page()
        other.route("**/api/v1/**", self.route)
        other.goto(self.origin)
        other.get_by_label(self.tr("Language"), exact=True).select_option("en")
        dialog = self.page.get_by_role("dialog")
        expect(dialog.get_by_label("Name", exact=True)).to_have_value("Literal $& <name>")
        expect(dialog.get_by_label("Languages", exact=True)).to_have_value("en-US, en")
        expect(dialog.get_by_label("Timezone", exact=True)).to_have_value("Europe/Berlin")
        self.assertFalse(
            any(method in {"POST", "PATCH", "PUT", "DELETE"} for method, _, _ in self.calls)
        )
        self.page.set_viewport_size({"width": 375, "height": 812})
        for locale in (self.ui_locale, "en"):
            other.get_by_role(
                "combobox",
                name="Language" if locale == self.ui_locale else self.tr("Language"),
                exact=True,
            ).select_option(locale)
            expect(self.page.locator("html")).to_have_attribute("lang", locale)
            self.assertLessEqual(
                dialog.evaluate("el => el.scrollWidth"), dialog.evaluate("el => el.clientWidth")
            )
        dialog.get_by_role("button", name="Create profile", exact=True).click()
        saved = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "POST" and path == "/profiles"
        )
        self.assertEqual(saved["browser_settings"]["languages"], ["en-US", "en"])
        self.assertEqual(saved["browser_settings"]["timezone"], "Europe/Berlin")
        self.page.reload()
        expect(self.page.locator("html")).to_have_attribute("lang", "en")

    def test_blocked_storage_keyboard_and_compact_proxy_help(self):
        self.page.add_init_script(
            "Object.defineProperty(window, 'localStorage', {get() { throw new Error('Storage disabled') }})"
        )
        self.page.goto(self.origin + "/proxies/")
        self.label("Language").select_option("en")
        self.page.get_by_label("Language", exact=True).select_option(self.ui_locale)
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.button("New proxy").click()
        dialog = self.page.get_by_role("dialog")
        self.label("Type").select_option("socks5")
        self.label("Name").fill("Fictional proxy")
        self.label("Server").fill("proxy.example:1080")
        self.label("Username").fill("界" * 86)
        self.label("Password (optional)").fill("fictional password")
        expect(dialog.get_by_role("alert")).to_contain_text(
            self.tr("SOCKS5 username and password must each be 1–255 UTF-8 bytes.")
        )
        expect(self.button("Create proxy")).to_be_disabled()
        self.assertLessEqual(
            dialog.evaluate("el => el.scrollWidth"), dialog.evaluate("el => el.clientWidth")
        )
        self.screenshot("proxy-compact")
        for _ in range(16):
            self.page.keyboard.press("Tab")
            self.assertTrue(dialog.evaluate("el => el.contains(document.activeElement)"))
        self.page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        expect(self.button("New proxy")).to_be_focused()
        self.assertFalse(
            any(method in {"POST", "PATCH", "PUT", "DELETE"} for method, _, _ in self.calls)
        )


class TemplateLocaleUI(Localized, TemplateBatchesUI):
    def test_names_and_counts_in_template_batch(self):
        self.page.goto(self.origin + "/templates/")
        self.button("Create batch").click()
        self.label("Name pattern").fill("Демо 测试 {n}")
        self.label("Count").fill("2")
        self.button("Preview batch").click()
        expect(self.page.get_by_text("Демо 测试 001", exact=True)).to_be_visible()
        self.assertEqual(self.creations, [])
        self.button("Create {count} profiles", count=2).click()
        expect(
            self.page.get_by_text(
                self.tr("{created} of {total} profiles created", created=2, total=2), exact=True
            )
        ).to_be_visible()
        self.assertEqual(self.creations[0]["names"], ["Демо 测试 001", "Демо 测试 002"])


class BatchLocaleUI(Localized, BatchOperationsUI):
    def test_review_launch_and_stop_batch(self):
        self.profiles = self.profiles[:2]
        self.page.goto(self.origin)
        self.page.get_by_role(
            "checkbox", name=self.tr("Select all on this page"), exact=True
        ).check()
        self.button("Launch selected").click()
        self.assertEqual(self.operation_calls, [])
        self.button("Launch {count} profiles", count=2).click()
        expect(self.page.get_by_text(self.tr("Completed"), exact=True)).to_have_count(2)
        self.button("Done").click()
        self.button("Stop selected").click()
        self.button("Stop {count} profiles", count=2).click()
        expect(self.page.get_by_text(self.tr("Completed"), exact=True)).to_have_count(2)
        self.assertEqual(self.running_profile_ids, [])


class AccessLocaleUI(Localized, StageThreeUI):
    def test_navigation_grants_activity_and_recovery(self):
        for route, heading in [
            ("/", "Profiles"),
            ("/groups/", "Groups"),
            ("/schedules/", "Schedules"),
            ("/trash/", "Trash"),
            ("/extensions/", "Extension library"),
            ("/access/", "Access and activity"),
        ]:
            self.page.goto(self.origin + route)
            expect(
                self.page.get_by_role("heading", name=self.tr(heading), exact=True)
            ).to_be_visible()
        self.button("Manage grants").click()
        grant = self.page.get_by_role(
            "checkbox", name=self.tr("Assign {profile}", profile="Account 001"), exact=True
        )
        export = self.page.get_by_role(
            "checkbox",
            name=self.tr("Allow export for {profile}", profile="Account 001"),
            exact=True,
        )
        expect(export).to_be_disabled()
        grant.click()
        expect(grant).to_be_checked()
        expect(export).not_to_be_checked()
        expect(export).to_be_enabled()
        self.assertEqual(self.grants, [{"profile_id": "profile-1", "can_export": False}])
        self.page.goto(self.origin + "/settings/")
        self.button("Check for updates").click()
        self.button("Back up and prepare update").click()
        expect(
            self.page.get_by_role("link", name=self.tr("Open release downloads"), exact=True)
        ).to_be_visible()
        self.assertFalse(any(path.endswith(("/apply", "/rollback")) for _, path, _ in self.calls))


class SyncLocaleUI(Localized, SyncUI):
    def test_sync_requires_explicit_consent_and_redacts_failure(self):
        self.page.goto(self.origin + "/settings/")
        create = self.button("Create owner and recovery setup")
        self.label("Device name").fill("Демо / 测试")
        self.label("Relay address").fill("https://relay.example")
        expect(create).to_be_disabled()
        self.assertFalse(
            any(method == "POST" and path.startswith("/sync/") for method, path, _ in self.calls)
        )
        self.page.get_by_label(
            self.tr(
                "I understand that trusted devices receive the selected profiles’ cookies, sessions and proxy credentials."
            ),
            exact=True,
        ).check()
        self.sync_fail = True
        create.click()
        expect(
            self.page.get_by_role("alert").filter(
                has_text=self.tr(
                    "The operation could not finish. Check the connection code, your login and unlock password, then retry. Your existing profiles are preserved."
                )
            )
        ).to_be_visible()
        self.assertNotIn("Private backend diagnostic", self.page.locator("body").inner_text())


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for base in (OnboardingLocaleUI, TemplateLocaleUI, BatchLocaleUI, AccessLocaleUI, SyncLocaleUI):
        for locale in ("ru", "zh-CN"):
            localized = type(f"{base.__name__}_{locale}", (base,), {"ui_locale": locale})
            suite.addTests(localized(name) for name in base.__dict__ if name.startswith("test_"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
