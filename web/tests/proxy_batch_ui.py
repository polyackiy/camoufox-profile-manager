"""Offline proxy batch checks against the static UI; no real proxy traffic."""

import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_one_ui import DesktopUI


class ProxyBatchUI(DesktopUI):
    def setUp(self):
        super().setUp()
        self.library = [
            {**self.library[0], "id": f"proxy-{i}", "name": f"Proxy {i}"}
            for i in range(1, 6)
        ]
        self.checked_ids = []
        self.pending = []
        self.hold = False
        self.deny = False

    def route(self, route):
        path = urlsplit(route.request.url).path.split("/api/v1", 1)[1]
        if path.startswith("/proxies/") and path.endswith("/check"):
            proxy_id = path.split("/")[-2]
            self.checked_ids.append(proxy_id)
            if self.deny:
                return route.fulfill(status=403, json={"detail": "Access denied"})
            if self.hold:
                self.pending.append((route, proxy_id))
                return
            return self.complete(route, proxy_id)
        super().route(route)

    def complete(self, route, proxy_id):
        if proxy_id == "proxy-3":
            return route.fulfill(status=503, json={"detail": "private-proxy-password"})
        proxy = next(item for item in self.library if item["id"] == proxy_id)
        proxy["last_check"] = {
            "reachable": proxy_id != "proxy-2",
            "error": None,
            "latency_ms": 18,
            "ip": "203.0.113.4" if proxy_id != "proxy-2" else None,
            "country": "DE",
            "timezone": "Europe/Berlin",
            "findings": [],
            "checked_at": "2026-10-28T10:00:00Z",
        }
        route.fulfill(json={"success": True, "data": proxy})

    def open_library(self):
        self.page.goto(self.origin + "/proxies/")
        expect(self.page.get_by_role("checkbox", name="Select proxy Proxy 1", exact=True)).to_be_visible()

    def open_batch(self):
        self.page.get_by_role("button", name="Batch check", exact=True).click()
        return self.page.get_by_role("dialog", name="Batch proxy check", exact=True)

    def test_proxy_batch_selection_and_unreachable_results(self):
        self.open_library()
        for i in range(1, 4):
            self.page.get_by_role("checkbox", name=f"Select proxy Proxy {i}", exact=True).check()
        dialog = self.open_batch()
        self.assertEqual(self.checked_ids, [])
        dialog.get_by_role("button", name="Start checks", exact=True).click()
        expect(dialog.get_by_role("status")).to_contain_text("Batch finished")
        self.assertCountEqual(self.checked_ids, ["proxy-1", "proxy-2", "proxy-3"])
        expect(dialog).to_contain_text("Reachable: 1; unreachable: 1; check errors: 1; cancelled: 0.")
        expect(dialog.get_by_text("Healthy", exact=True)).to_be_visible()
        expect(dialog.get_by_text("Failing", exact=True)).to_be_visible()
        expect(dialog).not_to_contain_text("private-proxy-password")
        dialog.get_by_role("button", name="Close", exact=True).last.click()
        expect(self.page.get_by_role("row").filter(has_text="Proxy 2")).to_contain_text("Failing")

    def test_proxy_batch_search_freezes_the_targets(self):
        self.open_library()
        self.page.get_by_role("textbox", name="Search library proxies", exact=True).fill("Proxy 4")
        dialog = self.open_batch()
        expect(dialog.get_by_role("listitem")).to_have_count(1)
        dialog.get_by_role("button", name="Start checks", exact=True).click()
        expect(dialog.get_by_role("status")).to_contain_text("Batch finished")
        self.assertEqual(self.checked_ids, ["proxy-4"])

    def test_proxy_batch_cancel_and_close_keep_active_checks_bounded(self):
        self.hold = True
        self.open_library()
        dialog = self.open_batch()
        dialog.get_by_label("Simultaneous checks", exact=True).select_option("2")
        dialog.get_by_role("button", name="Start checks", exact=True).click()
        expect(dialog.get_by_text("Checking…", exact=True)).to_have_count(2)
        self.assertEqual(len(self.checked_ids), 2)
        dialog.get_by_role("button", name="Cancel waiting", exact=True).click()
        expect(dialog.get_by_text("Cancelled", exact=True)).to_have_count(3)
        dialog.get_by_role("button", name="Close", exact=True).last.click()
        expect(self.page.get_by_role("button", name="Batch check", exact=True)).to_be_disabled()
        for pending in self.pending:
            self.complete(*pending)
        expect(self.page.get_by_role("button", name="Batch check", exact=True)).to_be_enabled()
        expect(self.page.get_by_role("row").filter(has_text="Proxy 1")).to_contain_text("Healthy")
        self.assertEqual(len(self.checked_ids), 2)

    def test_proxy_batch_access_loss_stops_waiting_checks(self):
        self.deny = True
        self.open_library()
        dialog = self.open_batch()
        dialog.get_by_label("Simultaneous checks", exact=True).select_option("1")
        dialog.get_by_role("button", name="Start checks", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(self.page.get_by_role("button", name="Batch check", exact=True)).to_be_visible()
        self.assertEqual(self.checked_ids, ["proxy-1"])

    def test_proxy_batch_chinese_and_limit_are_visible(self):
        self.library *= 21
        self.library = [{**item, "id": f"item-{i}", "name": f"Proxy {i}"} for i, item in enumerate(self.library, 1)]
        self.open_library()
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        self.page.get_by_role("button", name="批量检测", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="批量检测代理", exact=True)
        expect(dialog.get_by_role("alert")).to_contain_text("每批请选择 1 至 100 个代理")
        expect(dialog.get_by_role("button", name="开始检测", exact=True)).to_be_disabled()
        self.assertEqual(self.checked_ids, [])

    def test_proxy_batch_chinese_results(self):
        self.open_library()
        self.page.get_by_label("Language", exact=True).select_option("zh-CN")
        self.page.get_by_role("button", name="批量检测", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="批量检测代理", exact=True)
        dialog.get_by_role("button", name="开始检测", exact=True).click()
        expect(dialog.get_by_role("status")).to_contain_text("批量操作已结束")
        expect(dialog).to_contain_text("可连通：3；无法连通：1；检测出错：1；已取消：0。")
        expect(dialog.get_by_label("并发检测数", exact=True)).to_be_disabled()
        destination = os.environ.get("CPM_PROXY_BATCH_SCREENSHOTS")
        if destination:
            path = Path(destination)
            path.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path / "proxy-batch-zh-CN.png"), animations="disabled")


if __name__ == "__main__":
    suite = unittest.TestSuite(ProxyBatchUI(name) for name in unittest.defaultTestLoader.getTestCaseNames(ProxyBatchUI) if name.startswith("test_proxy_batch_"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
