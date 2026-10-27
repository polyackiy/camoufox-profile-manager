"""Workflow editor/review/recovery contracts; fictional API, isolated browser."""

import copy
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_three_ui import StageThreeUI


class WorkflowsUI(StageThreeUI):
    ui_locale = "en"

    def setUp(self):
        super().setUp()
        self.catalog = (
            {}
            if self.ui_locale == "en"
            else json.loads(
                (
                    Path(__file__).parents[1] / "src/lib/locales" / (self.ui_locale + ".json")
                ).read_text()
            )
        )
        self.context.close()
        self.context = self.browser.new_context(
            locale=self.ui_locale, viewport={"width": 1280, "height": 900}
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self.page.route("**/api/v1/**", self.route)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.definition = {
            "name": "Daily demo",
            "steps": [
                {"name": "Start", "action": "launch"},
                {
                    "name": "Visit",
                    "action": "navigate",
                    "url": "https://example.test/?definition=private",
                },
                {"name": "Finish", "action": "close"},
            ],
            "concurrency": 1,
            "timing": None,
            "schedule_enabled": False,
            "scheduled_targets": [],
        }
        self.workflow = {
            "id": "workflow-one",
            "revision": 1,
            "definition": copy.deepcopy(self.definition),
            "next_run_at": None,
        }
        self.workflows = [self.workflow]
        self.workflow_calls = []
        self.runs = []
        self.start_failed_once = False
        self.save_stale = False

    def tearDown(self):
        self.assertEqual(self.errors, [])
        super().tearDown()

    def tr(self, source):
        value = self.catalog.get(source, source)
        return value if isinstance(value, str) else value["other"]

    def button(self, source):
        return self.page.get_by_role("button", name=self.tr(source), exact=True)

    def label(self, source):
        return self.page.get_by_label(self.tr(source), exact=True)

    def screenshot(self, name):
        output = os.environ.get("CPM_WORKFLOW_SCREENSHOTS")
        if output:
            path = Path(output)
            path.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(
                path=str(path / (self.ui_locale + "-workflow-" + name + ".png")),
                animations="disabled",
            )

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.split("/api/v1", 1)[1]
        if not path.startswith("/workflows"):
            return super().route(route)
        self.workflow_calls.append(
            (request.method, path, request.post_data_json if request.post_data else None)
        )
        body = request.post_data_json if request.post_data else {}
        if path == "/workflows":
            if request.method == "POST":
                value = {
                    "id": "new-workflow",
                    "revision": 1,
                    "definition": body,
                    "next_run_at": None,
                }
                self.workflows.append(value)
                return route.fulfill(status=201, json=value)
            return route.fulfill(json={"workflows": self.workflows})
        if path == "/workflows/workflow-one" and request.method == "PUT":
            if self.save_stale:
                return route.fulfill(
                    status=409,
                    json={"detail": "Workflow changed. Reload before saving or running."},
                )
            self.workflow.update(
                definition={key: value for key, value in body.items() if key != "revision"},
                revision=2,
            )
            return route.fulfill(json=self.workflow)
        if path.endswith("/cancel"):
            next(run for run in self.runs if run["id"] == path.split("/")[-2])[
                "cancel_requested"
            ] = True
            return route.fulfill(json={"success": True})
        if path.endswith("/runs"):
            if request.method == "POST":
                prior = next((run for run in self.runs if run["id"] == body["request_id"]), None)
                if not prior:
                    prior = {
                        "id": body["request_id"],
                        "workflow_id": "workflow-one",
                        "revision": 1,
                        "state": "running",
                        "cancel_requested": False,
                        "started_at": "2026-10-26T12:00:00",
                        "finished_at": None,
                        "targets": [
                            {
                                **target,
                                "state": "running",
                                "message": None,
                                "steps": [
                                    {
                                        "name": step["name"],
                                        "action": step["action"],
                                        "state": "running" if index == 0 else "queued",
                                    }
                                    for index, step in enumerate(self.definition["steps"])
                                ],
                            }
                            for target in body["targets"]
                        ],
                    }
                    self.runs.insert(0, prior)
                if self.start_failed_once:
                    self.start_failed_once = False
                    return route.abort("failed")
                return route.fulfill(status=202, json=prior)
            return route.fulfill(json={"runs": self.runs})
        return route.fulfill(json={"success": True})

    def open(self):
        self.page.goto(self.origin + "/schedules/")
        expect(
            self.page.get_by_role("heading", name=self.tr("Automation workflows"), exact=True)
        ).to_be_visible()

    def review(self):
        self.open()
        self.button("Review and run").click()
        dialog = self.page.get_by_role("dialog", name=self.tr("Review workflow run"))
        expect(
            dialog.get_by_text("https://example.test/?definition=private", exact=True)
        ).to_be_visible()
        dialog.get_by_role("checkbox", name="Account 001", exact=True).check()
        return dialog

    def test_editor_is_declarative_and_save_never_runs_it(self):
        self.open()
        self.button("New workflow").click()
        self.label("Workflow name").fill("Утро / 早晨")
        self.button("Add navigation").click()
        self.label("Destination URL").fill("https://example.test/")
        self.button("Add wait").click()
        self.label("Wait seconds").fill("3")
        self.page.get_by_role(
            "checkbox", name=self.tr("Enable workflow schedule"), exact=True
        ).check()
        self.page.get_by_role("checkbox", name="Account 002", exact=True).check()
        self.label("When").select_option("daily")
        self.label("Time (server clock)").fill("09:15")
        self.button("Mon").click()
        self.button("Fri").click()
        self.button("Save workflow").click()
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        saved = next(
            body
            for method, path, body in self.workflow_calls
            if method == "POST" and path == "/workflows"
        )
        self.assertEqual(
            [step["action"] for step in saved["steps"]], ["launch", "navigate", "wait", "close"]
        )
        self.assertEqual(
            saved["scheduled_targets"], [{"profile_id": "profile-2", "row_version": 3}]
        )
        self.assertEqual(saved["timing"], {"kind": "daily", "at_time": "09:15", "days": [0, 4]})
        self.assertEqual(self.runs, [])

    def test_review_shows_hidden_selected_targets_before_explicit_start(self):
        dialog = self.review()
        self.label("Search profiles").fill("Account 002")
        expect(dialog.get_by_role("list", name=self.tr("Selected profiles"))).to_contain_text(
            "Account 001"
        )
        self.screenshot("run-review")
        self.assertEqual(self.runs, [])
        self.button("Clear selection").click()
        expect(self.button("Start workflow")).to_be_disabled()
        self.label("Search profiles").fill("")
        dialog.get_by_role("checkbox", name="Account 001", exact=True).check()
        self.button("Start workflow").click()
        expect(self.page.get_by_role("dialog", name=self.tr("Run history"))).to_be_visible()
        expect(self.page.get_by_role("progressbar")).to_have_attribute("value", "0")
        self.assertNotIn("definition=private", self.page.get_by_role("dialog").inner_text())
        self.runs[0].update(state="completed", finished_at="2026-10-26T12:01:00")
        self.runs[0]["targets"][0]["state"] = "completed"
        for step in self.runs[0]["targets"][0]["steps"]:
            step["state"] = "completed"
        self.page.get_by_role("dialog").get_by_role(
            "button", name=self.tr("Refresh"), exact=True
        ).click()
        expect(self.page.get_by_role("progressbar")).to_have_attribute("value", "1")
        expect(self.button("Review incomplete profiles")).to_have_count(0)

    def test_lost_response_preserves_request_id_and_draft(self):
        dialog = self.review()
        self.start_failed_once = True
        self.button("Start workflow").click()
        expect(dialog.get_by_role("alert")).to_be_visible()
        expect(dialog.get_by_role("checkbox", name="Account 001", exact=True)).to_be_checked()
        self.button("Start workflow").click()
        expect(self.page.get_by_role("dialog", name=self.tr("Run history"))).to_be_visible()
        starts = [
            body
            for method, path, body in self.workflow_calls
            if method == "POST" and path.endswith("/runs")
        ]
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0], starts[1])
        self.assertEqual(len(self.runs), 1)

    def test_retry_needs_confirmation_and_excludes_successful_profiles(self):
        dialog = self.review()
        dialog.get_by_role("checkbox", name="Account 002", exact=True).check()
        self.button("Start workflow").click()
        self.runs[0]["state"] = "partial"
        for index, target in enumerate(self.runs[0]["targets"]):
            target["state"] = "completed" if index == 0 else "failed"
        self.page.get_by_role("dialog").get_by_role(
            "button", name=self.tr("Refresh"), exact=True
        ).click()
        self.button("Review incomplete profiles").click()
        dialog = self.page.get_by_role("dialog", name=self.tr("Review workflow run"))
        expect(dialog.get_by_role("checkbox", name="Account 001", exact=True)).to_have_count(0)
        expect(dialog.get_by_role("checkbox", name="Account 002", exact=True)).to_be_checked()
        expect(self.button("Start workflow")).to_be_disabled()
        dialog.get_by_role("checkbox").last.check()
        self.button("Start workflow").click()
        starts = [
            body
            for method, path, body in self.workflow_calls
            if method == "POST" and path.endswith("/runs")
        ]
        self.assertEqual(starts[-1]["targets"], [{"profile_id": "profile-2", "row_version": 3}])
        self.assertTrue(starts[-1]["replay_confirmed"])
        self.assertEqual(starts[-1]["retry_of"], starts[0]["request_id"])

    def test_cancel_reports_requested_while_accepted_work_drains(self):
        self.review()
        self.button("Start workflow").click()
        self.button("Cancel remaining work").click()
        expect(self.button("Cancel remaining work")).to_be_disabled()
        expect(self.button("Review incomplete profiles")).to_have_count(0)
        self.assertEqual(self.runs[0]["state"], "running")
        cancellations = [
            path
            for method, path, _ in self.workflow_calls
            if method == "POST" and path.endswith("/cancel")
        ]
        self.assertEqual(len(cancellations), 1)

    def test_stale_edit_preserves_input_and_compact_keyboard_controls(self):
        self.open()
        self.button("Edit").first.click()
        self.label("Workflow name").fill("Unsent draft")
        self.save_stale = True
        self.button("Save workflow").click()
        dialog = self.page.get_by_role("dialog", name=self.tr("Edit workflow"))
        expect(dialog.get_by_role("alert")).to_contain_text(
            self.tr("Workflow changed. Reload before saving or running.")
        )
        expect(self.label("Workflow name")).to_have_value("Unsent draft")
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.assertLessEqual(
            dialog.evaluate("el => el.scrollWidth"), dialog.evaluate("el => el.clientWidth")
        )
        self.label("Workflow name").focus()
        for _ in range(18):
            self.page.keyboard.press("Tab")
            self.assertTrue(dialog.evaluate("el => el.contains(document.activeElement)"))
        self.screenshot("editor")
        self.page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)

    def test_operator_can_use_own_workflows_but_viewer_cannot_open_workspace(self):
        self.role = "operator"
        self.open()
        expect(self.page.get_by_text("Daily demo", exact=True)).to_be_visible()
        expect(self.page.get_by_role("link", name=self.tr("Schedules"), exact=True)).to_be_visible()
        self.role = "viewer"
        self.workflow_calls.clear()
        self.page.reload()
        expect(self.page.get_by_text(self.tr("Access denied"), exact=True)).to_be_visible()
        self.assertEqual(self.workflow_calls, [])


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for locale in ("en", "ru", "zh-CN"):
        localized = type("WorkflowsUI_" + locale, (WorkflowsUI,), {"ui_locale": locale})
        suite.addTests(localized(name) for name in WorkflowsUI.__dict__ if name.startswith("test_"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
