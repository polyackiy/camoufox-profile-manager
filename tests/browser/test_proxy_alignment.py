"""A profile must not tell a page where this computer really is.

Setting coordinates turns Camoufox's geoip off — the only way it keeps them —
and that same branch is what fills the timezone. Without the fill this exercises,
Firefox falls back to the host machine's own zone, which is the real machine
showing through.

The exit address is named rather than looked up. What belongs here is the part
only a browser can answer — that the filled timezone is what Intl reports — and
resolving an address over HTTP is neither that nor deterministic: it was the last
thing in this suite needing the internet, and it made the assertion depend on
where the machine sits. The lookup itself, including endpoints that answer with a
page instead of an address, is covered in tests/unit/test_proxy_check.py.

Run with:  uv run pytest -m browser
"""

import pytest
from camoufox import AsyncCamoufox

from camoufox_pm.core import proxy_check
from camoufox_pm.core.models import BrowserSettings, Profile
from tests.browser.support import GEOIP_ADDRESS


@pytest.fixture
def named_exit_address(monkeypatch):
    """Answer the exit-address lookup without leaving the machine."""

    async def fixed(proxy, timeout=None):
        return GEOIP_ADDRESS, 1

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", fixed)
    return GEOIP_ADDRESS


CLOCK = "() => Intl.DateTimeFormat().resolvedOptions().timeZone"


async def timezone_seen(options, user_data_dir):
    launch = dict(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        return await page.evaluate(CLOCK)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_coordinates_no_longer_leak_this_computers_timezone(tmp_path, named_exit_address):
    """The fix, against the behaviour it replaces.

    Launches the same profile twice: once with the raw options, which is what
    used to reach the browser, and once after filling in what geoip would have.
    """
    profile = Profile(
        name="coords",
        browser_settings=BrowserSettings(os="windows", geolocation={"lat": 35.68, "lon": 139.69}),
    )
    raw = profile.to_camoufox_launch_options()
    assert raw["geoip"] is False, "coordinates are expected to turn geoip off"
    assert "timezone" not in raw["config"]

    host = await timezone_seen(raw, tmp_path / "raw")

    filled = profile.to_camoufox_launch_options()
    await proxy_check.fill_what_geoip_would_have(profile.proxy, filled)
    assert filled["config"].get("timezone"), "the exit address should have supplied a timezone"
    if filled["config"]["timezone"] == host:
        pytest.skip(f"this machine sits in the timezone {named_exit_address} resolves to")

    spoofed = await timezone_seen(filled, tmp_path / "filled")

    assert spoofed == filled["config"]["timezone"]
    assert spoofed != host, "the browser is still reporting this computer's timezone"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_an_explicit_timezone_is_never_overwritten(tmp_path, named_exit_address):
    """The user's own answer wins over anything the address suggests."""
    profile = Profile(
        name="explicit",
        browser_settings=BrowserSettings(
            os="windows",
            timezone="Pacific/Auckland",
            geolocation={"lat": -36.85, "lon": 174.76},
        ),
    )
    options = profile.to_camoufox_launch_options()
    await proxy_check.fill_what_geoip_would_have(profile.proxy, options)

    assert options["config"]["timezone"] == "Pacific/Auckland"
    assert await timezone_seen(options, tmp_path / "explicit") == "Pacific/Auckland"
