"""Regression tests: anti-detect settings must reach Camoufox.

Guards the historic bug where ``BrowserSettings.to_camoufox_config()`` returned
an empty dict, so geolocation/WebRTC/hardware settings never applied.
"""

from camoufox_pm.core.models import (
    BrowserSettings,
    Profile,
    ProxyConfig,
    ProxyType,
    WebRTCMode,
)


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


def test_local_ips_do_not_clobber_public_ip():
    """Regression: webrtc_local_ips used to overwrite the public ICE address."""
    bs = BrowserSettings(
        webrtc_public_ip="203.0.113.7",
        webrtc_local_ips=["192.168.1.5"],
    )
    cfg = bs.to_camoufox_config()
    assert cfg["webrtc:ipv4"] == "203.0.113.7"


def test_webrtc_mode_none_blocks_webrtc():
    p = Profile(name="t", browser_settings=BrowserSettings(webrtc_mode=WebRTCMode.NONE))
    assert p.to_camoufox_launch_options()["block_webrtc"] is True


def test_webrtc_mode_replace_does_not_block():
    p = Profile(name="t", browser_settings=BrowserSettings(webrtc_mode=WebRTCMode.REPLACE))
    assert "block_webrtc" not in p.to_camoufox_launch_options()


def test_stable_canvas_sets_the_pref_and_is_off_by_default():
    """Off by default: the default keeps cross-site unlinkability."""
    default = Profile(name="d").to_camoufox_launch_options()
    assert "firefox_user_prefs" not in default

    stable = Profile(
        name="s", browser_settings=BrowserSettings(stable_canvas=True)
    ).to_camoufox_launch_options()
    assert stable["firefox_user_prefs"] == {"privacy.baselineFingerprintingProtection": False}


def test_window_tuple_from_width_height():
    p = Profile(
        name="t",
        browser_settings=BrowserSettings(window_width=1024, window_height=768),
    )
    assert p.to_camoufox_launch_options()["window"] == (1024, 768)


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


def test_clearing_geography_hands_the_location_back_to_the_proxy():
    """The point of clearing is the geoip flip, not the tidier record.

    Coordinates force ``geoip=False``, and that same branch is what Camoufox uses
    to fill the timezone and the WebRTC address from the exit address. A profile
    carrying a randomly chosen region therefore derives none of the three.
    """
    settings = BrowserSettings(timezone="Asia/Shanghai", geolocation={"lat": 31.2, "lon": 121.5})
    assert settings.has_geography() is True
    before = Profile(name="old", browser_settings=settings).to_camoufox_launch_options()
    assert before["geoip"] is False
    assert before["config"]["timezone"] == "Asia/Shanghai"

    assert settings.clear_geography() is True
    after = Profile(name="old", browser_settings=settings).to_camoufox_launch_options()
    assert after["geoip"] is True
    assert "timezone" not in after["config"]
    assert "geolocation:latitude" not in after["config"]


def test_clearing_geography_leaves_the_language_alone():
    """Languages are identity, not geography; Camoufox applies them either way."""
    settings = BrowserSettings(
        timezone="Asia/Shanghai", languages=["zh-CN", "zh"], locale="zh_CN", os="macos"
    )
    settings.clear_geography()
    assert settings.languages == ["zh-CN", "zh"]
    assert settings.locale == "zh_CN"
    assert settings.os == "macos"


def test_clearing_geography_reports_when_there_was_none():
    settings = BrowserSettings()
    assert settings.has_geography() is False
    assert settings.clear_geography() is False


def test_launch_options_include_proxy():
    p = Profile(
        name="t",
        proxy=ProxyConfig(type=ProxyType.HTTP, server="1.2.3.4:8080", username="u", password="p"),
    )
    opts = p.to_camoufox_launch_options()
    assert opts["proxy"]["server"] == "http://1.2.3.4:8080"
    assert opts["proxy"]["username"] == "u"
