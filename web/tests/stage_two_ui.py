"""Offline combined stage-two UI contracts, run after the static export.

  .venv/bin/python web/tests/stage_two_ui.py
Uses the same local server and isolated Chromium fixture as stage_one_ui.
"""

import json
import os
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from stage_one_ui import DesktopUI


def profile(number, **values):
    return {
        "id": f"profile-{number}",
        "name": f"Account {number:03}",
        "group": None,
        "tags": [],
        "status": "active",
        "browser_settings": {"os": "windows", "screen": "1920x1080"},
        "proxy_config": None,
        "notes": None,
        "created_at": "2026-01-01T00:00:00Z",
        "row_version": 3,
        **values,
    }


class StageTwoUI(DesktopUI):
    def setUp(self):
        super().setUp()
        self.profiles = [
            profile(1, tags=["work", "client-a"], group="team"),
            profile(2, tags=["personal"]),
        ]
        self.views = []
        self.view_stale = False
        self.tag_skips = []
        self.cookie_stale = False
        self.extension = {
            "id": "extension-one",
            "name": "Safe Add-on",
            "version": "1.0",
            "addon_id": "safe@example",
            "manifest_version": 2,
            "permissions": ["storage"],
            "sha256": "a" * 64,
            "unpacked_bytes": 512,
            "row_version": 0,
            "created_at": "2026-01-01T00:00:00Z",
        }
        self.profile_extension_state = {
            "row_version": 8,
            "extensions": [
                {
                    **self.extension,
                    "id": "snapshot-orphan",
                    "name": "Retained Snapshot",
                    "sha256": "b" * 64,
                }
            ],
        }
        self.templates = []
        self.multipart_content_type = None
        self.browser_state = {
            "installed": True,
            "version": "beta.36",
            "state": "ready",
            "progress": 100,
            "message": "Browser ready",
            "error": None,
        }

    def route(self, route):
        request = route.request
        full_path = request.url.split("/api/v1", 1)[1]
        parsed = urlsplit(full_path)
        path = parsed.path
        data, envelope = None, True
        if path == "/profiles" and request.method == "GET":
            query = parse_qs(parsed.query)
            page, per_page = int(query.get("page", [1])[0]), int(query.get("per_page", [100])[0])
            start = (page - 1) * per_page
            data, envelope = (
                {
                    "profiles": self.profiles[start : start + per_page],
                    "total": len(self.profiles),
                    "page": page,
                    "per_page": per_page,
                    "has_next": start + per_page < len(self.profiles),
                    "has_prev": page > 1,
                },
                False,
            )
        elif path == "/groups":
            data, envelope = (
                {"groups": [{"id": "team", "name": "Team", "profile_count": 1}], "total": 1},
                False,
            )
        elif path == "/saved-filters" and request.method == "GET":
            data = {"filters": self.views}
        elif path == "/saved-filters" and request.method == "POST":
            body = request.post_data_json
            data = {
                "id": "saved-view",
                **body,
                "row_version": 0,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
            self.views.append(data)
        elif path.startswith("/saved-filters/") and request.method in {"PATCH", "DELETE"}:
            if self.view_stale:
                self.view_stale = False
                self.views[0] = {**self.views[0], "name": "Changed elsewhere", "row_version": 1}
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {"error": {"code": "stale_write", "message": "Saved view changed"}}
                    ),
                )
                self.calls.append((request.method, full_path, request.post_data))
                return
            if request.method == "DELETE":
                self.views = []
                data = None
            else:
                body = request.post_data_json
                self.views[0] = {
                    **self.views[0],
                    **body,
                    "row_version": self.views[0]["row_version"] + 1,
                }
                data = self.views[0]
        elif path == "/profiles/tags":
            body = request.post_data_json
            skipped_ids = {item["id"] for item in self.tag_skips}
            updated = []
            for target in body["profiles"]:
                if target["id"] in skipped_ids:
                    continue
                for index, item in enumerate(self.profiles):
                    if item["id"] != target["id"]:
                        continue
                    tags = [tag for tag in item["tags"] if tag not in body["remove"]]
                    tags += [tag.lower() for tag in body["add"] if tag.lower() not in tags]
                    self.profiles[index] = {
                        **item,
                        "tags": tags,
                        "row_version": item["row_version"] + 1,
                    }
                    updated.append(self.profiles[index])
            data = {"updated": updated, "skipped": self.tag_skips}
        elif path == "/profiles" and request.method == "POST":
            body = request.post_data_json
            data, envelope = profile(99, **body), False
            self.profiles.append(data)
        elif (
            path.startswith("/profiles/")
            and request.method == "PUT"
            and not path.endswith("/extensions")
        ):
            body = request.post_data_json
            index = next(
                index
                for index, item in enumerate(self.profiles)
                if item["id"] == path.split("/")[2]
            )
            self.profiles[index] = {
                **self.profiles[index],
                **body,
                "row_version": self.profiles[index]["row_version"] + 1,
            }
            data, envelope = self.profiles[index], False
        elif path == "/extensions" and request.method == "GET":
            data = {"extensions": [self.extension]}
        elif path == "/extensions" and request.method == "POST":
            self.multipart_content_type = request.headers.get("content-type")
            data = self.extension
        elif path.endswith("/extensions") and path.startswith("/profiles/"):
            if request.method == "PUT":
                body = request.post_data_json
                self.profile_extension_state = {
                    "row_version": 9,
                    "extensions": [{**self.extension, "id": "new-snapshot"}],
                }
            data = self.profile_extension_state
        elif path == "/templates" and request.method == "GET":
            data = {"templates": self.templates}
        elif path == "/templates/from-profile":
            body = request.post_data_json
            data = {
                "id": "template-one",
                "name": body["name"],
                "browser_settings": self.profiles[0]["browser_settings"],
                "tags": self.profiles[0]["tags"],
                "notes": None,
                "extensions": [],
                "row_version": 0,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
            self.templates.append(data)
        elif "/cookies/" in path:
            if path.endswith("/preview"):
                data = {
                    "total": 2,
                    "importable": 1,
                    "session_count": 1,
                    "skipped": [{"row": 2, "reason": "Cookie expired"}],
                }
            elif path.endswith("/import"):
                if self.cookie_stale:
                    route.fulfill(
                        status=409,
                        content_type="application/json",
                        body=json.dumps(
                            {"error": {"code": "stale_write", "message": "Profile changed"}}
                        ),
                    )
                    self.calls.append((request.method, full_path, request.post_data))
                    return
                data = {
                    "imported": 1,
                    "session_count": 1,
                    "skipped": [],
                    "row_version": 4,
                    "backup_id": "cookie-backup",
                }
            else:
                raise AssertionError(f"Unexpected cookie request {path}")
        else:
            return super().route(route)
        self.calls.append((request.method, full_path, request.post_data))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"success": True, "message": "", "data": data} if envelope else data),
        )

    def capture(self, name):
        if os.environ.get("CPM_UI_SCREENSHOTS") == "1":
            target = Path(__file__).resolve().parents[2] / "docs" / "assets"
            target.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(
                path=str(target / f"stage-two-{name}.png"), full_page=True, animations="disabled"
            )

    def open_profiles(self, query=""):
        self.page.goto(f"{self.origin}/{query}")
        expect(
            self.page.get_by_role("table").get_by_text(self.profiles[0]["name"], exact=True)
        ).to_be_visible()

    def open_actions(self, name):
        self.page.get_by_role("button", name=f"Actions for {name}", exact=True).click()

    def test_stage_two_tag_chip_and_bulk_exact_versions_partial_result(self):
        self.tag_skips = [
            {"id": "profile-2", "reason": "Profile changed; reload before editing tags"}
        ]
        self.open_profiles()
        self.page.get_by_role("checkbox", name="Select Account 001", exact=True).check()
        self.page.get_by_role("checkbox", name="Select Account 002", exact=True).check()
        self.page.get_by_role("button", name="Edit tags", exact=True).click()
        self.page.get_by_label("Add tags", exact=True).fill("campaign")
        self.page.get_by_label("Remove tags", exact=True).fill("work")
        self.page.get_by_role("button", name="Apply tags", exact=True).click()
        expect(self.page.get_by_role("dialog")).to_contain_text("Updated tags for 1 profiles.")
        expect(self.page.get_by_role("dialog")).to_contain_text(
            "Account 002: Profile changed; reload before editing tags"
        )
        body = next(json.loads(body) for _, path, body in self.calls if path == "/profiles/tags")
        self.assertEqual(
            body["profiles"],
            [{"id": "profile-1", "row_version": 3}, {"id": "profile-2", "row_version": 3}],
        )
        self.page.get_by_role("button", name="Done", exact=True).click()
        self.page.get_by_role("button", name="Filter by tag campaign", exact=True).click()
        expect(
            self.page.get_by_role("table").get_by_text("Account 001", exact=True)
        ).to_be_visible()
        expect(
            self.page.get_by_role("table").get_by_text("Account 002", exact=True)
        ).not_to_be_visible()
        expect(self.page).to_have_url(re.compile(r"[?&]tags=campaign(?:&|$)"))
        self.capture("profile-tags")

    def test_stage_two_url_filters_sort_and_pagination_use_the_whole_collection(self):
        self.profiles = [profile(index) for index in range(1, 111)]
        self.profiles[104] = {
            **self.profiles[104],
            "name": "Late target",
            "tags": ["work", "client-a"],
            "group": "team",
        }
        self.profiles[105] = {
            **self.profiles[105],
            "name": "Other target",
            "tags": ["work"],
            "group": "team",
        }
        self.page.goto(
            f"{self.origin}/?search=target&tags=work&tags=client-a&group=team&status=active&sort=id&direction=desc"
        )
        expect(
            self.page.get_by_role("table").get_by_text("Late target", exact=True)
        ).to_be_visible()
        expect(
            self.page.get_by_role("table").get_by_text("Other target", exact=True)
        ).not_to_be_visible()
        self.assertTrue(
            any("page=2" in path for _, path, _ in self.calls if path.startswith("/profiles?"))
        )
        self.page.reload()
        expect(
            self.page.get_by_role("table").get_by_text("Late target", exact=True)
        ).to_be_visible()
        expect(self.page.get_by_label("Filter by status", exact=True)).to_have_value("active")
        self.page.get_by_role("button", name="Clear filters", exact=True).click()
        self.page.get_by_role("button", name="Next page", exact=True).click()
        expect(self.page).to_have_url(re.compile(r"[?&]page=2(?:&|$)"))
        self.page.get_by_label("Tags", exact=True).select_option("client-a")
        expect(
            self.page.get_by_role("table").get_by_text("Late target", exact=True)
        ).to_be_visible()
        expect(self.page).not_to_have_url(re.compile(r"[?&]page=2(?:&|$)"))

    def test_stage_two_saved_view_persists_applies_and_renames_without_losing_filters(self):
        self.open_profiles()
        self.page.get_by_label("Tags", exact=True).select_option("work")
        self.page.get_by_label("Group", exact=True).select_option("team")
        self.page.get_by_role("button", name="Save view", exact=True).click()
        self.page.get_by_label("View name", exact=True).fill("Work view")
        self.capture("saved-view")
        self.page.get_by_role("dialog").get_by_role("button", name="Save", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        self.assertEqual(self.views[0]["config"]["tags"], ["work"])
        self.assertEqual(self.views[0]["config"]["group"], "team")
        self.page.get_by_role("button", name="Clear filters", exact=True).click()
        self.page.reload()
        self.page.get_by_label("Saved views", exact=True).select_option("saved-view")
        expect(
            self.page.get_by_role("table").get_by_text("Account 002", exact=True)
        ).not_to_be_visible()
        self.page.get_by_role("button", name="Rename saved view", exact=True).click()
        self.page.get_by_label("View name", exact=True).fill("Renamed view")
        self.page.get_by_role("dialog").get_by_role("button", name="Save", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        self.assertEqual(self.views[0]["name"], "Renamed view")
        body = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "PATCH" and path.startswith("/saved-filters/")
        )
        self.assertEqual(body, {"name": "Renamed view", "row_version": 0})
        self.page.get_by_role("button", name="Delete saved view", exact=True).click()
        self.page.get_by_role("dialog").get_by_role("button", name="Delete", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        self.assertEqual(self.views, [])

    def test_stage_two_saved_view_stale_edit_has_no_automatic_overwrite(self):
        self.views = [
            {
                "id": "saved-view",
                "name": "Work view",
                "config": {
                    "search": "",
                    "status": "all",
                    "group": None,
                    "tags": ["work"],
                    "sort_key": "name",
                    "sort_direction": "asc",
                },
                "row_version": 0,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        self.open_profiles()
        self.page.get_by_label("Saved views", exact=True).select_option("saved-view")
        self.page.get_by_role("button", name="Rename saved view", exact=True).click()
        self.page.get_by_label("View name", exact=True).fill("My draft")
        self.view_stale = True
        self.page.get_by_role("dialog").get_by_role("button", name="Save", exact=True).click()
        expect(self.page.get_by_role("dialog").get_by_role("alert")).to_contain_text(
            "changed elsewhere"
        )
        expect(self.page.get_by_label("View name", exact=True)).to_have_value("My draft")
        expect(
            self.page.get_by_role("dialog").get_by_role("button", name="Save", exact=True)
        ).to_be_disabled()
        self.assertEqual(sum(method == "PATCH" for method, _, _ in self.calls), 1)
        self.page.get_by_role("button", name="Reload saved views", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        expect(self.page.get_by_label("Saved views", exact=True)).to_contain_text(
            "Changed elsewhere"
        )

    def test_stage_two_profile_form_submits_tags_as_metadata(self):
        self.open_profiles()
        self.open_actions("Account 001")
        self.page.get_by_role("menuitem", name="Edit", exact=True).click()
        self.page.get_by_role("dialog").get_by_label("Tags", exact=True).fill(
            "work, client-a, campaign"
        )
        self.page.get_by_role("button", name="Save changes", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        body = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "PUT" and path == "/profiles/profile-1"
        )
        self.assertEqual(body["tags"], ["work", "client-a", "campaign"])
        self.assertEqual(body["row_version"], 3)

    def test_stage_two_row_menu_template_uses_current_version_without_sending_secrets(self):
        self.open_profiles()
        self.open_actions("Account 001")
        self.page.get_by_role("menuitem", name="Save as template", exact=True).click()
        self.page.get_by_label("Template name", exact=True).fill("Reusable work")
        self.capture("template")
        self.page.get_by_role("button", name="Save template", exact=True).click()
        expect(self.page.get_by_role("dialog")).not_to_be_visible()
        body = next(
            json.loads(body) for _, path, body in self.calls if path == "/templates/from-profile"
        )
        self.assertEqual(
            body, {"name": "Reusable work", "profile_id": "profile-1", "profile_row_version": 3}
        )

    def test_stage_two_row_menu_extension_assignment_retains_removed_library_snapshot(self):
        self.open_profiles()
        self.open_actions("Account 001")
        self.page.get_by_role("menuitem", name="Extensions", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog.get_by_text("Retained Snapshot", exact=True)).to_be_visible()
        dialog.get_by_role("checkbox").first.check()
        self.capture("extensions")
        dialog.get_by_role("button", name="Save extensions", exact=True).click()
        expect(dialog).not_to_be_visible()
        body = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "PUT" and path.endswith("/extensions")
        )
        self.assertEqual(
            body,
            {
                "row_version": 8,
                "extension_ids": ["extension-one"],
                "retained_refs": ["snapshot-orphan"],
            },
        )

    def test_stage_two_extension_upload_uses_multipart_boundary(self):
        self.page.goto(f"{self.origin}/extensions/")
        self.page.get_by_label("Choose local XPI", exact=True).set_input_files(
            {"name": "safe.xpi", "mimeType": "application/zip", "buffer": b"synthetic xpi payload"}
        )
        expect(self.page.get_by_text("Extension uploaded", exact=True)).to_be_visible()
        self.assertTrue(self.multipart_content_type.startswith("multipart/form-data; boundary="))

    def test_stage_two_cookie_replace_requires_preview_confirmation_and_clears_secret(self):
        self.open_profiles()
        self.open_actions("Account 001")
        self.page.get_by_role("menuitem", name="Cookies", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        dialog.get_by_label("Paste cookies or choose a file", exact=True).fill(
            '[{"name":"session","value":"cookie-secret-value","domain":"example.test","path":"/"}]'
        )
        dialog.get_by_label("Import behavior", exact=True).select_option("replace")
        expect(dialog.get_by_role("button", name="Import cookies", exact=True)).to_be_disabled()
        dialog.get_by_role("button", name="Validate cookies", exact=True).click()
        expect(
            dialog.get_by_text(
                "2 rows: 1 ready to import, 1 skipped, 1 session cookies.", exact=True
            )
        ).to_be_visible()
        self.assertNotIn(
            "cookie-secret-value", " ".join(dialog.get_by_role("status").all_text_contents())
        )
        expect(dialog.get_by_role("button", name="Import cookies", exact=True)).to_be_disabled()
        dialog.get_by_role(
            "checkbox",
            name="I understand replacement removes existing cookies and creates a recovery backup.",
            exact=True,
        ).check()
        dialog.get_by_role("button", name="Import cookies", exact=True).click()
        expect(dialog.get_by_label("Paste cookies or choose a file", exact=True)).to_have_value("")
        expect(dialog).to_contain_text("A recovery backup is available")
        self.capture("cookies")
        body = next(
            json.loads(body) for _, path, body in self.calls if path.endswith("/cookies/import")
        )
        self.assertEqual(
            (body["row_version"], body["mode"], body["confirm_replace"]), (3, "replace", True)
        )

    def test_stage_two_cookie_failed_file_replacement_invalidates_prior_preview(self):
        self.open_profiles()
        self.open_actions("Account 001")
        self.page.get_by_role("menuitem", name="Cookies", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        draft = dialog.get_by_label("Paste cookies or choose a file", exact=True)
        choose_file = dialog.get_by_label("Choose cookie file", exact=True)
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                draft.fill("[]")
                dialog.get_by_role("button", name="Validate cookies", exact=True).click()
                expect(
                    dialog.get_by_role("button", name="Import cookies", exact=True)
                ).to_be_enabled()
                if unreadable:
                    self.page.evaluate(
                        "() => { File.prototype.text = async function () { throw new Error('unreadable') } }"
                    )
                choose_file.set_input_files(
                    {
                        "name": "cookies.json",
                        "mimeType": "application/json",
                        "buffer": b"[]" if unreadable else b" " * (2 * 1024 * 1024 + 1),
                    }
                )
                expect(dialog.get_by_role("alert")).to_contain_text(
                    "Could not read" if unreadable else "exceeds 2 MiB"
                )
                expect(draft).to_have_value("")
                expect(
                    dialog.get_by_role("button", name="Import cookies", exact=True)
                ).to_be_disabled()
                expect(
                    dialog.get_by_role("button", name="Validate cookies", exact=True)
                ).to_be_disabled()
                self.assertEqual(choose_file.input_value(), "")
        self.assertFalse(any(path.endswith("/cookies/import") for _, path, _ in self.calls))

    def test_stage_two_cookie_stale_write_requires_close_and_reload(self):
        self.open_profiles()
        self.open_actions("Account 001")
        self.page.get_by_role("menuitem", name="Cookies", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        dialog.get_by_label("Paste cookies or choose a file", exact=True).fill("[]")
        dialog.get_by_role("button", name="Validate cookies", exact=True).click()
        self.cookie_stale = True
        dialog.get_by_role("button", name="Import cookies", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("changed elsewhere")
        expect(dialog.get_by_role("button", name="Import cookies", exact=True)).to_be_disabled()
        self.assertEqual(sum(path.endswith("/cookies/import") for _, path, _ in self.calls), 1)
        dialog.get_by_role("button", name="Close and reload profile", exact=True).click()
        expect(dialog).not_to_be_visible()


def load_tests(loader, tests, pattern):
    # The original suite runs separately in CI; do not repeat inherited tests.
    return unittest.TestSuite(
        StageTwoUI(name) for name in StageTwoUI.__dict__ if name.startswith("test_stage_two_")
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
