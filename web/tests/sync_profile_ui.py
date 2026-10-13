"""Profile handoff UI contracts. Static build and browser run only in CI."""

import json
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_three_ui import StageThreeUI
from sync_ui import code


class SyncProfileUI(StageThreeUI):
    def setUp(self):
        super().setUp()
        self.bindings = []
        self.available = []
        self.sync_fail = False
        self.vault_kind = "native"
        self.checkpoint = {
            "profile_id": "remote-profile",
            "holder_device_id": "this-device",
            "revision_id": "revision-one",
            "checkout_generation": 1,
            "membership_epoch": 1,
        }

    def binding(self, phase):
        return {
            "local_id": "profile-1",
            "name": "Account 001",
            "phase": phase,
            "checkpoint": self.checkpoint,
            "installed_revision": "revision-one",
            "needs_snapshot": False,
        }

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.split("/api/v1", 1)[1]
        if path.startswith("/sync/"):
            self.calls.append((request.method, path, request.post_data))
            self.assertNotIn("x-api-key", request.headers)
            if path == "/sync/status":
                return route.fulfill(
                    json={"phase": "ready", "authority": True, "vault_kind": self.vault_kind}
                )
            if path == "/sync/profiles" and request.method == "GET":
                return route.fulfill(json={"profiles": self.bindings, "available": self.available})
            if self.sync_fail:
                return route.fulfill(status=409, json={"detail": "Sensitive internal diagnostic"})
            if path.endswith("/preserve-copy"):
                self.assertTrue(json.loads(request.post_data)["acknowledge_fork"])
                return route.fulfill(json={"local_id": "copy0001", "name": "Recovered copy"})
            if path == "/sync/profiles/accept-code":
                self.assertEqual(json.loads(request.post_data)["pack"]["signature"], "owner-signed")
                self.available = [{"profile_id": "remote-profile", "checkpoint": self.checkpoint}]
                return route.fulfill(json={"accepted": True, "added": 1, "sequence": 2})
            if path.endswith("/enable"):
                self.bindings = [self.binding("held")]
            elif path.endswith("/handoff"):
                self.bindings = [self.binding("released")]
            elif path.endswith("/acquire") or path.endswith("/retry"):
                self.bindings = [self.binding("held")]
            elif path == "/sync/profiles/import":
                self.assertEqual(json.loads(request.post_data)["profile_id"], "remote-profile")
                self.bindings = [self.binding("apply_required")]
            else:
                return route.fulfill(status=404, json={"detail": "Unknown fixture"})
            return route.fulfill(json=self.bindings[0])
        super().route(route)

    def open_profile_sync(self):
        self.page.goto(self.origin + "/")
        self.page.get_by_role("button", name="Actions for Account 001").click()
        self.page.get_by_role("menuitem", name="Device synchronization", exact=True).click()
        return self.page.get_by_role("dialog", name="Synchronize Account 001", exact=True)

    def test_enabling_profile_requires_explicit_secret_sharing_consent(self):
        dialog = self.open_profile_sync()
        enable = dialog.get_by_role("button", name="Enable synchronization for this profile")
        expect(enable).to_be_disabled()
        dialog.get_by_label("Allow my trusted devices to receive", exact=False).check()
        enable.click()
        expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()

    def test_handoff_is_confirmed_and_disables_launch(self):
        self.bindings = [self.binding("held")]
        dialog = self.open_profile_sync()
        dialog.get_by_role("button", name="Send to another device").click()
        confirmation = self.page.get_by_role("dialog", name="Send this profile to another device?")
        expect(confirmation).to_contain_text("This device will stop being allowed to open it")
        confirmation.get_by_role("button", name="Send profile", exact=True).click()
        expect(dialog.get_by_text("Ready for another device", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Close", exact=True).click()
        expect(
            self.page.get_by_role("row")
            .filter(has_text="Account 001")
            .get_by_role("button", name="Run", exact=True)
        ).to_be_disabled()

    def test_conflict_preserves_both_copies_and_offers_no_overwrite(self):
        self.bindings = [self.binding("conflict")]
        dialog = self.open_profile_sync()
        expect(dialog).to_contain_text("do not overwrite or delete either copy")
        expect(dialog.get_by_role("button", name="Get latest profile")).to_have_count(0)
        expect(
            dialog.get_by_role("button", name="Enable synchronization for this profile")
        ).to_have_count(0)

    def test_uncertain_transfer_resumes_existing_operation(self):
        item = self.binding("pending")
        item["pending"] = {"kind": "commit", "operation_id": "same-operation"}
        self.bindings = [item]
        dialog = self.open_profile_sync()
        dialog.get_by_role("button", name="Resume pending transfer").click()
        expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()
        self.assertTrue(any(path.endswith("/retry") for _, path, _ in self.calls))

    def test_import_requires_separate_get_latest_action(self):
        self.available = [{"profile_id": "remote-profile", "checkpoint": self.checkpoint}]
        self.page.goto(self.origin + "/")
        self.page.get_by_role("button", name="Profiles from trusted devices", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Profiles from trusted devices", exact=True)
        dialog.get_by_role("button", name="Add trusted profile to this device").click()
        expect(dialog.get_by_text("Latest profile must be installed", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Get latest profile").click()
        expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()

    def test_existing_device_accepts_new_owner_profile_list(self):
        self.page.goto(self.origin + "/")
        self.page.get_by_role("button", name="Profiles from trusted devices", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Profiles from trusted devices", exact=True)
        field = dialog.get_by_label("Updated profile list code from the owner", exact=True)
        field.fill(code("profiles", {"body": {"sequence": 2}, "signature": "owner-signed"}))
        dialog.get_by_role("button", name="Verify updated profile list").click()
        expect(field).to_have_value("")
        expect(
            dialog.get_by_role("button", name="Add trusted profile to this device")
        ).to_be_visible()
        self.assertFalse(any("pairing" in path for _, path, _ in self.calls))

    def test_failed_transfer_clears_unlock_password_and_hides_diagnostic(self):
        self.bindings = [self.binding("released")]
        self.vault_kind = "encrypted_file"
        self.sync_fail = True
        dialog = self.open_profile_sync()
        password = dialog.get_by_label("Unlock password", exact=True)
        password.fill("an-explicit-long-password")
        dialog.get_by_role("button", name="Get latest profile").click()
        expect(dialog.get_by_role("alert")).to_contain_text("Your existing profile is preserved")
        expect(password).to_have_value("")
        expect(dialog).not_to_contain_text("Sensitive internal diagnostic")

    def test_conflict_copy_requires_duplicate_identity_confirmation(self):
        self.bindings = [self.binding("conflict")]
        dialog = self.open_profile_sync()
        dialog.get_by_role("button", name="Preserve as a new local copy").click()
        confirmation = self.page.get_by_role(
            "dialog", name="Create a separate local recovery copy?"
        )
        expect(confirmation).to_contain_text("Another device may still be running")
        confirmation.get_by_role("button", name="Create local copy", exact=True).click()
        expect(
            dialog.get_by_text(
                "A separate local copy was created. The original sync state is preserved."
            )
        ).to_be_visible()
        expect(
            dialog.get_by_text("Conflicting history — opening blocked", exact=True)
        ).to_be_visible()


if __name__ == "__main__":
    suite = unittest.TestSuite(
        SyncProfileUI(name) for name in SyncProfileUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
