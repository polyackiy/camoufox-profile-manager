"""A combined daily workflow against real Camoufox and portable profile files."""

import io
import json
import time
import zipfile

import pytest

from camoufox_pm.core.backups import ProfileBackupManager
from camoufox_pm.core.cookie_transfer import CookieTransfer
from camoufox_pm.core.extension_library import ExtensionLibrary
from camoufox_pm.core.profile_templates import ProfileTemplates
from camoufox_pm.core.saved_filters import SavedFilterStore, ViewConfig
from tests.browser.support import GEOIP_ADDRESS


def workflow_addon() -> bytes:
    """A local synthetic content script; no third-party add-on or account data."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "manifest_version": 2,
                    "name": "Stage two workflow proof",
                    "version": "1.0",
                    "browser_specific_settings": {"gecko": {"id": "stage-two@cpm.test"}},
                    "content_scripts": [
                        {
                            "matches": ["http://127.0.0.1/*"],
                            "js": ["proof.js"],
                            "run_at": "document_end",
                        }
                    ],
                }
            ),
        )
        archive.writestr("proof.js", "document.documentElement.dataset.cpmStageTwo = 'loaded';")
    return buffer.getvalue()


@pytest.mark.browser
async def test_template_extension_cookie_archive_and_saved_view(
    profile_manager, tmp_path, local_sites
):
    manager = profile_manager
    library = ExtensionLibrary(manager)
    templates = ProfileTemplates(manager)
    extension = await library.upload(workflow_addon())
    template = await templates.create(
        "Daily workflow",
        {"browser_ui": "desktop"},
        tags=["daily"],
        extension_ids=[extension["id"]],
    )
    # The template owns its add-on snapshot even after the library entry disappears.
    await library.delete(extension["id"], extension["row_version"])
    first = await templates.create_profile(template["id"], template["row_version"], "First")
    cookies = CookieTransfer(manager, ProfileBackupManager(manager))
    imported = await cookies.import_cookies(
        first.id,
        json.dumps(
            [
                {
                    "name": "persistent-proof",
                    "value": "retained",
                    "domain": "127.0.0.1",
                    "path": "/",
                    "expires": int(time.time()) + 86400,
                    "httpOnly": True,
                    "secure": False,
                    "sameSite": "Lax",
                },
                {
                    "name": "session-proof",
                    "value": "one-session",
                    "domain": "127.0.0.1",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Strict",
                },
            ]
        ),
        "json",
        "merge",
        first.row_version,
    )
    assert imported["imported"] == 2
    first = await manager.get_profile(first.id)
    # Saving a populated profile as a template must still exclude its cookies.
    populated = await templates.from_profile(first.id, first.row_version, "Settings only")
    second = await templates.create_profile(populated["id"], populated["row_version"], "Second")
    assert first.id != second.id and first.storage_path != second.storage_path
    assert first.fingerprint is second.fingerprint is None
    assert first.proxy is second.proxy is None

    archive = await manager.export_profile(first.id, tmp_path / "portable.zip")
    restored = await manager.import_profile(archive, name="Restored daily")
    assert restored.extensions == first.extensions and restored.tags == ["daily"]
    tagged = await manager.bulk_update_tags(
        [{"id": restored.id, "row_version": restored.row_version}],
        ["restored"],
        [],
    )
    assert tagged["skipped"] == []
    views = SavedFilterStore(manager.storage)
    view = views.create("Restored daily", ViewConfig(search="Restored", tags=["daily", "restored"]))
    stored = SavedFilterStore(manager.storage).get(view.id)
    matches = await manager.storage.list_profiles(
        {
            "search": stored.config.search,
            "tags": stored.config.tags,
        }
    )
    assert [profile.id for profile in matches] == [restored.id]

    try:
        for profile in (first, second, restored):
            await manager.launch_browser(profile.id, headless=True, geoip=GEOIP_ADDRESS)
            browser = manager.browser_sessions.active_sessions[profile.id].camoufox.browser
            page = await browser.new_page()
            await page.goto(local_sites.first)
            await page.wait_for_function(
                "document.documentElement.dataset.cpmStageTwo === 'loaded'"
            )
            actual = {cookie["name"]: cookie for cookie in await browser.cookies(local_sites.first)}
            if profile.id == second.id:
                assert "persistent-proof" not in actual and "session-proof" not in actual
            else:
                assert actual["persistent-proof"]["value"] == "retained"
                assert actual["persistent-proof"]["httpOnly"] is True
                assert actual["session-proof"]["value"] == "one-session"
                assert actual["session-proof"]["expires"] == -1
                assert "persistent-proof" not in await page.evaluate("document.cookie")
            assert (await manager.get_profile(profile.id)).fingerprint
            await manager.close_browser(profile.id)
    finally:
        await manager.close_all_browsers()
        await manager.browser_sessions.stop_heartbeat()
