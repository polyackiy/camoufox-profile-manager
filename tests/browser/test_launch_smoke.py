"""Opt-in smoke test that launches a real Camoufox browser.

Run explicitly (downloads the Camoufox binary the first time):

    uv run camoufox fetch
    uv run pytest -m browser
"""

import asyncio

import pytest
from camoufox import AsyncCamoufox

from camoufox_pm.core.browser_session import BrowserSessionManager
from camoufox_pm.core.models import BrowserSettings, Profile, WebRTCMode
from tests.browser.support import offline_launch


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
    options = offline_launch(profile.to_camoufox_launch_options())
    options["headless"] = True
    options["user_data_dir"] = str(tmp_path / "profile")

    async with AsyncCamoufox(**options) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        assert await page.evaluate("navigator.hardwareConcurrency") == 4


@pytest.mark.browser
@pytest.mark.asyncio
async def test_timezone_applies(tmp_path):
    """A profile timezone must drive Intl in the browser."""
    profile = Profile(
        name="tz", browser_settings=BrowserSettings(os="windows", timezone="Europe/Berlin")
    )
    options = offline_launch(profile.to_camoufox_launch_options())
    options["headless"] = True
    options["user_data_dir"] = str(tmp_path / "tz")

    async with AsyncCamoufox(**options) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        resolved = await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        assert resolved == "Europe/Berlin"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_webrtc_mode_none_removes_the_api(tmp_path):
    """webrtc_mode="none" must actually remove RTCPeerConnection."""
    profile = Profile(name="no-rtc", browser_settings=BrowserSettings(webrtc_mode=WebRTCMode.NONE))
    options = offline_launch(profile.to_camoufox_launch_options())
    options["headless"] = True
    options["user_data_dir"] = str(tmp_path / "no-rtc")
    assert options["block_webrtc"] is True

    async with AsyncCamoufox(**options) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        assert await page.evaluate("typeof RTCPeerConnection !== 'undefined'") is False


@pytest.mark.browser
@pytest.mark.asyncio
async def test_webrtc_stays_available_in_other_modes(tmp_path):
    """Any mode other than "none" must leave WebRTC usable."""
    profile = Profile(
        name="rtc",
        browser_settings=BrowserSettings(
            webrtc_mode=WebRTCMode.REPLACE, webrtc_public_ip="203.0.113.7"
        ),
    )
    options = offline_launch(profile.to_camoufox_launch_options())
    options["headless"] = True
    options["user_data_dir"] = str(tmp_path / "rtc")
    assert "block_webrtc" not in options
    assert options["config"]["webrtc:ipv4"] == "203.0.113.7"

    async with AsyncCamoufox(**options) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        assert await page.evaluate("typeof RTCPeerConnection !== 'undefined'") is True


@pytest.mark.browser
@pytest.mark.asyncio
async def test_closing_the_browser_prunes_the_session(tmp_path):
    """Closing the browser (as a user does) must clean up and notify exactly once."""
    manager = BrowserSessionManager()
    exits = []

    async def on_exit(profile_id):
        exits.append(profile_id)

    options = offline_launch(Profile(name="life").to_camoufox_launch_options())
    options["headless"] = True
    options["user_data_dir"] = str(tmp_path / "life")

    session = await manager.launch("p1", options, on_exit=on_exit)
    assert manager.is_running("p1") is True

    # The close races the manager's own teardown; either order must converge.
    try:
        await session.camoufox.browser.close()
    except Exception:  # noqa: BLE001 - the close handler may win the race
        pass

    for _ in range(50):
        if not manager.is_running("p1"):
            break
        await asyncio.sleep(0.1)

    assert manager.is_running("p1") is False
    assert exits == ["p1"]
