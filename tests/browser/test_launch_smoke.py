"""Opt-in smoke test that launches a real Camoufox browser.

Run explicitly (downloads the Camoufox binary the first time):

    uv run camoufox fetch
    uv run pytest -m browser
"""

import pytest

from camoufox import AsyncCamoufox

from camoufox_pm.core.models import BrowserSettings, Profile


@pytest.mark.browser
@pytest.mark.asyncio
async def test_config_reaches_the_browser():
    """The anti-detect config must actually apply in a running browser."""
    async with AsyncCamoufox(
        headless=True, os="windows", config={"navigator.hardwareConcurrency": 8}
    ) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        hardware_concurrency = await page.evaluate("navigator.hardwareConcurrency")
        assert hardware_concurrency == 8


@pytest.mark.browser
@pytest.mark.asyncio
async def test_profile_launch_options_drive_the_browser(tmp_path):
    """A Profile's launch options should start a working browser."""
    profile = Profile(
        name="smoke",
        browser_settings=BrowserSettings(os="windows", hardware_concurrency=4),
    )
    options = profile.to_camoufox_launch_options()
    options["headless"] = True
    options["user_data_dir"] = str(tmp_path / "profile")

    async with AsyncCamoufox(**options) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        assert await page.evaluate("navigator.hardwareConcurrency") == 4
