"""The core antidetect property: a profile is the same machine every session.

Camoufox generates a fresh fingerprint per launch, so without pinning the same
profile reports different hardware each time it opens — which is exactly what a
long-lived account must never do. These tests launch a real browser twice and
compare what a site would actually observe.

Run with:  uv run pytest -m browser
"""

import hashlib

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


CANVAS = """() => {
  const c = document.createElement('canvas');
  c.width = 240; c.height = 60;
  const x = c.getContext('2d');
  x.textBaseline = 'top';
  x.font = '16px Arial';
  x.fillStyle = '#f60'; x.fillRect(0, 0, 120, 25);
  x.fillStyle = '#069'; x.fillText('canvas probe', 2, 18);
  return c.toDataURL();
}"""


async def read_canvas(options, user_data_dir, url):
    launch = dict(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        value = await page.evaluate(CANVAS)
        return hashlib.sha256(value.encode()).hexdigest()[:16]


def pinned_options(profile):
    """Launch options with the profile's machine pinned, as a real launch does."""
    fresh = profile.to_camoufox_launch_options()
    fresh["config"] = {**profile.fingerprint, **fresh["config"]}
    return fresh


@pytest.mark.browser
@pytest.mark.asyncio
async def test_stable_canvas_survives_a_relaunch(tmp_path):
    """The whole point of the setting: the canvas reads the same next session.

    Needs both halves — the pref stops the pixel randomisation, and the pinned
    fonts:spacing_seed keeps text rendering identical. Text is deliberately drawn
    here because it follows the font seed rather than the canvas path, and that
    is what made this look broken while only the pref was in place.
    """
    profile = Profile(
        name="stable-canvas",
        browser_settings=BrowserSettings(os="windows", stable_canvas=True),
    )
    options = profile.to_camoufox_launch_options()
    assert options["firefox_user_prefs"] == {"privacy.baselineFingerprintingProtection": False}

    profile.fingerprint = fingerprint_store.resolve(options)
    assert profile.fingerprint, "the pin carries the font seed this relies on"

    first = await read_canvas(pinned_options(profile), tmp_path / "a", "https://example.com")
    second = await read_canvas(pinned_options(profile), tmp_path / "a", "https://example.com")
    assert first == second, f"canvas drifted between launches: {first} vs {second}"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_a_randomised_canvas_profile_still_drifts(tmp_path):
    """Guards the default. If this starts passing, the setting is redundant."""
    profile = Profile(
        name="randomised-canvas",
        browser_settings=BrowserSettings(os="windows", stable_canvas=False),
    )
    options = profile.to_camoufox_launch_options()
    assert "firefox_user_prefs" not in options

    profile.fingerprint = fingerprint_store.resolve(options)
    first = await read_canvas(pinned_options(profile), tmp_path / "b", "https://example.com")
    second = await read_canvas(pinned_options(profile), tmp_path / "b", "https://example.com")
    assert first != second, "expected the default to keep randomising the canvas"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_stable_canvas_is_linkable_across_sites(tmp_path):
    """The cost, asserted rather than only documented.

    A stable canvas is the same everywhere, so two sites can tell they are
    looking at one machine. That is what real hardware does and why this is a
    per-profile choice instead of the default.
    """
    profile = Profile(
        name="linkable",
        browser_settings=BrowserSettings(os="windows", stable_canvas=True),
    )
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint

    one = await read_canvas(pinned_options(profile), tmp_path / "c", "https://example.com")
    two = await read_canvas(pinned_options(profile), tmp_path / "c", "https://www.iana.org")
    assert one == two, "a stable canvas is expected to be identical across sites"


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
