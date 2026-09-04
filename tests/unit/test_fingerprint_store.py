"""What gets frozen into a profile's identity, and what must stay dynamic."""

import pytest

from camoufox_pm.core import fingerprint_store

RESOLVED = {
    # Hardware — must be frozen so the profile is the same machine every time.
    "navigator.userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0)",
    "navigator.platform": "Win32",
    "navigator.hardwareConcurrency": 8,
    "navigator.oscpu": "Windows NT 10.0; Win64; x64",
    "screen.width": 1920,
    "screen.height": 1080,
    "window.outerWidth": 1920,
    "webGl:vendor": "Google Inc. (NVIDIA)",
    "webGl:renderer": "ANGLE (NVIDIA, GeForce GTX 980)",
    "webGl2:parameters": {"2849": 1},
    "canvas:seed": 2688239157,
    "audio:seed": 534371864,
    "fonts": ["Arial", "Calibri"],
    "fonts:spacing_seed": 2742678354,
    "mediaDevices:micros": 1,
    "voices": [{"name": "Microsoft David"}],
    # Location and locale — must follow the proxy and the profile's settings.
    "geolocation:latitude": 52.52,
    "geolocation:longitude": 13.405,
    "timezone": "Europe/Berlin",
    "locale:language": "de",
    "locale:region": "DE",
    "webrtc:ipv4": "203.0.113.7",
    "navigator.language": "de-DE",
    "navigator.languages": ["de-DE", "de"],
    "headers.Accept-Encoding": "gzip",
    # Session state and machine-local paths.
    "window.history.length": 3,
    "addons": ["/Users/someone/Library/Caches/camoufox/addons/UBO"],
}


def test_hardware_is_frozen():
    frozen = fingerprint_store.freeze(RESOLVED)
    for key in (
        "navigator.userAgent",
        "navigator.hardwareConcurrency",
        "screen.width",
        "webGl:renderer",
        "webGl2:parameters",
        "canvas:seed",
        "audio:seed",
        "fonts",
        "fonts:spacing_seed",
        "mediaDevices:micros",
        "voices",
    ):
        assert key in frozen, f"{key} identifies the machine and must be pinned"


def test_location_and_locale_stay_dynamic():
    """Freezing these would pin a Berlin timezone onto a Tokyo proxy."""
    frozen = fingerprint_store.freeze(RESOLVED)
    for key in (
        "geolocation:latitude",
        "geolocation:longitude",
        "timezone",
        "locale:language",
        "locale:region",
        "webrtc:ipv4",
        "navigator.language",
        "navigator.languages",
        "headers.Accept-Encoding",
    ):
        assert key not in frozen, f"{key} must keep following the proxy or the profile"


def test_session_state_and_local_paths_are_not_frozen():
    frozen = fingerprint_store.freeze(RESOLVED)
    assert "window.history.length" not in frozen, "history grows; it is not hardware"
    assert "addons" not in frozen, "addon paths belong to the machine that generated them"


def test_freeze_is_stable_and_lossless_for_hardware():
    once = fingerprint_store.freeze(RESOLVED)
    twice = fingerprint_store.freeze(RESOLVED)
    assert once == twice
    assert once["navigator.hardwareConcurrency"] == 8
    assert once["fonts"] == ["Arial", "Calibri"]


def test_summarize_reports_the_values_a_user_recognises():
    summary = fingerprint_store.summarize(fingerprint_store.freeze(RESOLVED))
    assert summary is not None
    assert summary["screen"] == "1920x1080"
    assert summary["hardware_concurrency"] == 8
    assert summary["gpu"] == "ANGLE (NVIDIA, GeForce GTX 980)"
    assert summary["font_count"] == 2
    assert summary["property_count"] == len(fingerprint_store.freeze(RESOLVED))


def test_summarize_without_a_pin():
    assert fingerprint_store.summarize(None) is None
    assert fingerprint_store.summarize({}) is None


UA_OLD = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0"
UA_NEW = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"


def test_browser_major_reads_the_version_from_the_user_agent():
    assert fingerprint_store.browser_major({"navigator.userAgent": UA_OLD}) == 149
    assert fingerprint_store.browser_major({"navigator.userAgent": "nonsense"}) is None
    assert fingerprint_store.browser_major({}) is None
    assert fingerprint_store.browser_major(None) is None


def test_is_outdated_compares_against_the_installed_browser(monkeypatch):
    monkeypatch.setattr(fingerprint_store, "installed_major", lambda: 152)
    assert fingerprint_store.is_outdated({"navigator.userAgent": UA_OLD}) is True
    assert fingerprint_store.is_outdated({"navigator.userAgent": UA_NEW}) is False
    # Never claim staleness without both numbers.
    assert fingerprint_store.is_outdated(None) is False
    monkeypatch.setattr(fingerprint_store, "installed_major", lambda: None)
    assert fingerprint_store.is_outdated({"navigator.userAgent": UA_OLD}) is False


def test_refresh_replaces_the_browser_but_keeps_the_machine():
    """The whole point: same computer, newer browser."""
    pinned = {
        "navigator.userAgent": UA_OLD,
        "navigator.hardwareConcurrency": 12,
        "navigator.platform": "Win32",
        "navigator.oscpu": "Windows NT 10.0; Win64; x64",
        "screen.width": 2560,
        "screen.height": 1440,
        "webGl:renderer": "ANGLE (NVIDIA, GeForce GTX 980)",
        "webGl:vendor": "Google Inc. (NVIDIA)",
        "canvas:seed": 424242,
        "audio:seed": 111,
        "fonts": ["Arial", "Calibri"],
        "fonts:spacing_seed": 777,
    }
    # A newly resolved fingerprint describes a *different* machine entirely.
    resolved = {
        "navigator.userAgent": UA_NEW,
        "navigator.hardwareConcurrency": 4,
        "screen.width": 1366,
        "screen.height": 768,
        "webGl:renderer": "ANGLE (Intel, Intel(R) HD Graphics)",
        "canvas:seed": 999999,
        "fonts": ["Segoe UI"],
        "fonts:spacing_seed": 5,
    }

    updated = fingerprint_store.refresh_browser_version(pinned, resolved)

    assert updated["navigator.userAgent"] == UA_NEW, "the browser must move forward"
    # Everything that makes it this device must be untouched — especially the
    # seeds, since changing one would alter the canvas the profile presents.
    for key in (
        "navigator.hardwareConcurrency",
        "navigator.platform",
        "navigator.oscpu",
        "screen.width",
        "screen.height",
        "webGl:renderer",
        "webGl:vendor",
        "canvas:seed",
        "audio:seed",
        "fonts",
        "fonts:spacing_seed",
    ):
        assert updated[key] == pinned[key], f"{key} is the machine and must survive"


def test_refresh_does_not_mutate_the_stored_pin():
    pinned = {"navigator.userAgent": UA_OLD, "screen.width": 2560}
    fingerprint_store.refresh_browser_version(pinned, {"navigator.userAgent": UA_NEW})
    assert pinned["navigator.userAgent"] == UA_OLD


def test_refresh_drops_a_version_key_the_new_build_stopped_emitting():
    """A stale buildID would be worse than none at all."""
    pinned = {"navigator.userAgent": UA_OLD, "navigator.buildID": "20240101000000"}
    updated = fingerprint_store.refresh_browser_version(pinned, {"navigator.userAgent": UA_NEW})
    assert "navigator.buildID" not in updated


def test_summary_reports_the_version_and_whether_it_is_behind(monkeypatch):
    monkeypatch.setattr(fingerprint_store, "installed_major", lambda: 152)
    summary = fingerprint_store.summarize({"navigator.userAgent": UA_OLD, "screen.width": 800})
    assert summary["browser_major"] == 149
    assert summary["installed_major"] == 152
    assert summary["browser_outdated"] is True


def test_pinned_os_is_read_from_the_pin_not_the_settings():
    """A refresh must resolve for the OS the pin describes.

    Regression: refreshing a Windows pin on a profile whose settings had drifted
    to macOS produced a macOS user agent on Windows hardware — a Mac browser
    reporting Win32 and an ANGLE Direct3D11 GPU.
    """
    assert fingerprint_store.pinned_os({"navigator.platform": "Win32"}) == "windows"
    assert fingerprint_store.pinned_os({"navigator.platform": "MacIntel"}) == "macos"
    assert fingerprint_store.pinned_os({"navigator.platform": "Linux x86_64"}) == "linux"
    # Falls back to oscpu when the platform string is unfamiliar.
    assert (
        fingerprint_store.pinned_os(
            {"navigator.platform": "Weird", "navigator.oscpu": "Windows NT 10.0; Win64; x64"}
        )
        == "windows"
    )
    assert fingerprint_store.pinned_os({}) is None
    assert fingerprint_store.pinned_os(None) is None


def test_summary_reports_a_setting_that_disagrees_with_the_pin():
    """The dropdown can be changed long after the pin was made, and silently.

    While a pin exists it is what a page sees, so a profile set to macOS on
    Windows hardware behaves exactly as before — the setting simply stops meaning
    anything, with nothing to say so.
    """
    pin = {"navigator.platform": "Win32", "navigator.userAgent": UA_NEW}

    agreeing = fingerprint_store.summarize(pin, "windows")
    assert agreeing["pinned_os"] == "windows"
    assert agreeing["settings_os"] == "windows"
    assert agreeing["os_mismatch"] is False

    disagreeing = fingerprint_store.summarize(pin, "macos")
    assert disagreeing["pinned_os"] == "windows"
    assert disagreeing["settings_os"] == "macos"
    assert disagreeing["os_mismatch"] is True


def test_summary_claims_no_mismatch_when_either_side_is_unknown():
    """Half an answer must not be reported as a contradiction."""
    assert fingerprint_store.summarize({"screen.width": 800}, "macos")["os_mismatch"] is False
    assert (
        fingerprint_store.summarize({"navigator.platform": "Win32"}, None)["os_mismatch"] is False
    )


def test_get_preset_rejects_malformed_ids():
    """Only a plain run of ASCII digits names a preset.

    int() accepts far more than that, and two of these forms used to resolve to a
    real preset the id does not read as: "-1" indexed from the end, and "1_0"
    (underscore as a digit separator) resolved to index 10 — a different device.
    """
    for bad in (
        "windows:-1",
        "windows:+1",
        "windows:1_0",
        "windows: 1",
        "windows:1\n",
        "windows:١",  # Arabic-Indic digit one: str.isdigit() alone accepts it
        "windows:",
        "windows:notanumber",
        "nosuchos:0",
    ):
        assert fingerprint_store.get_preset(bad) is None, f"{bad!r} must not resolve"


def test_get_preset_still_resolves_a_real_id():
    """The guard must not have made every id unresolvable."""
    presets = fingerprint_store.list_presets("windows")
    if not presets:
        pytest.skip("no bundled presets available")
    assert fingerprint_store.get_preset(presets[1]["id"]) is not None


class TestAppVersion:
    """A pin must not let the host's operating system show through.

    Camoufox's generated fingerprints carry navigator.appVersion; the ones built
    from a device preset do not, and Firefox then falls back to the host's own
    value. Measured before this fill, on a macOS host with a Linux preset:
    platform "Linux x86_64" beside appVersion "5.0 (Macintosh)" — two properties
    any page can read together. Reported upstream as daijro/camoufox#753.
    """

    def test_each_platform_gets_the_token_firefox_reports(self):
        cases = {
            "Linux x86_64": "5.0 (X11)",
            "Win32": "5.0 (Windows)",
            "MacIntel": "5.0 (Macintosh)",
        }
        for platform, expected in cases.items():
            pin = {"navigator.platform": platform}
            fingerprint_store._fill_app_version(pin)
            assert pin["navigator.appVersion"] == expected

    def test_a_value_already_there_is_kept(self):
        """A resolved fingerprint that carries one knows better than this."""
        pin = {"navigator.platform": "Win32", "navigator.appVersion": "5.0 (Windows NT 10.0)"}

        fingerprint_store._fill_app_version(pin)

        assert pin["navigator.appVersion"] == "5.0 (Windows NT 10.0)"

    def test_a_platform_firefox_does_not_run_on_is_left_alone(self):
        """Inventing a token would be worse than leaving the gap visible."""
        pin = {"navigator.platform": "iPhone"}

        fingerprint_store._fill_app_version(pin)

        assert "navigator.appVersion" not in pin

    def test_a_pin_without_a_platform_is_left_alone(self):
        pin: dict = {}

        fingerprint_store._fill_app_version(pin)

        assert pin == {}
