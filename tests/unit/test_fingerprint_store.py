"""What gets frozen into a profile's identity, and what must stay dynamic."""

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
