"""Switching upstream browser chrome must preserve the profile's identity/data."""

import pytest
from camoufox.async_api import AsyncCamoufox

from camoufox_pm.core.fingerprint_store import resolve
from camoufox_pm.core.models import Profile
from tests.browser.support import offline_launch, serve_local_sites
from tests.browser.test_fingerprint_stability import IDENTITY


@pytest.mark.browser
async def test_interface_switch_preserves_pinned_hardware_and_session(tmp_path):
    profile = Profile(name="interface")
    original = offline_launch(profile.to_camoufox_launch_options())
    pin = resolve(original)
    assert pin
    seen = []
    with serve_local_sites() as sites:
        for mode in ("camoufox", "desktop"):
            profile.browser_settings.browser_ui = mode
            options = offline_launch(profile.to_camoufox_launch_options())
            options["config"] = {**pin, **options["config"]}
            options.update(headless=True, user_data_dir=str(tmp_path / "identity"))
            async with AsyncCamoufox(**options) as browser:
                page = await browser.new_page()
                await page.goto(sites.first)
                seen.append(await page.evaluate(IDENTITY))
                if mode == "camoufox":
                    # Use a site's own storage path, rather than the automation
                    # protocol's cookie injection/expiry conversion.
                    await page.evaluate(
                        "document.cookie = 'session-check=retained; Max-Age=31536000; Path=/'"
                    )
                    await page.evaluate("localStorage.setItem('session-check', 'retained')")
                assert "session-check=retained" in await page.evaluate("document.cookie")
                assert await page.evaluate("localStorage.getItem('session-check')") == "retained"
    assert seen[0] == seen[1]
