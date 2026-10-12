"""The core antidetect property: a profile is the same machine every session.

Camoufox generates a fresh fingerprint per launch, so without pinning the same
profile reports different hardware each time it opens — which is exactly what a
long-lived account must never do. These tests launch a real browser twice and
compare what a site would actually observe.

Run with:  uv run pytest -m browser
"""

import base64
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

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


# Preserve the PNG read itself exactly; collect raw pixels and font metrics only
# for the restart failure diagnostic. Never print the image or complete config.
CANVAS_DIAGNOSTIC = CANVAS.replace(
    "return c.toDataURL();",
    """const value = c.toDataURL();
  const fontStatus = document.fonts.status;
  const fontAvailable = document.fonts.check('16px Arial');
  const m = x.measureText('canvas probe');
  const metrics = {width: m.width, left: m.actualBoundingBoxLeft,
    right: m.actualBoundingBoxRight, ascent: m.actualBoundingBoxAscent,
    descent: m.actualBoundingBoxDescent};
  const pixelBytes = x.getImageData(0, 0, c.width, c.height).data;
  // Keep hashing in JS: Gecko's isolated world wraps typed arrays as Xrays,
  // which crypto.subtle.digest cannot consume as a native BufferSource.
  let h = 2166136261;
  for (let i = 0; i < pixelBytes.length; i++) h = Math.imul(h ^ pixelBytes[i], 16777619) >>> 0;
  const pixelHash = h.toString(16).padStart(8, '0');
  return {value, pixelHash, fontStatus, fontAvailable, metrics,
    dpr: devicePixelRatio, font: x.font, colorSpace: x.getContextAttributes?.().colorSpace ?? null};""",
)


def canvas_launch_diagnostic(options, snapshot, png_hash):
    """Bounded synthetic diagnostics: hashes instead of fonts/config/images."""
    config = options.get("config", {})

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]

    return {
        "pngHash": png_hash,
        **{key: value for key, value in snapshot.items() if key != "value"},
        "configHash": digest(config),
        "configKeyHashes": {key[:80]: digest(config[key]) for key in sorted(config)[:64]},
        "baselineProtection": options.get("firefox_user_prefs", {}).get(
            "privacy.baselineFingerprintingProtection"
        ),
        "rasterPrefs": {
            key: value
            for key, value in options.get("firefox_user_prefs", {}).items()
            if key
            in {
                "gfx.canvas.azure.backends",
                "gfx.canvas.accelerated",
                "gfx.font_rendering.cleartype_params.rendering_mode",
                "gfx.font_rendering.cleartype_params.force_gdi_classic_for_families",
                "gfx.font_rendering.cleartype_params.force_gdi_classic_max_size",
            }
        },
    }


def canvas_profile_diagnostic(directory):
    """Inspect only renderer preferences and named caches of this test profile."""
    directory = Path(directory)
    prefs = {}
    prefs_file = directory / "prefs.js"
    pref_files = {
        name: {"bytes": (directory / name).stat().st_size}
        for name in ("prefs.js", "user.js", "prefs.js.playwright")
        if (directory / name).is_file()
    }
    if prefs_file.is_file() and prefs_file.stat().st_size <= 2_000_000:
        for line in prefs_file.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.fullmatch(r'user_pref\("([^"\\]+)", (.+)\);', line.strip())
            if not match:
                continue
            key = match[1]
            if not key.startswith(("gfx.", "font.")) and key not in {
                "privacy.baselineFingerprintingProtection",
                "privacy.resistFingerprinting",
                "privacy.fingerprintingProtection",
            }:
                continue
            try:
                value = json.loads(match[2])
            except ValueError:
                continue
            if isinstance(value, str):
                # Font-family lists are useful; paths, URLs and unbounded text
                # are not. Hash those rather than publishing their content.
                if len(value) > 200 or "/" in value or "\\" in value or ":" in value:
                    value = {"sha256": hashlib.sha256(value.encode()).hexdigest()[:16]}
            elif not isinstance(value, (bool, int, float, type(None))):
                continue
            prefs[key] = value
    caches = {}
    for name in ("startupCache", "shader-cache", "cache2", "fontcache", "compatibility.ini"):
        path = directory / name
        if path.is_dir():
            caches[name] = {"present": True, "entries": min(sum(1 for _ in path.iterdir()), 1000)}
        elif path.is_file():
            caches[name] = {"present": True, "bytes": path.stat().st_size}
    return {
        "persistedPrefs": dict(sorted(prefs.items())[:128]),
        "prefFiles": pref_files,
        "caches": caches,
    }


async def canvas_graphics_diagnostic(browser):
    """Read bounded public about:support DOM rows, not privileged browser APIs."""
    page = await browser.new_page()
    try:
        await page.goto("about:support", wait_until="load", timeout=15000)
        await (
            page.locator("#graphics-features-tbody tr")
            .nth(1)
            .wait_for(state="attached", timeout=5000)
        )
        return await page.evaluate("""() => {
          const rows = document.querySelectorAll(
            '#graphics-features-tbody tr, #graphics-diagnostics-tbody tr');
          return Array.from(rows).map(row => {
            const label = row.querySelector('th');
            const value = row.querySelector('td');
            const key = label?.getAttribute('data-l10n-id') || label?.textContent || '';
            return {key: key.trim().slice(0, 100), value: value?.textContent?.trim() || ''};
          }).filter(row => /azure|directwrite|cleartype|compositing|font|pixel.?ratio/i.test(row.key))
            .slice(0, 24).map(row => ({...row, value: row.value.slice(0, 200)}));
        }""")
    except Exception as exc:  # Diagnostics must not replace the exact assertion.
        return {"unavailable": type(exc).__name__}
    finally:
        await page.close()


def save_canvas_diagnostic(directory, launch_number, png_value, record):
    """Only the fixed synthetic drawing and sanitized metadata enter artifacts."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    prefix = directory / f"launch-{launch_number}"
    if png_value.startswith("data:image/png;base64,") and len(png_value) < 200_000:
        prefix.with_suffix(".png").write_bytes(base64.b64decode(png_value.split(",", 1)[1]))
    prefix.with_suffix(".json").write_text(json.dumps(record, sort_keys=True, indent=2))


def remove_synthetic_canvas_caches(profile_directory, test_directory):
    """Remove only named caches in one direct child of pytest's temporary root."""
    root = Path(test_directory).resolve()
    profile = Path(profile_directory)
    if profile.is_symlink() or profile.resolve().parent != root:
        raise ValueError("The cache probe requires a direct synthetic test-profile directory")
    removed = []
    for name in ("startupCache", "fontcache"):
        cache = profile / name
        if cache.is_symlink():
            raise ValueError("The cache probe must not follow cache symlinks")
        if cache.is_dir():
            shutil.rmtree(cache)
            removed.append(name)
        elif cache.is_file():
            cache.unlink()
            removed.append(name)
    return removed


async def read_canvases(options, user_data_dir, *urls, diagnostics=None):
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
            if diagnostics is None:
                value = await page.evaluate(CANVAS)
            else:
                snapshot = await page.evaluate(CANVAS_DIAGNOSTIC)
                value = snapshot["value"]
            png_hash = hashlib.sha256(value.encode()).hexdigest()[:16]
            seen.append(png_hash)
            if diagnostics is not None:
                record = canvas_launch_diagnostic(options, snapshot, png_hash)
                # Evaluate a separate identical draw as a real page script.
                # Reading its result through DOM text avoids isolated-world
                # window/typed-array wrappers. This does not replace the exact
                # PNG measurement above or change its stability assertion.
                await page.add_script_tag(
                    content=(
                        "(() => { const result = (" + CANVAS_DIAGNOSTIC + ")();"
                        "const out = document.createElement('pre');"
                        "out.id = 'cpm-canvas-mainworld';"
                        "out.textContent = JSON.stringify(result);"
                        "document.body.appendChild(out); })();"
                    )
                )
                main_snapshot = json.loads(
                    await page.locator("#cpm-canvas-mainworld").text_content()
                )
                record["mainWorld"] = {
                    "pngHash": hashlib.sha256(main_snapshot["value"].encode()).hexdigest()[:16],
                    **{key: item for key, item in main_snapshot.items() if key != "value"},
                }
                diagnostics.append(record)
        diagnostic_dir = os.environ.get("CPM_CANVAS_DIAGNOSTIC_DIR")
        if diagnostics is not None and diagnostic_dir:
            diagnostics[-1]["graphics"] = await canvas_graphics_diagnostic(browser)
    if diagnostics is not None and diagnostic_dir:
        # Firefox persists user prefs on shutdown: inspect only after the
        # context is closed, so first/second snapshots describe disk state.
        diagnostics[-1].update(canvas_profile_diagnostic(user_data_dir))
        save_canvas_diagnostic(diagnostic_dir, len(diagnostics), value, diagnostics[-1])
    return seen


async def read_canvas(options, user_data_dir, url, *, diagnostics=None):
    """One canvas from one launch, which is what the cross-launch tests compare."""
    return (await read_canvases(options, user_data_dir, url, diagnostics=diagnostics))[0]


def pinned_options(profile):
    """Launch options with the profile's machine pinned, as a real launch does."""
    fresh = profile.to_camoufox_launch_options()
    fresh["config"] = {**profile.fingerprint, **fresh["config"]}
    return fresh


def canvas_probe_options(profile, mode):
    """Controlled CI-only backend experiment; default acceptance stays unchanged.

    The beta.36 browser defaults its Azure backend to Skia. Mozilla declares
    gfx.canvas.accelerated as a Windows boolean, so disabling it tests whether
    first-run acceleration selection changes raster output. This is not a
    manager launch setting or a proposed product workaround.
    """
    options = pinned_options(profile)
    if mode == "software":
        options["firefox_user_prefs"] = {
            **options.get("firefox_user_prefs", {}),
            "gfx.canvas.azure.backends": "skia",
            "gfx.canvas.accelerated": False,
        }
    elif mode in {"gdi-classic", "natural-symmetric"}:
        # Mozilla all.js documents 2=GDI Classic, 5=Natural Symmetric.
        # gfxDWriteFont::UpdateClearTypeVars validates these and updates the
        # native DirectWrite rendering parameters. Only CI probes use them.
        options["firefox_user_prefs"] = {
            **options.get("firefox_user_prefs", {}),
            "gfx.font_rendering.cleartype_params.rendering_mode": (
                2 if mode == "gdi-classic" else 5
            ),
        }
    elif mode == "gdi-family":
        # gfxDWriteFont::GetForceGDIClassic requires a listed family, size
        # threshold and rendering_mode=-1. ScaledFontDWrite then disables
        # subpixel positioning and Skia explicitly enters its ForceGDI path.
        options["firefox_user_prefs"] = {
            **options.get("firefox_user_prefs", {}),
            "gfx.font_rendering.cleartype_params.rendering_mode": -1,
            "gfx.font_rendering.cleartype_params.force_gdi_classic_for_families": "Arial",
            "gfx.font_rendering.cleartype_params.force_gdi_classic_max_size": 32,
        }
    elif mode != "default":
        raise ValueError(
            "Select default, software, gdi-classic, natural-symmetric or gdi-family raster mode"
        )
    return options


@pytest.mark.browser
@pytest.mark.asyncio
async def test_stable_canvas_survives_a_relaunch(tmp_path, local_sites):
    """The whole point of the setting: the canvas reads the same next session.

    The protection preference and pinned font list must keep text and pixels
    stable. Camoufox 0.5.8 no longer emits canvas or glyph-spacing noise seeds;
    retaining the complete equality check still verifies the page-visible result.
    """
    profile = Profile(
        name="stable-canvas",
        browser_settings=BrowserSettings(os="windows", stable_canvas=True),
    )
    options = profile.to_camoufox_launch_options()
    assert options["firefox_user_prefs"] == {"privacy.baselineFingerprintingProtection": False}

    profile.fingerprint = fingerprint_store.resolve(options)
    assert profile.fingerprint, "the pin carries the font list this relies on"

    mode = os.environ.get("CPM_CANVAS_RASTER_MODE", "default")
    diagnostics = []
    first = await read_canvas(
        canvas_probe_options(profile, mode),
        tmp_path / "a",
        local_sites.first,
        diagnostics=diagnostics,
    )
    second = await read_canvas(
        canvas_probe_options(profile, mode),
        tmp_path / "a",
        local_sites.first,
        diagnostics=diagnostics,
    )
    if os.environ.get("CPM_CANVAS_CACHE_PROBE") == "1":
        diagnostics[0]["phase"] = "initial"
        diagnostics[1]["phase"] = "restart"
        for phase, directory in (
            ("third-launch", tmp_path / "a"),
            ("fresh-directory", tmp_path / "fresh"),
        ):
            await read_canvas(
                canvas_probe_options(profile, mode),
                directory,
                local_sites.first,
                diagnostics=diagnostics,
            )
            diagnostics[-1]["phase"] = phase
        removed = remove_synthetic_canvas_caches(tmp_path / "a", tmp_path)
        await read_canvas(
            canvas_probe_options(profile, mode),
            tmp_path / "a",
            local_sites.first,
            diagnostics=diagnostics,
        )
        diagnostics[-1].update({"phase": "after-cache-removal", "removedCaches": removed})
        diagnostic_dir = os.environ.get("CPM_CANVAS_DIAGNOSTIC_DIR")
        if diagnostic_dir:
            for number, record in enumerate(diagnostics, 1):
                (Path(diagnostic_dir) / f"launch-{number}.json").write_text(
                    json.dumps(record, sort_keys=True, indent=2)
                )
    assert first == second, (
        f"canvas drifted between launches ({mode} raster): {first} vs {second}; "
        f"diagnostics={json.dumps(diagnostics, sort_keys=True)}"
    )


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

    # Asserted against the pin's own user agent rather than a fixed string: a
    # fifth of the bundled Linux presets say "X11; Ubuntu", and the coherence is
    # the property under test, not any particular token.
    expected = fingerprint_store._app_version_from_user_agent(pinned["navigator.userAgent"])

    assert "Linux" in platform, f"the pinned machine is Linux, the page saw {platform}"
    assert app_version == expected, (
        f"the pinned user agent implies {expected}, the page read {app_version} "
        f"beside platform {platform} — the setting overrode the pinned machine"
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
