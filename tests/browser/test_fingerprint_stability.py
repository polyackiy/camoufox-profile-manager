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
from camoufox_pm.core.models import BrowserSettings, Profile, ProxyConfig, ProxyType
from tests.browser.support import offline_launch

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
    launch = offline_launch(options)
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

    Three launches, not two: Camoufox draws from a finite catalogue of machines,
    so two consecutive draws can coincide by chance and did once here. Three
    identical draws would mean it is not drawing at all.
    """
    profile = Profile(name="drift", browser_settings=BrowserSettings(os="windows"))

    seen = [
        await observe(profile.to_camoufox_launch_options(), tmp_path / "drift") for _ in range(3)
    ]

    assert any(other != seen[0] for other in seen[1:]), (
        f"expected an unpinned profile to drift between launches, got {seen[0]}"
    )


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


async def read_canvases(options, user_data_dir, *urls):
    """Read the canvas at each URL within a single launch.

    One session, several origins: that is the only way to observe the per-site
    keying, since a fresh launch re-randomises and would mask it.
    """
    launch = offline_launch(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        seen = []
        for url in urls:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            value = await page.evaluate(CANVAS)
            seen.append(hashlib.sha256(value.encode()).hexdigest()[:16])
        return seen


async def read_canvas(options, user_data_dir, url):
    """One canvas from one launch, which is what the cross-launch tests compare."""
    return (await read_canvases(options, user_data_dir, url))[0]


def pinned_options(profile):
    """Launch options with the profile's machine pinned, as a real launch does."""
    fresh = profile.to_camoufox_launch_options()
    fresh["config"] = {**profile.fingerprint, **fresh["config"]}
    return fresh


@pytest.mark.browser
@pytest.mark.asyncio
async def test_stable_canvas_survives_a_relaunch(tmp_path, local_sites):
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

    first = await read_canvas(pinned_options(profile), tmp_path / "a", local_sites.first)
    second = await read_canvas(pinned_options(profile), tmp_path / "a", local_sites.first)
    assert first == second, f"canvas drifted between launches: {first} vs {second}"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_a_randomised_canvas_profile_still_drifts(tmp_path, local_sites):
    """Guards the default. If this starts passing, the setting is redundant."""
    profile = Profile(
        name="randomised-canvas",
        browser_settings=BrowserSettings(os="windows", stable_canvas=False),
    )
    options = profile.to_camoufox_launch_options()
    assert "firefox_user_prefs" not in options

    profile.fingerprint = fingerprint_store.resolve(options)
    first = await read_canvas(pinned_options(profile), tmp_path / "b", local_sites.first)
    second = await read_canvas(pinned_options(profile), tmp_path / "b", local_sites.first)
    assert first != second, "expected the default to keep randomising the canvas"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_stable_canvas_is_linkable_across_sites(tmp_path, local_sites):
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

    one, two = await read_canvases(
        pinned_options(profile), tmp_path / "c", local_sites.first, local_sites.second
    )
    assert one == two, "a stable canvas is expected to be identical across sites"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_the_pinned_machine_decides_the_operating_system_a_page_reads(tmp_path):
    """Every property naming the OS must come from the pin, including appVersion.

    Camoufox builds a preset fingerprint without navigator.appVersion, so the
    value is generated per launch from the profile's *setting* instead. That is
    invisible until the two disagree — a state this product allows on purpose
    and reports as os_mismatch — and then a page reads platform "Linux x86_64"
    beside appVersion "5.0 (Windows)". Measured, in that order, before the fill.

    The upstream half of this is daijro/camoufox#753: a preset launched through
    Camoufox's own path leaks the *host* OS the same way.
    """
    pinned = next(
        machine
        for machine in (
            fingerprint_store.resolve(
                Profile(
                    name="preset", browser_settings=BrowserSettings(os="linux")
                ).to_camoufox_launch_options(),
                preset=fingerprint_store.get_preset(candidate["id"]),
            )
            for candidate in fingerprint_store.list_presets("linux")[:8]
        )
        if machine
    )

    profile = Profile(name="preset", browser_settings=BrowserSettings(os="linux"))
    profile.fingerprint = pinned
    # The setting drifts away from the pinned machine, which is allowed.
    profile.browser_settings.os = "windows"

    launch = offline_launch(pinned_options(profile))
    launch["headless"] = True
    launch["user_data_dir"] = str(tmp_path / "preset")
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        # Read by the page itself: automation's world is not what a site gets.
        await page.set_content(
            "<script>document.title = navigator.platform + '|' + navigator.appVersion</script>"
        )
        platform, app_version = (await page.title()).split("|")

    assert "Linux" in platform, f"the pinned machine is Linux, the page saw {platform}"
    assert app_version == "5.0 (X11)", (
        f"platform says {platform} while appVersion says {app_version} — "
        "the setting overrode the pinned machine on one property"
    )


@pytest.mark.browser
@pytest.mark.asyncio
async def test_the_two_local_origins_are_different_sites(tmp_path, local_sites):
    """Guards the fixture, and with it the linkability test above.

    That test only means something if the browser counts the two origins as
    different sites. With example.com and iana.org that went without saying;
    with two loopback names it does not, and if they ever collapsed into one
    site the assertion would pass while proving nothing.

    One launch, three visits: randomisation is stable per site within a session,
    so a hash that changes on the second origin and returns on the third is the
    keying itself, not launch-to-launch drift.
    """
    profile = Profile(
        name="two-sites",
        browser_settings=BrowserSettings(os="windows", stable_canvas=False),
    )
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint

    first, second, again = await read_canvases(
        pinned_options(profile),
        tmp_path / "sites",
        local_sites.first,
        local_sites.second,
        local_sites.first,
    )

    assert first == again, f"the same site drifted inside one session: {first} vs {again}"
    assert first != second, (
        f"{local_sites.first} and {local_sites.second} look like one site to the browser, "
        "so the linkability test above no longer proves anything"
    )


@pytest.mark.browser
@pytest.mark.asyncio
async def test_refreshing_the_browser_version_keeps_the_device(tmp_path):
    """A profile can move onto the installed browser and stay the same computer.

    Simulates a profile pinned months ago on an older release, then refreshed
    against the browser actually on disk. What a page sees afterwards must be the
    old device with the current browser.
    """
    profile = Profile(name="ageing", browser_settings=BrowserSettings(os="windows"))
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint

    before = await observe(pinned_options(profile), tmp_path / "before")

    # Age the pin: an older browser, same hardware.
    aged = dict(profile.fingerprint)
    aged["navigator.userAgent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:100.0) Gecko/20100101 Firefox/100.0"
    )
    profile.fingerprint = aged
    assert fingerprint_store.is_outdated(profile.fingerprint)

    stale = await observe(pinned_options(profile), tmp_path / "stale")
    assert "rv:100.0" in stale["userAgent"], "the aged pin should be what the page sees"

    profile.fingerprint = fingerprint_store.refresh_browser_version(
        profile.fingerprint,
        fingerprint_store.resolve(profile.to_camoufox_launch_options()),
    )
    after = await observe(pinned_options(profile), tmp_path / "after")

    assert not fingerprint_store.is_outdated(profile.fingerprint)
    assert after["userAgent"] == before["userAgent"], "back on the installed version"
    # The device is untouched, which is the point.
    for key in ("screen", "cores", "gpu", "platform"):
        assert after[key] == before[key], f"{key} changed across a version refresh"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_refreshing_follows_the_pinned_os_not_the_settings(tmp_path):
    """The refreshed user agent must match the hardware, not a drifted setting.

    Regression: a Windows pin on a profile whose OS setting had been changed to
    macOS came back with a macOS user agent while still reporting Win32 and an
    ANGLE Direct3D11 GPU — a Mac browser on a Windows machine.
    """
    profile = Profile(name="drifted", browser_settings=BrowserSettings(os="windows"))
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint
    assert fingerprint_store.pinned_os(profile.fingerprint) == "windows"

    # The setting drifts away from the pinned machine.
    profile.browser_settings.os = "macos"

    options = profile.to_camoufox_launch_options()
    options["os"] = fingerprint_store.pinned_os(profile.fingerprint)
    profile.fingerprint = fingerprint_store.refresh_browser_version(
        profile.fingerprint, fingerprint_store.resolve(options)
    )

    seen = await observe(pinned_options(profile), tmp_path / "drift")
    assert "Windows" in seen["userAgent"], f"user agent left the pinned OS: {seen['userAgent']}"
    assert seen["platform"] == "Win32"


@pytest.mark.browser
@pytest.mark.asyncio
async def test_refreshing_keeps_the_canvas_identical(tmp_path, local_sites):
    """The seeds must survive a refresh, or the canvas silently changes."""
    profile = Profile(
        name="refresh-canvas",
        browser_settings=BrowserSettings(os="windows", stable_canvas=True),
    )
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint

    before = await read_canvas(pinned_options(profile), tmp_path / "c1", local_sites.first)

    profile.fingerprint = fingerprint_store.refresh_browser_version(
        profile.fingerprint,
        fingerprint_store.resolve(profile.to_camoufox_launch_options()),
    )
    after = await read_canvas(pinned_options(profile), tmp_path / "c2", local_sites.first)

    assert before == after, "a version refresh must not move the canvas"


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


@pytest.mark.browser
@pytest.mark.asyncio
async def test_pinning_a_device_does_not_need_a_working_proxy(tmp_path):
    """Pinning freezes hardware, and hardware has no geography in it.

    Resolving used to run with geoip on, so it reached the internet *through*
    the profile's proxy to geolocate it — and creating a profile from a device
    preset failed outright when that proxy was unreachable, which says nothing
    about its screen or its GPU.
    """
    profile = Profile(
        name="pinned-behind-a-dead-proxy",
        browser_settings=BrowserSettings(os="windows"),
        proxy=ProxyConfig(type=ProxyType.HTTP, server="does-not-resolve.invalid:9999"),
    )

    pinned = fingerprint_store.resolve(profile.to_camoufox_launch_options())

    assert pinned, "an unreachable proxy must not stop the machine being pinned"
    assert pinned.get("navigator.platform"), "the hardware is what gets frozen"
    # Geography stays out of the pin so it can follow the proxy at launch.
    assert not any(key.startswith(("geolocation:", "timezone", "webrtc:")) for key in pinned)
