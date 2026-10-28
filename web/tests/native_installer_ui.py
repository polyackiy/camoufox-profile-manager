"""Installer UI contracts use fictional releases and never install software."""

import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_one_ui import DesktopUI


class NativeInstallerUI(DesktopUI):
    def setUp(self):
        super().setUp()
        self.installer = {"supported": True, "state": "idle"}
        self.install_calls = []

    def route(self, route):
        path = urlsplit(route.request.url).path.split("/api/v1", 1)[1]
        if path == "/system/updates/installer":
            if route.request.method == "POST":
                self.install_calls.append(route.request.post_data_json)
                self.installer = {"supported": True, "state": "downloading", "downloaded": 250, "total": 1000}
            return route.fulfill(json={"success": True, "data": self.installer})
        super().route(route)

    def test_native_installer_requires_confirmation_and_keeps_exact_channel(self):
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_label("Include preview releases (opt-in)").check()
        self.page.get_by_role("button", name="Check for updates", exact=True).click()
        self.page.get_by_role("button", name="Download and install", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Download and install", exact=True)
        expect(dialog).to_contain_text("Save your work")
        self.assertEqual(self.install_calls, [])
        dialog.get_by_role("button", name="Update and restart", exact=True).click()
        expect(self.page.get_by_role("progressbar", name="Installer download progress")).to_have_attribute("value", "250")
        self.assertEqual(self.install_calls, [{"version": "0.6.0-rc.2", "channel": "preview"}])
        expect(self.page.get_by_role("button", name="Download and install", exact=True)).to_be_disabled()
        expect(self.page.get_by_label("Include preview releases (opt-in)")).to_be_disabled()
        self.assertFalse(any("/updates/prepare" in path for _, path, _ in self.calls))
        self.installer.update(state="error", error="Could not download or verify the installer. Check your connection and free disk space, then try again.")
        expect(self.page.get_by_role("alert").filter(has_text="Could not download")).to_be_visible()
        expect(self.page.get_by_role("button", name="Download and install", exact=True)).to_be_enabled()
        self.assertEqual(len(self.install_calls), 1)

    def test_native_installer_chinese_confirmation_and_cancel(self):
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        self.page.get_by_role("button", name="检查更新", exact=True).click()
        self.page.get_by_role("button", name="下载并安装", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="下载并安装", exact=True)
        expect(dialog).to_contain_text("关闭配置、备份数据、安装更新并重新打开")
        destination = os.environ.get("CPM_INSTALLER_SCREENSHOTS")
        if destination:
            path = Path(destination)
            path.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path / "native-installer-zh-CN.png"), animations="disabled")
        dialog.get_by_role("button", name="取消", exact=True).click()
        self.assertEqual(self.install_calls, [])
        expect(dialog).not_to_be_visible()


if __name__ == "__main__":
    suite = unittest.TestSuite(NativeInstallerUI(name) for name in unittest.defaultTestLoader.getTestCaseNames(NativeInstallerUI) if name.startswith("test_native_installer_"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
