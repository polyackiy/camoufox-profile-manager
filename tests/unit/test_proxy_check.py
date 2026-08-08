"""What a page could notice between a profile and the address its proxy exits from.

compare() is pure, so every rule is tested here without a proxy or a network.
"""

import pytest

from camoufox_pm.core.models import BrowserSettings, ProxyConfig, ProxyType
from camoufox_pm.core.proxy_check import ProxyLocation, compare, preflight, proxy_url

TOKYO = ProxyLocation(
    ip="203.0.113.7",
    country="JP",
    timezone="Asia/Tokyo",
    latitude=35.6895,
    longitude=139.6917,
)
BERLIN = ProxyLocation(
    ip="198.51.100.4",
    country="DE",
    timezone="Europe/Berlin",
    latitude=52.52,
    longitude=13.405,
)


def levels(findings, field):
    return [f.level for f in findings if f.field == field]


def test_a_timezone_on_the_other_side_of_the_world_is_a_warning():
    """The mismatch this exists to catch: a Berlin clock on a Japanese address."""
    findings = compare(BrowserSettings(timezone="Europe/Berlin"), TOKYO)

    assert levels(findings, "timezone") == ["warning"]
    message = next(f.message for f in findings if f.field == "timezone")
    assert "Europe/Berlin" in message and "Asia/Tokyo" in message and "JP" in message


def test_a_matching_timezone_says_nothing():
    assert compare(BrowserSettings(timezone="Asia/Tokyo"), TOKYO) == []


def test_a_different_zone_on_the_same_clock_is_only_a_note():
    """Berlin and Paris never disagree on the hour, so no page can see a contradiction.

    The zone name still names another country, which is worth mentioning but not
    worth flagging as a mismatch — over-warning trains people to ignore warnings.
    """
    findings = compare(BrowserSettings(timezone="Europe/Paris"), BERLIN)

    assert levels(findings, "timezone") == ["info"]


def test_no_timezone_means_it_follows_the_proxy():
    findings = compare(BrowserSettings(timezone=None), TOKYO)

    assert levels(findings, "timezone") == ["info"]
    assert "follows the proxy" in findings[0].message


def test_coordinates_without_a_timezone_still_follow_the_proxy():
    """Coordinates turn Camoufox's geoip off, which used to leave the timezone
    unset and let Firefox report the host machine's own zone. The launch path now
    fills it from the same exit address, so this is no longer a mismatch."""
    settings = BrowserSettings(timezone=None, geolocation={"lat": 35.68, "lon": 139.69})

    assert levels(compare(settings, TOKYO), "timezone") == ["info"]


def test_coordinates_far_from_the_exit_are_a_warning():
    settings = BrowserSettings(timezone="Asia/Tokyo", geolocation={"lat": 52.52, "lon": 13.405})

    findings = compare(settings, TOKYO)

    assert levels(findings, "geolocation") == ["warning"]
    assert "km from where the proxy exits" in findings[-1].message


def test_coordinates_in_the_next_city_are_only_a_note():
    """Osaka is 400 km from Tokyo: the same country, and within one story."""
    settings = BrowserSettings(timezone="Asia/Tokyo", geolocation={"lat": 34.69, "lon": 135.50})

    findings = compare(settings, TOKYO)

    assert levels(findings, "geolocation") == ["info"]


def test_coordinates_in_the_same_city_say_nothing_extra():
    settings = BrowserSettings(timezone="Asia/Tokyo", geolocation={"lat": 35.70, "lon": 139.70})

    assert compare(settings, TOKYO) == []


def test_an_unplaceable_exit_address_still_compares_what_it_can():
    """A working proxy the database cannot place must not produce false mismatches."""
    findings = compare(BrowserSettings(timezone="Europe/Berlin"), ProxyLocation(ip="203.0.113.7"))

    assert findings == []


def test_an_unknown_timezone_name_is_treated_as_a_contradiction():
    """A name zoneinfo cannot resolve cannot be shown to agree, so it does not get
    the benefit of the doubt."""
    findings = compare(BrowserSettings(timezone="Mars/Olympus"), TOKYO)

    assert levels(findings, "timezone") == ["warning"]


@pytest.mark.parametrize("kind", [ProxyType.SOCKS4, ProxyType.SOCKS5])
def test_socks_with_credentials_is_reported_before_any_request(kind):
    """Firefox refuses to authenticate to a SOCKS proxy, so the launch fails.

    Better to say so on the check button than to let the user find out when the
    browser will not start.
    """
    findings = preflight(ProxyConfig(type=kind, server="h:1080", username="u", password="p"))

    assert [f.level for f in findings] == ["error"]
    assert "fail to launch" in findings[0].message


def test_socks_without_credentials_is_fine():
    assert preflight(ProxyConfig(type=ProxyType.SOCKS5, server="h:1080")) == []


def test_http_with_credentials_is_fine():
    assert preflight(ProxyConfig(type=ProxyType.HTTP, server="h:8080", username="u")) == []


@pytest.mark.parametrize(
    ("proxy", "expected"),
    [
        (ProxyConfig(type=ProxyType.HTTP, server="h:8080"), "http://h:8080"),
        (
            ProxyConfig(type=ProxyType.SOCKS5, server="h:1080", username="u", password="p"),
            "socks5://u:p@h:1080",
        ),
        (
            ProxyConfig(type=ProxyType.HTTPS, server="h:443", username="u"),
            "https://u@h:443",
        ),
    ],
)
def test_proxy_url_carries_credentials(proxy, expected):
    assert proxy_url(proxy) == expected
