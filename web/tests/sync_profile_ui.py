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
        self.remote_trash = False
        self.vault_kind = "native"
        self.authority = True
        self.retention_decisions = []
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
                    json={
                        "phase": "ready",
                        "authority": self.authority,
                        "vault_kind": self.vault_kind,
                    }
                )
            if path == "/sync/profiles" and request.method == "GET":
                return route.fulfill(json={"profiles": self.bindings, "available": self.available})
            if self.sync_fail:
                return route.fulfill(status=409, json={"detail": "Sensitive internal diagnostic"})
            if path.endswith("/retention/list"):
                return route.fulfill(
                    json={
                        "revisions": [
                            {
                                "revision_id": "old-version",
                                "profile_id": "remote-profile",
                                "manifest_sha256": "a" * 64,
                            }
                        ],
                        "decisions": self.retention_decisions,
                        "grace_seconds": 604800,
                    }
                )
            if path.endswith("/retention/schedule"):
                body = json.loads(request.post_data)
                self.assertIs(body["acknowledge_cleanup"], True)
                self.assertEqual(body["revisions"], ["old-version"])
                item = {
                    "decision": {
                        "body": {
                            "decision_id": "decision-one",
                            "not_before": 1800000000,
                            "revisions": [{"revision_id": "old-version"}],
                        },
                        "signature": "signed",
                    },
                    "state": "scheduled",
                }
                self.retention_decisions = [item]
                return route.fulfill(json=item)
            if path.endswith("/retention/cancel"):
                self.assertEqual(json.loads(request.post_data)["decision_id"], "decision-one")
                self.retention_decisions[0]["state"] = "cancelled"
                return route.fulfill(json=self.retention_decisions[0])
            if path.endswith("/preserve-copy"):
                self.assertTrue(json.loads(request.post_data)["acknowledge_fork"])
                return route.fulfill(json={"local_id": "copy0001", "name": "Recovered copy"})
            if path == "/sync/profiles/accept-code":
                self.assertEqual(json.loads(request.post_data)["pack"]["signature"], "owner-signed")
                self.available = [{"profile_id": "remote-profile", "checkpoint": self.checkpoint}]
                return route.fulfill(json={"accepted": True, "added": 1, "sequence": 2})
            if path.endswith("/trash") or path.endswith("/apply-trash"):
                self.assertTrue(json.loads(request.post_data)["acknowledge_deletion"])
                item = self.binding("released")
                item["lifecycle"] = {"state": "trashed", "revision_id": "revision-trash"}
                item["local_trashed"] = True
                self.bindings = [item]
            elif path.endswith("/restore"):
                self.assertTrue(json.loads(request.post_data)["acknowledge_restore"])
                self.bindings = [self.binding("held")]
            elif path.endswith("/takeover"):
                self.assertTrue(json.loads(request.post_data)["acknowledge_lost_device"])
                self.bindings = [self.binding("held")]
            elif path.endswith("/enable"):
                self.bindings = [self.binding("held")]
            elif path.endswith("/handoff"):
                self.bindings = [self.binding("released")]
            elif path.endswith("/acquire") and self.remote_trash:
                local_trashed = bool(self.bindings and self.bindings[0].get("local_trashed"))
                item = self.binding("released")
                item["lifecycle"] = {"state": "trashed", "revision_id": "revision-trash"}
                item["local_trashed"] = local_trashed
                self.bindings = [item]
                return route.fulfill(
                    status=409, json={"detail": "Explicit lifecycle choice required"}
                )
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

    def test_lost_device_recovery_requires_explicit_warning(self):
        self.bindings = [self.binding("held_elsewhere")]
        dialog = self.open_profile_sync()
        expect(dialog).to_contain_text("first revoke it in Settings")
        dialog.get_by_role("button", name="Recover from lost device", exact=True).click()
        confirm = self.page.get_by_role(
            "dialog", name="Recover this profile from a lost device?", exact=True
        )
        expect(confirm).to_contain_text("Its browser may still run offline")
        expect(confirm).to_contain_text("existing website sessions cannot be recalled")
        expect(confirm).to_contain_text("retains a backup")
        self.assertFalse(any(path.endswith("/takeover") for _, path, _ in self.calls))
        confirm.get_by_role("button", name="Recover revoked device profile", exact=True).click()
        expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()
        self.assertTrue(any(path.endswith("/takeover") for _, path, _ in self.calls))

    def test_paired_nonowner_cannot_take_over_lost_device(self):
        self.authority = False
        self.bindings = [self.binding("held_elsewhere")]
        dialog = self.open_profile_sync()
        expect(
            dialog.get_by_role("button", name="Recover from lost device", exact=True)
        ).to_have_count(0)

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

    def test_sync_trash_requires_confirmation_and_retains_data_notice(self):
        self.bindings = [self.binding("held")]
        dialog = self.open_profile_sync()
        dialog.get_by_role("button", name="Move synchronized profile to Trash", exact=True).click()
        confirm = self.page.get_by_role(
            "dialog", name="Move synchronized profile to Trash?", exact=True
        )
        expect(confirm).to_contain_text("Browser data and history are retained")
        self.assertFalse(any(path.endswith("/trash") for _, path, _ in self.calls))
        confirm.get_by_role("button", name="Publish deletion", exact=True).click()
        expect(dialog.get_by_text("Synchronized profile is in Trash", exact=True)).to_be_visible()
        expect(dialog.get_by_role("button", name="Send to another device")).to_have_count(0)
        expect(
            dialog.get_by_role("button", name="Restore synchronized profile", exact=True)
        ).to_be_visible()

    def test_remote_trash_requires_explicit_apply_or_restore(self):
        binding = self.binding("released")
        binding["lifecycle"] = {"state": "trashed", "revision_id": "revision-trash"}
        binding["local_trashed"] = False
        self.bindings = [binding]
        dialog = self.open_profile_sync()
        expect(dialog.get_by_role("button", name="Get latest profile")).to_have_count(0)
        dialog.get_by_role("button", name="Apply deletion on this device", exact=True).click()
        confirm = self.page.get_by_role("dialog", name="Apply deletion on this device?", exact=True)
        expect(confirm).to_contain_text("No local data is permanently erased")
        confirm.get_by_role("button", name="Apply verified deletion", exact=True).click()
        expect(
            dialog.get_by_role("button", name="Apply deletion on this device", exact=True)
        ).to_have_count(0)
        dialog.get_by_role("button", name="Restore synchronized profile", exact=True).click()
        confirm = self.page.get_by_role("dialog", name="Restore synchronized profile?", exact=True)
        expect(confirm).to_contain_text("Local unsent data is backed up")
        confirm.get_by_role("button", name="Restore and publish", exact=True).click()
        expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()

    def test_cancel_sync_deletion_sends_no_request(self):
        self.bindings = [self.binding("held")]
        dialog = self.open_profile_sync()
        dialog.get_by_role("button", name="Move synchronized profile to Trash", exact=True).click()
        self.page.get_by_role(
            "dialog", name="Move synchronized profile to Trash?", exact=True
        ).get_by_role("button", name="Cancel", exact=True).click()
        self.assertFalse(any(path.endswith("/trash") for _, path, _ in self.calls))
        expect(dialog.get_by_role("button", name="Send to another device")).to_be_visible()

    def test_historical_cleanup_selection_confirmation_and_cancel(self):
        self.bindings = [self.binding("held")]
        dialog = self.open_profile_sync()
        dialog.get_by_text("Historical storage cleanup", exact=True).click()
        dialog.get_by_role("button", name="Load verified historical versions").click()
        expect(dialog.get_by_role("button", name="Schedule selected cleanup")).to_be_disabled()
        dialog.get_by_label("old-version", exact=True).check()
        dialog.get_by_role("button", name="Schedule selected cleanup").click()
        confirm = self.page.get_by_role(
            "dialog", name="Schedule permanent historical cleanup?", exact=True
        )
        expect(confirm).to_contain_text("Current profiles and local backups are retained")
        self.assertFalse(any(path.endswith("/retention/schedule") for _, path, _ in self.calls))
        confirm.get_by_role("button", name="Sign cleanup decision", exact=True).click()
        expect(dialog.get_by_text("Cleanup scheduled", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Cancel historical cleanup").click()
        expect(dialog.get_by_text("Cleanup cancelled", exact=True)).to_be_visible()

    def test_regular_device_cannot_see_historical_cleanup_controls(self):
        self.authority = False
        self.bindings = [self.binding("held")]
        dialog = self.open_profile_sync()
        expect(dialog.get_by_text("Historical storage cleanup", exact=True)).to_have_count(0)

    def test_discovered_remote_trash_shows_recovery_actions_after_rejected_acquire(self):
        self.bindings = [self.binding("released")]
        self.remote_trash = True
        dialog = self.open_profile_sync()
        dialog.get_by_role("button", name="Get latest profile").click()
        expect(dialog.get_by_text("Synchronized profile is in Trash", exact=True)).to_be_visible()
        expect(
            dialog.get_by_role("button", name="Apply deletion on this device", exact=True)
        ).to_be_visible()
        expect(
            dialog.get_by_role("button", name="Restore synchronized profile", exact=True)
        ).to_be_visible()
        expect(dialog.get_by_role("button", name="Get latest profile")).to_have_count(0)

    def test_local_trash_can_receive_restoration_from_another_device(self):
        item = self.binding("released")
        item["lifecycle"] = {"state": "trashed", "revision_id": "revision-trash"}
        item["local_trashed"] = True
        self.bindings = [item]
        dialog = self.open_profile_sync()
        expect(dialog.get_by_role("button", name="Send to another device")).to_have_count(0)
        dialog.get_by_role("button", name="Check for restoration", exact=True).click()
        expect(dialog.get_by_text("Ready on this device", exact=True)).to_be_visible()
        self.assertTrue(any(path.endswith("/acquire") for _, path, _ in self.calls))
        self.assertFalse(any(path.endswith("/restore") for _, path, _ in self.calls))
        expect(dialog.get_by_role("button", name="Check for restoration")).to_have_count(0)

    def test_restoration_check_retains_trash_when_no_active_successor_exists(self):
        item = self.binding("released")
        item["lifecycle"] = {"state": "trashed", "revision_id": "revision-trash"}
        item["local_trashed"] = True
        self.bindings = [item]
        self.remote_trash = True
        dialog = self.open_profile_sync()
        dialog.get_by_role("button", name="Check for restoration", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("Your existing profile is preserved")
        expect(dialog.get_by_text("Synchronized profile is in Trash", exact=True)).to_be_visible()
        expect(dialog.get_by_role("button", name="Send to another device")).to_have_count(0)
        expect(dialog.get_by_role("button", name="Apply deletion on this device")).to_have_count(0)
        self.assertTrue(self.bindings[0]["local_trashed"])
        self.assertFalse(any(path.endswith("/restore") for _, path, _ in self.calls))

    def test_pending_lifecycle_offers_resume_without_restoration_check(self):
        item = self.binding("released")
        item["lifecycle"] = {
            "state": "trashed",
            "revision_id": "revision-trash",
            "pending": True,
            "operation": "restore",
        }
        item["local_trashed"] = True
        self.bindings = [item]
        dialog = self.open_profile_sync()
        expect(dialog.get_by_role("button", name="Check for restoration")).to_have_count(0)
        expect(dialog.get_by_role("button", name="Restore synchronized profile")).to_have_count(0)
        expect(dialog.get_by_role("button", name="Resume pending transfer")).to_be_visible()


if __name__ == "__main__":
    suite = unittest.TestSuite(
        SyncProfileUI(name) for name in SyncProfileUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
