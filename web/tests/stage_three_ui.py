"""Access UI offline contracts. Build and run only in GitHub Actions."""

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from stage_two_ui import StageTwoUI


class StageThreeUI(StageTwoUI):
    def setUp(self):
        super().setUp()
        self.role = "administrator"
        self.revoke_launch = False
        self.users = [{"username": "alice", "role": "viewer", "created_at": "2026-01-01T00:00:00Z"}]
        self.grants = []
        self.events = [
            {
                "id": 1,
                "actor_kind": "account_session",
                "actor_username": "alice",
                "action": "profile.export",
                "object_id": "profile-1",
                "result": "denied",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.split("/api/v1", 1)[1]
        query = parse_qs(urlsplit(request.url).query)
        if path == "/auth/session":
            return route.fulfill(
                json={
                    "authenticated": True,
                    "user_auth_enabled": True,
                    "username": "admin" if self.role == "administrator" else "alice",
                    "role": self.role,
                    "actor_kind": "account_session",
                    "can_manage_access": self.role == "administrator",
                }
            )
        if path.endswith("/launch") and self.revoke_launch:
            self.profiles = []
            return route.fulfill(
                status=403, json={"error": {"code": "forbidden", "message": "Access denied"}}
            )
        if path.startswith("/access/"):
            self.calls.append((request.method, path, request.post_data))
            data = {}
            body = json.loads(request.post_data or "{}")
            if path == "/access/users":
                if request.method == "POST":
                    self.users.append(
                        {
                            "username": body["username"],
                            "role": body["role"],
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                    )
                data = {"users": self.users, "total": len(self.users)}
            elif path == "/access/users/alice/profiles":
                data = {"grants": self.grants, "total": len(self.grants)}
            elif path.startswith("/access/users/alice/profiles/"):
                profile_id = path.rsplit("/", 1)[1]
                self.grants = [g for g in self.grants if g["profile_id"] != profile_id]
                if request.method == "PUT":
                    self.grants.append({"profile_id": profile_id, "can_export": body["can_export"]})
            elif path.startswith("/access/audit"):
                events = self.events
                for key, field in [
                    ("actor", "actor_username"),
                    ("action", "action"),
                    ("result", "result"),
                ]:
                    if query.get(key):
                        events = [event for event in events if event[field] == query[key][0]]
                data = {"events": events, "total": len(events)}
            return route.fulfill(json=data)
        super().route(route)

    def test_grant_and_export_are_separate(self):
        self.page.goto(self.origin + "/access/")
        self.page.get_by_role("button", name="Manage grants").click()
        assign = self.page.get_by_role("checkbox", name="Assign Account 001", exact=True)
        export = self.page.get_by_role("checkbox", name="Allow export for Account 001", exact=True)
        expect(export).to_be_disabled()
        # Grant checkboxes show only the committed server state.
        assign.click()
        expect(assign).to_be_checked()
        expect(export).to_be_enabled()
        expect(export).not_to_be_checked()
        export.click()
        expect(export).to_be_checked()
        self.assertEqual(self.grants, [{"profile_id": "profile-1", "can_export": True}])

    def test_create_user_with_deliberate_role(self):
        self.page.goto(self.origin + "/access/")
        self.page.get_by_label("Username", exact=True).fill("bob")
        self.page.get_by_label("Password", exact=True).fill("temporary-test-password")
        self.page.get_by_label("Role", exact=True).select_option("operator")
        self.page.get_by_role("button", name="Add user").click()
        expect(self.page.get_by_role("cell", name="bob", exact=True)).to_be_visible()
        self.assertEqual(self.users[-1]["role"], "operator")
        expect(self.page.get_by_label("Password", exact=True)).to_have_value("")

    def test_activity_result_filter_includes_partial_stream_and_schedule_outcomes(self):
        self.page.goto(self.origin + "/access/")
        result = self.page.get_by_label("Result", exact=True)
        for outcome in ["partial", "interrupted", "skipped"]:
            result.select_option(outcome)
            self.page.get_by_role("button", name="Apply filters").click()
            expect(self.page.get_by_text("No activity found")).to_be_visible()
        result.select_option("denied")
        self.page.get_by_role("button", name="Apply filters").click()
        expect(self.page.get_by_role("cell", name="profile.export", exact=True)).to_be_visible()

    def test_first_user_is_administrator_and_password_has_minimum_length(self):
        self.users = []
        self.page.goto(self.origin + "/access/")
        role = self.page.get_by_label("Role", exact=True)
        expect(role).to_have_value("administrator")
        expect(role).to_be_disabled()
        expect(self.page.get_by_label("Password", exact=True)).to_have_attribute("minlength", "8")
        self.page.get_by_label("Username", exact=True).fill("first-admin")
        self.page.get_by_label("Password", exact=True).fill("long-enough-password")
        self.page.get_by_role("button", name="Add user").click()
        expect(self.page.get_by_role("cell", name="first-admin", exact=True)).to_be_visible()
        self.assertEqual(self.users[-1]["role"], "administrator")

    def test_password_reset_rejects_short_draft(self):
        self.page.goto(self.origin + "/access/")
        self.page.get_by_role("button", name="Manage grants").click()
        password = self.page.get_by_label("New password", exact=True)
        expect(password).to_have_attribute("minlength", "8")
        password.fill("short")
        expect(self.page.get_by_role("button", name="Reset password")).to_be_disabled()
        password.fill("long-enough-password")
        expect(self.page.get_by_role("button", name="Reset password")).to_be_enabled()

    def test_activity_filter_and_safe_download(self):
        self.page.goto(self.origin + "/access/")
        expect(self.page.get_by_role("cell", name="profile.export", exact=True)).to_be_visible()
        self.page.get_by_label("Actor", exact=True).fill("nobody")
        self.page.get_by_role("button", name="Apply filters").click()
        expect(self.page.get_by_text("No activity found")).to_be_visible()
        with self.page.expect_download() as download:
            self.page.get_by_role("button", name="Export activity").click()
        self.assertEqual(download.value.suggested_filename, "activity.json")
        exported = json.loads(Path(download.value.path()).read_text())
        self.assertEqual(exported["events"], [])

    def test_nonadmin_direct_navigation_does_not_fetch_libraries(self):
        for role in ["operator", "viewer"]:
            self.role = role
            self.calls.clear()
            self.page.goto(self.origin + "/settings/")
            expect(self.page.get_by_text("Access denied", exact=True)).to_be_visible()
            expect(self.page.get_by_role("link", name="Proxy library")).to_have_count(0)
            self.assertFalse(
                any(
                    path.startswith(("/system/", "/access/", "/proxies"))
                    for _, path, _ in self.calls
                )
            )

    def test_operator_edit_preserves_redacted_proxy(self):
        self.role = "operator"
        self.profiles[0]["permissions"] = {
            "view": True,
            "edit": True,
            "launch": True,
            "export": False,
        }
        self.profiles[0]["proxy_config"] = {"type": "http", "server": "proxy.example:3128"}
        self.page.goto(self.origin + "/")
        self.page.get_by_role("button", name="Actions for Account 001").click()
        self.page.get_by_role("menuitem", name="Edit", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog.get_by_label("Server", exact=True)).to_have_count(0)
        expect(dialog.get_by_label("Password", exact=True)).to_have_count(0)
        dialog.get_by_label("Name", exact=True).fill("Renamed operator profile")
        dialog.get_by_role("button", name="Save changes").click()
        expect(dialog).to_have_count(0)
        payload = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "PUT" and path == "/profiles/profile-1"
        )
        self.assertNotIn("proxy_config", payload)
        self.assertNotIn("group", payload)
        self.assertFalse(
            any(
                path.startswith(("/system/", "/saved-filters", "/templates", "/extensions"))
                for _, path, _ in self.calls
            )
        )

    def test_viewer_explicit_export_does_not_allow_import_or_launch(self):
        self.role = "viewer"
        self.profiles[0]["permissions"] = {
            "view": True,
            "edit": False,
            "launch": False,
            "export": True,
        }
        self.page.goto(self.origin + "/")
        expect(self.page.get_by_role("button", name="Run", exact=True).first).to_be_disabled()
        self.page.get_by_role("button", name="Actions for Account 001").click()
        expect(self.page.get_by_role("menuitem", name="Edit", exact=True)).to_have_count(0)
        self.page.get_by_role("menuitem", name="Cookies", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog.get_by_role("button", name="Export cookies")).to_be_visible()
        expect(dialog.get_by_role("button", name="Import cookies")).to_have_count(0)
        expect(dialog.get_by_label("Paste cookies or choose a file")).to_have_count(0)

    def test_operator_extensions_only_fetch_assigned_snapshots(self):
        self.role = "operator"
        self.profiles[0]["permissions"] = {
            "view": True,
            "edit": True,
            "launch": True,
            "export": False,
        }
        self.page.goto(self.origin + "/")
        self.page.get_by_role("button", name="Actions for Account 001").click()
        self.page.get_by_role("menuitem", name="Extensions", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog.get_by_text("Retained Snapshot")).to_be_visible()
        expect(dialog.get_by_role("button", name="Save extensions")).to_have_count(0)
        self.assertFalse(any(path == "/extensions" for _, path, _ in self.calls))

    def test_revoked_grant_clears_profile_state(self):
        self.role = "operator"
        self.profiles[0]["permissions"] = {
            "view": True,
            "edit": True,
            "launch": True,
            "export": False,
        }
        self.profiles = self.profiles[:1]
        self.page.goto(self.origin + "/")
        expect(self.page.get_by_text("Account 001", exact=True)).to_be_visible()
        self.revoke_launch = True
        self.page.get_by_role("button", name="Run", exact=True).click()
        expect(
            self.page.get_by_text("Ask an administrator to grant you access to profiles.")
        ).to_be_visible()
        expect(self.page.get_by_text("Account 001", exact=True)).to_have_count(0)

    def test_chinese_access_ui(self):
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.page.goto(self.origin + "/access/")
        expect(self.page.get_by_role("heading", name="访问权限与活动")).to_be_visible()
        expect(self.page.get_by_role("button", name="管理授权")).to_be_visible()


if __name__ == "__main__":
    suite = unittest.TestSuite(
        StageThreeUI(name) for name in StageThreeUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
