"""The core antidetect property: a profile is the same machine every session.

Camoufox generates a fresh fingerprint per launch, so without pinning the same
profile reports different hardware each time it opens — which is exactly what a
long-lived account must never do. These tests launch a real browser twice and
compare what a site would actually observe.

Run with:  uv run pytest -m browser
"""

import pytest
from camoufox import AsyncCamoufox

from camoufox_pm.core import fingerprint_store
from camoufox_pm.core.models import BrowserSettings, Profile

IDENTITY = """() => {
  const gl = document.createElement('canvas').getContext('webgl');
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    cores: navigator.hardwareConcurrency,
    screen: screen.width + 'x' + screen.height,
    gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
  };
}"""


async def observe(options, user_data_dir):
    """Launch with these options and report what a page would see."""
    launch = dict(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        return await page.evaluate(IDENTITY)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_unpinned_profiles_look_like_new_hardware(tmp_path):
    """Guards the premise: without a pin, the identity really does drift.

    If this ever starts passing as "stable", Camoufox changed its behaviour and
    the pinning below may no longer be needed.
    """
    profile = Profile(name="drift", browser_settings=BrowserSettings(os="windows"))

    first = await observe(profile.to_camoufox_launch_options(), tmp_path / "drift")
    second = await observe(profile.to_camoufox_launch_options(), tmp_path / "drift")

    assert first != second, "expected an unpinned profile to drift between launches"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_a_pinned_profile_is_the_same_machine_every_launch(tmp_path):
    """The feature itself: pin once, then every launch reports that machine."""
    profile = Profile(name="pinned", browser_settings=BrowserSettings(os="windows"))
    options = profile.to_camoufox_launch_options()

    profile.fingerprint = fingerprint_store.resolve(options)
    assert profile.fingerprint, "Camoufox should have resolved a fingerprint to pin"

    def pinned_options():
        fresh = profile.to_camoufox_launch_options()
        fresh["config"] = {**profile.fingerprint, **fresh["config"]}
        return fresh

    first = await observe(pinned_options(), tmp_path / "pinned")
    second = await observe(pinned_options(), tmp_path / "pinned")

    assert first == second, f"identity drifted: {first} vs {second}"
    assert first["userAgent"], "expected a real user agent"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_a_pinned_profile_survives_losing_its_directory(tmp_path):
    """The pin lives in the database, so the identity is portable.

    This is what makes backing up or moving a profile meaningful: the browser
    data directory can be gone and the machine identity still comes back.
    """
    profile = Profile(name="portable", browser_settings=BrowserSettings(os="windows"))
    options = profile.to_camoufox_launch_options()
    profile.fingerprint = fingerprint_store.resolve(options)
    assert profile.fingerprint

    def pinned_options():
        fresh = profile.to_camoufox_launch_options()
        fresh["config"] = {**profile.fingerprint, **fresh["config"]}
        return fresh

    original = await observe(pinned_options(), tmp_path / "original")
    elsewhere = await observe(pinned_options(), tmp_path / "elsewhere")

    assert original == elsewhere


@pytest.mark.browser
@pytest.mark.asyncio
async def test_profile_overrides_still_win_over_the_pin(tmp_path):
    """A pinned machine must not freeze out the user's own settings."""
    profile = Profile(
        name="override",
        browser_settings=BrowserSettings(os="windows", hardware_concurrency=12),
    )
    options = profile.to_camoufox_launch_options()
    profile.fingerprint = fingerprint_store.resolve(options)
    assert profile.fingerprint

    merged = profile.to_camoufox_launch_options()
    merged["config"] = {**profile.fingerprint, **merged["config"]}
    seen = await observe(merged, tmp_path / "override")

    assert seen["cores"] == 12
