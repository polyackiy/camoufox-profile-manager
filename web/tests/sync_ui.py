"""Synchronization onboarding contracts; static export and browser run only in CI."""

import base64
import hashlib
import json
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_three_ui import StageThreeUI


def code(kind, value):
    return (
        "CPM-SYNC-1."
        + kind
        + "."
        + base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()
    )


class SyncUI(StageThreeUI):
    def setUp(self):
        super().setUp()
        self.sync_status = {"phase": "not_enrolled"}
        self.sync_fail = False
        self.draft = None
        self.pending_membership = None
        self.package = b'{"version":1,"ciphertext":"encrypted-owner-key"}'
        self.secret = "S" * 43
        self.owner_root = {
            "owner_id": "11111111-1111-4111-8111-111111111111",
            "relay_url": "https://relay.example",
        }
        self.request = {
            "invitation_id": "22222222-2222-4222-8222-222222222222",
            "root_digest": "a" * 64,
        }
        self.fingerprint = hashlib.sha256(
            b"camoufox-pm/sync/experimental-v1/identity/PairingRequest\0"
            + json.dumps(self.request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.approval = {"request_digest": self.fingerprint, "signature": "public-signature"}

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.split("/api/v1", 1)[1]
        if path.startswith("/sync/"):
            self.calls.append((request.method, path, request.post_data))
            self.assertNotIn("x-api-key", request.headers)
            if self.sync_fail and request.method == "POST":
                return route.fulfill(status=409, json={"detail": "Private backend diagnostic"})
            if path == "/sync/status":
                return route.fulfill(json=self.sync_status)
            if path == "/sync/pairing/draft":
                return route.fulfill(body=json.dumps(self.draft), content_type="application/json")
            if path == "/sync/membership/pending":
                return route.fulfill(
                    body=json.dumps(self.pending_membership), content_type="application/json"
                )
            if path == "/sync/pairing/abandon":
                self.draft = None
                return route.fulfill(json={"success": True})
            if path == "/sync/membership/resume":
                self.pending_membership = None
                return route.fulfill(json={"phase": "published", "membership_epoch": 2})
            if path == "/sync/enrollment":
                body = json.loads(request.post_data)
                self.sync_status = {
                    "phase": "recovery_required",
                    "authority": True,
                    "vault_kind": body["vault_kind"],
                    "device_id": "this-device",
                    "relay_url": body["relay_url"],
                }
                return route.fulfill(status=201, json=self.sync_status)
            if path == "/sync/recovery-package":
                return route.fulfill(
                    json={
                        "package": base64.urlsafe_b64encode(self.package).decode().rstrip("="),
                        "secret": self.secret,
                    }
                )
            if path == "/sync/confirm-recovery":
                body = json.loads(request.post_data)
                self.assertEqual(body["secret"], self.secret)
                self.assertEqual(
                    base64.urlsafe_b64decode(body["package"] + "=" * (-len(body["package"]) % 4)),
                    self.package,
                )
                self.sync_status["phase"] = "ready"
                return route.fulfill(json=self.sync_status)
            if path == "/sync/pairing/owner-code":
                return route.fulfill(json=self.owner_root)
            if path == "/sync/pairing/prepare":
                return route.fulfill(
                    json={
                        "request": self.request,
                        "fingerprint": self.fingerprint,
                        "phase": "prepared",
                    }
                )
            if path == "/sync/pairing/approve":
                self.assertEqual(
                    json.loads(request.post_data)["confirmed_fingerprint"], self.fingerprint
                )
                return route.fulfill(json=self.approval)
            if path == "/sync/pairing/accept":
                self.sync_status = {
                    "phase": "freshness_unknown",
                    "authority": False,
                    "vault_kind": "native",
                }
                return route.fulfill(json=self.sync_status)
            if path == "/sync/profiles/share":
                return route.fulfill(
                    json={
                        "body": {"sequence": 2, "target_device_id": "other-device"},
                        "signature": "signed",
                    }
                )
            if path == "/sync/devices":
                return route.fulfill(
                    json={"devices": [{"device_id": "this-device"}, {"device_id": "other-device"}]}
                )
            if path in {"/sync/devices/revoke", "/sync/relay/connect", "/sync/abandon"}:
                return route.fulfill(json={"success": True})
            return route.fulfill(status=404, json={"detail": "Unknown fixture"})
        super().route(route)

    def test_recovery_download_contains_no_secret_and_requires_saved_file(self):
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_label("Device name", exact=True).fill("My laptop")
        self.page.get_by_label("Relay address", exact=True).fill("https://relay.example")
        create = self.page.get_by_role("button", name="Create owner and recovery setup")
        expect(create).to_be_disabled()
        self.page.get_by_label("I understand that trusted devices receive", exact=False).check()
        create.click()
        self.page.get_by_role("button", name="Export encrypted recovery package").click()
        expect(self.page.get_by_label("Separate recovery secret", exact=True)).to_have_attribute(
            "type", "password"
        )
        with self.page.expect_download() as pending:
            self.page.get_by_role("button", name="Download recovery package").click()
        download = pending.value
        downloaded = download.path().read_bytes()
        self.assertEqual(downloaded, self.package)
        self.assertNotIn(self.secret.encode(), downloaded)
        verify = self.page.get_by_role("button", name="Verify saved recovery and continue")
        expect(verify).to_be_disabled()
        self.page.get_by_label("Select the saved recovery package").set_input_files(
            {
                "name": "saved.cpmrecovery",
                "mimeType": "application/octet-stream",
                "buffer": downloaded,
            }
        )
        self.page.get_by_label("Enter the separately saved recovery secret").fill(self.secret)
        verify.click()
        expect(self.page.get_by_text("Owner recovery verified.", exact=False)).to_be_visible()
        expect(self.page.get_by_label("Separate recovery secret", exact=True)).to_have_count(0)
        state = self.page.evaluate(
            "JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})"
        )
        self.assertNotIn(self.secret, state)

    def test_explicit_file_vault_and_failed_request_clears_password(self):
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_label("Device name", exact=True).fill("Headless laptop")
        self.page.get_by_label("Protect device keys").select_option("encrypted_file")
        self.page.get_by_label("Relay address", exact=True).fill("https://relay.example")
        self.page.get_by_label("I understand that trusted devices receive", exact=False).check()
        self.page.get_by_label("Unlock password", exact=True).fill("short")
        create = self.page.get_by_role("button", name="Create owner and recovery setup")
        expect(create).to_be_disabled()
        self.page.get_by_label("Unlock password", exact=True).fill("long-private-password")
        self.sync_fail = True
        create.click()
        expect(
            self.page.get_by_role("alert").filter(has_text="The operation could not finish")
        ).to_be_visible()
        expect(self.page.get_by_label("Unlock password", exact=True)).to_have_value("")
        self.assertNotIn("Private backend diagnostic", self.page.locator("body").inner_text())

    def test_operator_cannot_start_owner_setup(self):
        self.role = "operator"
        self.page.goto(self.origin + "/settings/")
        # The application shell blocks the settings route before mounting any
        # administrator component or requesting synchronization state.
        expect(self.page.get_by_text("Access denied", exact=True)).to_be_visible()
        expect(
            self.page.get_by_role("button", name="Create owner and recovery setup")
        ).to_have_count(0)
        self.assertFalse(any(path.startswith("/sync/") for _, path, _ in self.calls))

    def test_both_device_pairing_requires_full_fingerprint_confirmation(self):
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_role("button", name="Connect another device", exact=True).click()
        self.page.get_by_label("Device name", exact=True).fill("Second laptop")
        self.page.get_by_label("Owner connection code", exact=True).fill(
            code("owner", self.owner_root)
        )
        self.page.get_by_label("I understand that trusted devices receive", exact=False).check()
        self.page.get_by_role("button", name="Prepare device connection").click()
        expect(self.page.get_by_label("Full pairing fingerprint", exact=True)).to_have_value(
            self.fingerprint
        )
        self.page.get_by_label("Approved connection code", exact=True).fill(
            code("approval", self.approval)
        )
        accept = self.page.get_by_role("button", name="Accept trusted device connection")
        expect(accept).to_be_disabled()
        self.page.get_by_label("I compared the full fingerprint with the owner device.").check()
        accept.click()
        expect(
            self.page.get_by_text("Device connected. Profiles remain blocked", exact=False)
        ).to_be_visible()

    def test_owner_review_uses_exact_request_fingerprint(self):
        self.sync_status = {
            "phase": "ready",
            "authority": True,
            "vault_kind": "native",
            "device_id": "this-device",
        }
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_text("Add a trusted device", exact=True).click()
        self.page.get_by_label("Request from the other device").fill(code("request", self.request))
        self.page.get_by_role("button", name="Review device request").click()
        expect(self.page.get_by_label("Full pairing fingerprint", exact=True)).to_have_value(
            self.fingerprint
        )
        approve = self.page.get_by_role("button", name="Approve this exact device")
        expect(approve).to_be_disabled()
        self.page.get_by_label("I compared every fingerprint character", exact=False).check()
        approve.click()
        expect(self.page.get_by_label("Approved connection code", exact=True)).to_have_value(
            code("approval", self.approval)
        )

    def test_revoke_requires_explicit_warning_confirmation(self):
        self.sync_status = {
            "phase": "ready",
            "authority": True,
            "vault_kind": "native",
            "device_id": "this-device",
        }
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_text("Trusted devices", exact=True).click()
        self.page.get_by_role("button", name="Refresh trusted devices").click()
        self.page.get_by_role("button", name="Revoke device", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Revoke trusted device?")
        expect(dialog).to_contain_text(
            "Downloaded profiles and website sessions cannot be recalled"
        )
        dialog.get_by_role("button", name="Revoke device", exact=True).click()
        expect(self.page.get_by_text("Revocation published.", exact=False)).to_be_visible()

    def test_reload_restores_pairing_request_and_explicit_abandon(self):
        self.draft = {
            "root": self.owner_root,
            "request": self.request,
            "fingerprint": self.fingerprint,
            "phase": "pending",
            "device_label": "Second laptop",
            "vault_kind": "native",
        }
        self.page.goto(self.origin + "/settings/")
        expect(self.page.get_by_label("Device request code", exact=True)).to_have_value(
            code("request", self.request)
        )
        expect(
            self.page.get_by_role("button", name="Create owner and recovery setup")
        ).to_have_count(0)
        self.page.get_by_role("button", name="Abandon saved device connection", exact=True).click()
        self.page.get_by_role("dialog").get_by_role(
            "button", name="Abandon connection", exact=True
        ).click()
        expect(
            self.page.get_by_role("button", name="Create owner and recovery setup")
        ).to_be_visible()

    def test_interrupted_membership_update_has_resume_action(self):
        self.sync_status = {"phase": "ready", "authority": True, "vault_kind": "native"}
        self.pending_membership = {"kind": "revoke", "phase": "pending"}
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_role("button", name="Resume membership update").click()
        expect(self.page.get_by_role("button", name="Resume membership update")).to_have_count(0)

    def test_owner_shares_updated_list_for_selected_existing_device(self):
        self.sync_status = {
            "phase": "ready",
            "authority": True,
            "vault_kind": "native",
            "device_id": "this-device",
        }
        self.page.goto(self.origin + "/settings/")
        self.page.get_by_text("Trusted devices", exact=True).click()
        self.page.get_by_role("button", name="Refresh trusted devices").click()
        self.page.get_by_role("button", name="Share updated profile list").click()
        expect(self.page.get_by_label("Updated profile list code", exact=True)).to_have_value(
            code(
                "profiles",
                {
                    "body": {"sequence": 2, "target_device_id": "other-device"},
                    "signature": "signed",
                },
            )
        )
        sent = [
            json.loads(body) for method, path, body in self.calls if path == "/sync/profiles/share"
        ]
        self.assertEqual(sent[0]["target_device_id"], "other-device")

    def test_chinese_sync_settings(self):
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.page.goto(self.origin + "/settings/")
        expect(self.page.get_by_role("heading", name="\u8bbe\u5907\u540c\u6b65")).to_be_visible()


if __name__ == "__main__":
    suite = unittest.TestSuite(SyncUI(name) for name in SyncUI.__dict__ if name.startswith("test_"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
