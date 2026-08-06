"""Regression tests: anti-detect settings must reach Camoufox.

Guards the historic bug where ``BrowserSettings.to_camoufox_config()`` returned
an empty dict, so geolocation/WebRTC/hardware settings never applied.
"""

from camoufox_pm.core.models import BrowserSettings, Profile, ProxyConfig, ProxyType


def test_config_carries_geolocation_and_hardware():
    bs = BrowserSettings(
        os="windows",
        geolocation={"lat": 40.0, "lon": -74.0, "accuracy": 50.0},
        hardware_concurrency=8,
    )
    cfg = bs.to_camoufox_config()
    assert cfg["geolocation:latitude"] == 40.0
    assert cfg["geolocation:longitude"] == -74.0
    assert cfg["geolocation:accuracy"] == 50.0
    assert cfg["navigator.hardwareConcurrency"] == 8


def test_config_carries_webrtc_public_ip():
    bs = BrowserSettings(os="linux", webrtc_public_ip="203.0.113.7")
    cfg = bs.to_camoufox_config()
    assert cfg["webrtc:ipv4"] == "203.0.113.7"


def test_config_is_empty_when_nothing_set():
    bs = BrowserSettings(os="macos")
    assert bs.to_camoufox_config() == {}


def test_launch_options_pass_high_level_params_and_config():
    p = Profile(name="t", browser_settings=BrowserSettings(os="macos", languages=["en-US", "en"]))
    opts = p.to_camoufox_launch_options()
    assert opts["os"] == "macos"
    assert opts["humanize"] is True
    assert opts["persistent_context"] is True
    assert isinstance(opts["config"], dict)
    # Camoufox owns the fingerprint: we must not inject a manual user agent.
    assert "user_agent" not in opts


def test_launch_options_geoip_auto_unless_explicit_coords():
    without = Profile(name="a").to_camoufox_launch_options()
    assert without["geoip"] is True
    with_coords = Profile(
        name="b", browser_settings=BrowserSettings(geolocation={"lat": 1.0, "lon": 2.0})
    ).to_camoufox_launch_options()
    assert with_coords["geoip"] is False


def test_launch_options_include_proxy():
    p = Profile(
        name="t",
        proxy=ProxyConfig(type=ProxyType.HTTP, server="1.2.3.4:8080", username="u", password="p"),
    )
    opts = p.to_camoufox_launch_options()
    assert opts["proxy"]["server"] == "http://1.2.3.4:8080"
    assert opts["proxy"]["username"] == "u"
