"""What a page could notice between a profile and the address its proxy exits from.

compare() is pure, so every rule is tested here without a proxy or a network.
"""

import httpx
import pytest

from camoufox_pm.core import proxy_check
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
        # Provider-issued passwords routinely contain these. Interpolated raw,
        # the parser splits on the last "@" and both the host and the
        # credentials come out wrong.
        (
            ProxyConfig(type=ProxyType.HTTP, server="h:8080", username="u@x", password="p@ss:w/d"),
            "http://u%40x:p%40ss%3Aw%2Fd@h:8080",
        ),
    ],
)
def test_proxy_url_carries_credentials(proxy, expected):
    assert proxy_url(proxy) == expected


class FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]


class FakeClient:
    """Answers each endpoint in turn from a scripted list."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.asked.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


@pytest.fixture
def answers(monkeypatch):
    """Script the exit-address endpoints. Returns the client so it can be inspected."""
    holder = {}

    def install(*scripted):
        client = FakeClient(scripted)
        monkeypatch.setattr(proxy_check.httpx, "AsyncClient", lambda **kwargs: client)
        holder["client"] = client
        return client

    return install


@pytest.mark.asyncio
async def test_an_endpoint_that_answers_with_a_page_is_skipped(answers):
    """A captive portal or a rate-limit page can answer 200 with HTML.

    That string must never be taken for an address: it would end up in
    webrtc:ipv4 and be a far louder tell than the one this module exists to fix.
    """
    client = answers("<html>Sign in to continue</html>", "203.0.113.7")

    ip, _ = await proxy_check.resolve_exit_ip(None)

    assert ip == "203.0.113.7"
    assert len(client.asked) == 2, "expected the bad answer to fall through to the next endpoint"


@pytest.mark.asyncio
async def test_no_endpoint_gives_an_address(answers):
    answers("nonsense", "also nonsense", "still nonsense")

    with pytest.raises(ConnectionError):
        await proxy_check.resolve_exit_ip(None)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            httpx.HTTPStatusError("boom", request=None, response=httpx.Response(407)),  # type: ignore[arg-type]
            "rejected the credentials",
        ),
        # httpx reports a failed CONNECT as a ProxyError rather than a status, so
        # the commonest failure of all arrives without a status code on it.
        (httpx.ProxyError("407 Proxy Authentication Required"), "rejected the credentials"),
        (httpx.HTTPStatusError("boom", request=None, response=httpx.Response(502)), "answered 502"),
        (httpx.TimeoutException("slow"), "did not answer in time"),
        (httpx.UnsupportedProtocol("socks4h"), "SOCKS4 is not supported"),
    ],
)
@pytest.mark.asyncio
async def test_a_failure_is_reported_in_words_the_user_can_act_on(answers, failure, expected):
    """This string is the whole result of the check: it has to say what to fix."""
    answers(failure, failure, failure)

    with pytest.raises(ConnectionError, match=expected):
        await proxy_check.resolve_exit_ip(None)


@pytest.mark.asyncio
async def test_the_ipv6_answer_goes_into_the_ipv6_key(answers):
    """Camoufox routes a v6 exit address to webrtc:ipv6, and so must this."""
    answers("2001:db8::1")
    options = {"geoip": False, "config": {"timezone": "Asia/Tokyo"}}

    await proxy_check.fill_what_geoip_would_have(None, options)

    assert options["config"]["webrtc:ipv6"] == "2001:db8::1"
    assert "webrtc:ipv4" not in options["config"]
    assert "firefox_user_prefs" not in options


@pytest.mark.asyncio
async def test_a_spoofed_v4_candidate_brings_its_pref(answers):
    """Camoufox sets this alongside webrtc:ipv4 so a page cannot reach around the
    spoofed candidate over IPv6."""
    answers("203.0.113.7")
    options = {"geoip": False, "config": {"timezone": "Asia/Tokyo"}}

    await proxy_check.fill_what_geoip_would_have(None, options)

    assert options["config"]["webrtc:ipv4"] == "203.0.113.7"
    assert options["firefox_user_prefs"]["network.dns.disableIPv6"] is True


@pytest.mark.asyncio
async def test_the_pref_joins_any_others_already_set(answers):
    """stable_canvas puts its own pref here; filling must not replace it."""
    answers("203.0.113.7")
    options = {
        "geoip": False,
        "config": {"timezone": "Asia/Tokyo"},
        "firefox_user_prefs": {"privacy.baselineFingerprintingProtection": False},
    }

    await proxy_check.fill_what_geoip_would_have(None, options)

    assert options["firefox_user_prefs"] == {
        "privacy.baselineFingerprintingProtection": False,
        "network.dns.disableIPv6": True,
    }


@pytest.mark.asyncio
async def test_nothing_is_asked_when_geoip_will_do_it(answers):
    client = answers("203.0.113.7")

    await proxy_check.fill_what_geoip_would_have(None, {"geoip": True, "config": {}})

    assert client.asked == []


@pytest.mark.asyncio
async def test_a_blocked_webrtc_profile_is_left_alone(answers):
    """webrtc_mode "none" removes RTCPeerConnection; spoofing an address is moot."""
    answers("203.0.113.7")
    options = {"geoip": False, "block_webrtc": True, "config": {"timezone": "Asia/Tokyo"}}

    await proxy_check.fill_what_geoip_would_have(None, options)

    assert "webrtc:ipv4" not in options["config"]


@pytest.mark.asyncio
async def test_an_unreachable_proxy_does_not_block_the_launch(monkeypatch):
    async def refuse(proxy, timeout=proxy_check.DEFAULT_TIMEOUT):
        raise ConnectionError("nope")

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", refuse)
    options = {"geoip": False, "config": {}}

    await proxy_check.fill_what_geoip_would_have(None, options)

    assert options["config"] == {}
