"""Offline batch-operation UI contracts, run against the static export in CI."""

import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_three_ui import StageThreeUI
from stage_two_ui import profile


class BatchOperationsUI(StageThreeUI):
    def setUp(self):
        super().setUp()
        self.profiles = [profile(i) for i in range(1, 5)]
        self.operation_calls = []
        self.pending = []
        self.hold = False
        self.fail_once = None
        self.deny = False
        self.browser_state.update(installed=True, state="ready", version="test")

    def route(self, route):
        path = urlsplit(route.request.url).path.split("/api/v1", 1)[1]
        if path.startswith("/profiles/") and path.endswith(("/launch", "/close")):
            profile_id, action = path.split("/")[-2:]
            self.operation_calls.append((profile_id, action))
            if self.deny:
                self.profiles = []
                return route.fulfill(status=403, json={"error": {"message": "Access denied"}})
            if self.fail_once == profile_id:
                self.fail_once = None
                return route.fulfill(status=500, json={"detail": "private-provider-password"})
            if self.hold:
                self.pending.append((route, profile_id, action))
                return
            return self.complete(route, profile_id, action)
        super().route(route)

    def complete(self, route, profile_id, action):
        if action == "launch" and profile_id not in self.running_profile_ids:
            self.running_profile_ids.append(profile_id)
        if action == "close":
            self.running_profile_ids = [
                item for item in self.running_profile_ids if item != profile_id
            ]
        route.fulfill(json={"status": "launched" if action == "launch" else "closed"})

    def select_profiles(self):
        self.page.goto(self.origin + "/")
        self.page.get_by_role("checkbox", name="Select all on this page", exact=True).check()

    def test_preview_cancel_and_retry_preserve_completed_work(self):
        self.hold = True
        self.select_profiles()
        self.page.get_by_role("button", name="Launch selected", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Launch selected profiles")
        expect(dialog.get_by_text("Account 001", exact=True)).to_be_visible()
        self.assertEqual(self.operation_calls, [])
        dialog.get_by_label("Simultaneous operations").select_option("1")
        dialog.get_by_role("button", name="Launch 4 profiles", exact=True).click()
        expect(dialog.get_by_text("In progress", exact=True)).to_have_count(1)
        self.assertEqual(len(self.operation_calls), 1)
        dialog.get_by_role("button", name="Cancel waiting", exact=True).click()
        expect(dialog.get_by_text("Cancelled", exact=True)).to_have_count(3)
        expect(dialog.get_by_text("In progress", exact=True)).to_have_count(1)
        self.complete(*self.pending.pop(0))
        expect(dialog.get_by_text("Batch finished", exact=False)).to_be_visible()
        self.hold = False
        dialog.get_by_role("button", name="Retry 3 incomplete", exact=True).click()
        expect(dialog.get_by_text("Completed", exact=True)).to_have_count(4)
        self.assertEqual(self.operation_calls, [(f"profile-{i}", "launch") for i in range(1, 5)])
        dialog.get_by_role("button", name="Done", exact=True).click()
        expect(dialog).not_to_be_visible()

    def test_partial_failure_is_redacted_and_retry_targets_only_the_failure(self):
        self.fail_once = "profile-2"
        self.select_profiles()
        self.page.get_by_role("button", name="Launch selected", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Launch selected profiles")
        dialog.get_by_role("button", name="Launch 4 profiles", exact=True).click()
        expect(dialog.get_by_role("button", name="Retry 1 incomplete", exact=True)).to_be_visible()
        expect(dialog.get_by_text("Completed", exact=True)).to_have_count(3)
        expect(dialog.get_by_text("private-provider-password", exact=False)).not_to_be_visible()
        dialog.get_by_role("button", name="Retry 1 incomplete", exact=True).click()
        expect(dialog.get_by_text("Completed", exact=True)).to_have_count(4)
        self.assertEqual(self.operation_calls.count(("profile-2", "launch")), 2)
        for i in (1, 3, 4):
            self.assertEqual(self.operation_calls.count((f"profile-{i}", "launch")), 1)

    def test_stop_requires_review_and_closes_only_selected_profiles(self):
        self.running_profile_ids = [f"profile-{i}" for i in range(1, 5)]
        self.page.goto(self.origin + "/")
        self.page.get_by_role("checkbox", name="Select Account 002", exact=True).check()
        self.page.get_by_role("button", name="Stop selected", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Stop selected profiles")
        expect(dialog.get_by_text("Save unfinished work", exact=False)).to_be_visible()
        self.assertEqual(self.operation_calls, [])
        dialog.get_by_role("button", name="Stop 1 profile", exact=True).click()
        expect(dialog.get_by_text("Completed", exact=True)).to_have_count(1)
        self.assertEqual(self.operation_calls, [("profile-2", "close")])
        self.assertEqual(self.running_profile_ids, ["profile-1", "profile-3", "profile-4"])

    def test_closing_the_dialog_cancels_the_undispatched_tail(self):
        self.hold = True
        self.select_profiles()
        self.page.get_by_role("button", name="Launch selected", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Launch selected profiles")
        dialog.get_by_label("Simultaneous operations").select_option("1")
        dialog.get_by_role("button", name="Launch 4 profiles", exact=True).click()
        expect(dialog.get_by_text("In progress", exact=True)).to_have_count(1)
        dialog.get_by_role("button", name="Close", exact=True).last.click()
        expect(dialog).not_to_be_visible()
        self.complete(*self.pending.pop(0))
        expect(self.page.get_by_role("checkbox", name="Select all on this page")).to_be_visible()
        self.assertEqual(self.operation_calls, [("profile-1", "launch")])

    def test_revoked_access_stops_the_rest_of_an_operator_queue(self):
        self.role = "operator"
        for item in self.profiles:
            item["permissions"] = {"view": True, "edit": True, "launch": True, "export": False}
        self.select_profiles()
        self.page.get_by_role("button", name="Launch selected", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Launch selected profiles")
        dialog.get_by_label("Simultaneous operations").select_option("1")
        self.deny = True
        dialog.get_by_role("button", name="Launch 4 profiles", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(self.page.get_by_text("No profiles yet", exact=True)).to_be_visible()
        self.assertEqual(self.operation_calls, [("profile-1", "launch")])

    def test_viewers_do_not_receive_batch_browser_controls(self):
        self.role = "viewer"
        for item in self.profiles:
            item["permissions"] = {"view": True, "edit": False, "launch": False, "export": False}
        self.page.goto(self.origin + "/")
        expect(self.page.get_by_text("Account 001", exact=True)).to_be_visible()
        expect(self.page.get_by_role("checkbox", name="Select all on this page")).to_be_disabled()
        expect(
            self.page.get_by_role("button", name="Launch selected", exact=True)
        ).not_to_be_visible()
        expect(
            self.page.get_by_role("button", name="Stop selected", exact=True)
        ).not_to_be_visible()
        self.assertEqual(self.operation_calls, [])


if __name__ == "__main__":
    suite = unittest.TestSuite(
        BatchOperationsUI(name) for name in BatchOperationsUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
