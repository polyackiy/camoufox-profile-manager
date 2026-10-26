"""Batch creation UI: no writes before confirmation and no duplicate work after a lost response."""

import copy
import re
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_two_ui import StageTwoUI


class TemplateBatchesUI(StageTwoUI):
    def setUp(self):
        super().setUp()
        self.templates = [
            {
                "id": "template-one",
                "name": "Work",
                "browser_settings": {"os": "linux"},
                "tags": [],
                "notes": None,
                "extensions": [],
                "row_version": 2,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        self.library.append(
            {
                **self.library[0],
                "id": "library-two",
                "name": "Second",
                "server": "second.example:8080",
            }
        )
        self.previews = []
        self.creations = []
        self.saved = {}
        self.drop_once = False
        self.fail_once = False
        self.duplicate = False

    def route(self, route):
        path = urlsplit(route.request.url).path.split("/api/v1", 1)[1]
        if path.endswith("/batch-preview"):
            body = route.request.post_data_json
            self.previews.append(body)
            if self.duplicate and not body["allow_duplicate_names"]:
                return route.fulfill(
                    status=422,
                    json={
                        "error": {
                            "message": "A profile name already exists; change the names or explicitly allow duplicates"
                        }
                    },
                )
            mapping = [
                {key: proxy[key] for key in ("id", "name", "type", "server")}
                for ref in body["proxy_refs"]
                for proxy in self.library
                if proxy["id"] == ref["id"]
            ]
            return route.fulfill(
                json={"success": True, "data": {"names": body["names"], "proxy_mapping": mapping}}
            )
        if path.endswith("/profile-batches"):
            body = route.request.post_data_json
            self.creations.append(body)
            prior = self.saved.get(body["batch_id"])
            value = prior or {
                "id": body["batch_id"],
                "template_id": "template-one",
                "created_at": "2026-01-01T00:00:00Z",
                "request": copy.deepcopy(body),
                "preview": {
                    "names": body["names"],
                    "proxy_mapping": [
                        {key: proxy[key] for key in ("id", "name", "type", "server")}
                        for ref in body["proxy_refs"]
                        for proxy in self.library
                        if proxy["id"] == ref["id"]
                    ],
                },
                "items": [],
            }
            if not prior:
                value["items"] = [
                    {
                        "position": i,
                        "name": name,
                        "profile_id": f"new-{i}",
                        "status": "created",
                        "error": None,
                    }
                    for i, name in enumerate(body["names"])
                ]
            if self.fail_once or self.drop_once:
                value["items"][-1].update(
                    profile_id=None,
                    status="failed",
                    error="Creation failed. Check the template, proxy and storage before retrying.",
                )
            else:
                for i, row in enumerate(value["items"]):
                    if not row["profile_id"]:
                        row.update(profile_id=f"new-{i}", status="created", error=None)
            self.saved[value["id"]] = copy.deepcopy(value)
            self.fail_once = False
            if self.drop_once:
                self.drop_once = False
                return route.abort("failed")
            return route.fulfill(json={"success": True, "data": value})
        if path == "/template-profile-batches":
            return route.fulfill(
                json={"success": True, "data": {"batches": list(self.saved.values())}}
            )
        if path.startswith("/template-profile-batches/"):
            return route.fulfill(json={"success": True, "data": self.saved[path.rsplit("/", 1)[1]]})
        super().route(route)

    def open_batch(self):
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="Create batch", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Create profiles from template")
        dialog.get_by_label("Count", exact=True).fill("2")
        return dialog

    def test_preview_names_and_proxy_mapping_before_any_creation(self):
        dialog = self.open_batch()
        dialog.get_by_role("checkbox", name=re.compile("Second")).check()
        dialog.get_by_role("checkbox", name=re.compile("Office")).check()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 001", exact=True)).to_be_visible()
        expect(dialog.get_by_text("Work 002", exact=True)).to_be_visible()
        self.assertEqual(self.creations, [])
        self.assertEqual(
            [ref["id"] for ref in self.previews[0]["proxy_refs"]], ["library-two", "library-one"]
        )
        self.assertNotIn("password", str(self.previews[0]))
        dialog.get_by_role("button", name="Create 2 profiles", exact=True).click()
        expect(dialog.get_by_text("2 of 2 profiles created", exact=True)).to_be_visible()
        self.assertEqual(self.creations, self.previews)

    def test_incomplete_proxy_selection_and_duplicate_names_require_correction(self):
        dialog = self.open_batch()
        dialog.get_by_role("checkbox", name=re.compile("Office")).check()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("exactly 2 distinct proxies")
        self.assertEqual(self.previews, [])
        dialog.get_by_role("checkbox", name=re.compile("Office")).uncheck()
        self.duplicate = True
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("already exists")
        self.assertEqual(self.creations, [])
        dialog.get_by_role(
            "checkbox",
            name="Allow duplicate profile names. Existing profiles will never be overwritten.",
        ).check()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_role("button", name="Create 2 profiles", exact=True)).to_be_visible()

    def test_lost_response_retry_retains_the_exact_batch_identifier_and_intent(self):
        self.drop_once = True
        dialog = self.open_batch()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        dialog.get_by_role("button", name="Create 2 profiles", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("Check the saved batch")
        dialog.get_by_role("button", name="Retry same batch", exact=True).click()
        expect(dialog.get_by_text("2 of 2 profiles created", exact=True)).to_be_visible()
        self.assertEqual(len(self.creations), 2)
        self.assertEqual(self.creations[0], self.creations[1])
        self.assertEqual(len(self.saved), 1)

    def test_recent_batch_review_does_not_resume_automatically_after_reload(self):
        self.fail_once = True
        dialog = self.open_batch()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        dialog.get_by_role("button", name="Create 2 profiles", exact=True).click()
        expect(dialog.get_by_text("1 of 2 profiles created", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Done", exact=True).click()
        self.page.reload()
        self.page.get_by_role("button", name="Recent batches", exact=True).click()
        self.page.get_by_role("button", name=re.compile("Review batch")).click()
        dialog = self.page.get_by_role("dialog", name="Create profiles from template")
        expect(dialog.get_by_text("Failed", exact=True)).to_be_visible()
        self.assertEqual(len(self.creations), 1)
        dialog.get_by_role("button", name="Resume incomplete", exact=True).click()
        expect(dialog.get_by_text("2 of 2 profiles created", exact=True)).to_be_visible()
        self.assertEqual(self.creations[0], self.creations[1])

    def test_chinese_creation_flow_preserves_names_and_substitutes_counts(self):
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="批量创建", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="从模板批量创建配置")
        dialog.get_by_label("名称格式", exact=True).fill("中文 {n}")
        dialog.get_by_label("数量", exact=True).fill("2")
        dialog.get_by_role("button", name="预览批次", exact=True).click()
        expect(dialog.get_by_text("中文 001", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="创建 2 个配置", exact=True).click()
        expect(dialog.get_by_text("已创建 2/2 个配置", exact=True)).to_be_visible()
        self.assertEqual(self.creations[0]["names"], ["中文 001", "中文 002"])


if __name__ == "__main__":
    suite = unittest.TestSuite(
        TemplateBatchesUI(name) for name in TemplateBatchesUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
