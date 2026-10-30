"""One-attempt GitHub authentication with fictional tokens and offline routes."""

import json
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_one_ui import DesktopUI

RATE_LIMIT = "GitHub's download request limit was reached. Wait and retry, or use a GitHub token below."
TOKEN = "github_pat_fictional_ui_token"


class BrowserDownloadAuthUI(DesktopUI):
    def setUp(self):
        super().setUp()
        self.install_requests = []

    def route(self, route):
        path = urlsplit(route.request.url).path.split("/api/v1", 1)[1]
        if path == "/system/browser/install":
            body = route.request.post_data_json if route.request.post_data else None
            self.install_requests.append(body)
            if len(self.install_requests) == 1:
                self.browser_state = {"installed": False, "state": "error", "error": RATE_LIMIT}
            else:
                self.browser_state = {"installed": False, "state": "downloading", "progress": 20}
            return route.fulfill(json={"data": self.browser_state})
        super().route(route)

    def exercise_authentication(self, locale="en"):
        catalog = {} if locale == "en" else json.loads(
            (Path(__file__).parents[1] / "src/lib/locales" / f"{locale}.json").read_text()
        )

        def tr(source):
            return catalog.get(source, source)

        self.page.goto(self.origin + "/settings/")
        if locale != "en":
            self.page.get_by_label("Language", exact=True).select_option(locale)
        self.page.get_by_role("button", name=tr("Install browser"), exact=True).click()
        expect(self.page.get_by_role("alert").filter(has_text=tr(RATE_LIMIT))).to_be_visible()
        self.page.get_by_text(tr("GitHub download authentication (optional)"), exact=True).click()
        token = self.page.get_by_label(tr("GitHub token"), exact=True)
        expect(token).to_have_attribute("type", "password")
        expect(self.page.get_by_role("link", name=tr("Create a GitHub token"), exact=True)).to_have_attribute(
            "href", "https://github.com/settings/personal-access-tokens/new"
        )
        token.fill(TOKEN)
        self.page.get_by_role("button", name=tr("Retry installation"), exact=True).click()
        expect(token).to_have_value("")
        expect(token).to_be_disabled()
        self.assertEqual(self.install_requests, [None, {"github_token": TOKEN}])
        storage = self.page.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])")
        self.assertNotIn(TOKEN, storage)
        self.assertNotIn(TOKEN, self.page.locator("body").inner_text())
        self.browser_state = {"installed": True, "version": "test-browser", "state": "ready"}
        retry = self.page.get_by_role("button", name=tr("Retry download"), exact=True)
        expect(retry).to_be_visible()
        retry.click()
        expect(self.page.get_by_role("progressbar", name=tr("Browser installation progress"))).to_be_visible()
        self.assertEqual(self.install_requests[-1], None)

    def test_github_download_auth_english(self):
        self.exercise_authentication()

    def test_github_download_auth_russian(self):
        self.exercise_authentication("ru")

    def test_github_download_auth_chinese(self):
        self.exercise_authentication("zh-CN")


if __name__ == "__main__":
    suite = unittest.TestSuite(
        BrowserDownloadAuthUI(name)
        for name in unittest.defaultTestLoader.getTestCaseNames(BrowserDownloadAuthUI)
        if name.startswith("test_github_download_auth_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
