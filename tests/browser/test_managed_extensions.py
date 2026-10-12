"""Persistent Firefox must load, remove and replace managed extension snapshots."""

from pathlib import Path

import pytest

from camoufox_pm.core.extension_library import ExtensionLibrary
from tests.browser.support import GEOIP_ADDRESS
from tests.unit.test_extension_library import xpi


@pytest.mark.browser
@pytest.mark.asyncio
async def test_managed_extension_load_remove_upgrade_and_archive(
    profile_manager, local_sites, tmp_path
):
    library = ExtensionLibrary(profile_manager)
    first = await library.upload(xpi("1.0", addon_id="uBlock0@raymondhill.net"))
    second = await library.upload(xpi("2.0", addon_id="uBlock0@raymondhill.net"))
    profile = await profile_manager.create_profile("Browser extension", generate_fingerprint=False)

    async def observe(profile_id):
        await profile_manager.launch_browser(profile_id, headless=True, geoip=GEOIP_ADDRESS)
        session = profile_manager.browser_sessions.active_sessions[profile_id]
        page = await session.camoufox.browser.new_page()
        await page.goto(local_sites.first)
        value = await page.get_attribute("html", "data-extension-proof")
        await profile_manager.close_browser(profile_id)
        return value

    await library.assign(profile.id, [first["id"]], 0)
    assert await observe(profile.id) == "1.0"
    current = await profile_manager.get_profile(profile.id)
    await library.assign(profile.id, [], current.row_version)
    assert await observe(profile.id) is None
    current = await profile_manager.get_profile(profile.id)
    await library.assign(profile.id, [second["id"]], current.row_version)
    assert await observe(profile.id) == "2.0"
    await library.delete(second["id"], 0)
    destination = tmp_path / "portable.zip"
    exported = await profile_manager.export_profile(profile.id, str(destination))
    imported = await profile_manager.import_profile(str(exported))
    assert await observe(imported.id) == "2.0"
    assert all(not Path(ref).is_absolute() for ref in imported.extensions)
