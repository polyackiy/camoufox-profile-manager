"""The proxy check over HTTP, with the network stubbed.

The rules themselves are covered in tests/unit/test_proxy_check.py; these tests
are about the endpoints — that a saved profile is compared against its own
settings, that an unsaved one can be checked while it is still being typed, and
that a dead proxy is reported rather than raised.
"""

import pytest

from camoufox_pm.core import proxy_check

TOKYO = proxy_check.ProxyLocation(
    ip="203.0.113.7",
    country="JP",
    timezone="Asia/Tokyo",
    latitude=35.6895,
    longitude=139.6917,
)


@pytest.fixture
def exit_in_tokyo(monkeypatch):
    """Every check comes out in Tokyo, without touching the network."""

    async def resolve(proxy, timeout=proxy_check.DEFAULT_TIMEOUT):
        return TOKYO.ip, 42

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", resolve)
    monkeypatch.setattr(proxy_check, "locate", lambda ip: TOKYO)


@pytest.fixture
def dead_proxy(monkeypatch):
    async def resolve(proxy, timeout=proxy_check.DEFAULT_TIMEOUT):
        raise ConnectionError("The proxy refused the connection.")

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", resolve)


@pytest.mark.asyncio
async def test_a_saved_profile_is_compared_against_its_own_timezone(client, exit_in_tokyo):
    created = await client.post(
        "/api/profiles",
        json={"name": "berlin-on-tokyo", "browser_settings": {"timezone": "Europe/Berlin"}},
    )
    profile_id = created.json()["id"]

    checked = await client.post(f"/api/profiles/{profile_id}/check-proxy")

    assert checked.status_code == 200
    body = checked.json()
    assert body["reachable"] is True
    assert body["latency_ms"] == 42
    assert body["location"]["country"] == "JP"
    assert [f["level"] for f in body["findings"]] == ["warning"]
    assert "Europe/Berlin" in body["findings"][0]["message"]


@pytest.mark.asyncio
async def test_a_profile_that_agrees_with_its_proxy_reports_nothing(client, exit_in_tokyo):
    created = await client.post(
        "/api/profiles",
        json={"name": "tokyo", "browser_settings": {"timezone": "Asia/Tokyo"}},
    )

    checked = await client.post(f"/api/profiles/{created.json()['id']}/check-proxy")

    assert checked.json()["findings"] == []


@pytest.mark.asyncio
async def test_checking_a_missing_profile_is_404(client, exit_in_tokyo):
    assert (await client.post("/api/profiles/nope/check-proxy")).status_code == 404


@pytest.mark.asyncio
async def test_an_unsaved_proxy_can_be_checked(client, exit_in_tokyo):
    """The form needs an answer before the profile exists."""
    checked = await client.post(
        "/api/proxy/check",
        json={
            "proxy_config": {"type": "http", "server": "proxy.example.com:8080"},
            "browser_settings": {"timezone": "Europe/Berlin"},
        },
    )

    assert checked.status_code == 200
    assert [f["level"] for f in checked.json()["findings"]] == ["warning"]


@pytest.mark.asyncio
async def test_socks_credentials_are_reported_as_a_launch_failure(client, exit_in_tokyo):
    checked = await client.post(
        "/api/proxy/check",
        json={
            "proxy_config": {
                "type": "socks5",
                "server": "h:1080",
                "username": "u",
                "password": "p",
            },
            "browser_settings": {"timezone": "Asia/Tokyo"},
        },
    )

    assert [f["level"] for f in checked.json()["findings"]] == ["error"]


@pytest.mark.asyncio
async def test_a_dead_proxy_is_reported_not_raised(client, dead_proxy):
    checked = await client.post(
        "/api/proxy/check",
        json={"proxy_config": {"type": "http", "server": "127.0.0.1:9"}},
    )

    assert checked.status_code == 200
    body = checked.json()
    assert body["reachable"] is False
    assert body["error"] == "The proxy refused the connection."
    assert body["location"] is None


@pytest.mark.asyncio
async def test_an_invalid_proxy_is_422(client):
    checked = await client.post(
        "/api/proxy/check",
        json={"proxy_config": {"type": "carrier-pigeon", "server": "h:1"}},
    )

    assert checked.status_code == 422


@pytest.mark.asyncio
async def test_a_working_proxy_the_database_cannot_place(client, monkeypatch):
    """A proxy that answers is still worth reporting when it cannot be located."""

    async def resolve(proxy, timeout=proxy_check.DEFAULT_TIMEOUT):
        return "203.0.113.7", 11

    def unplaceable(ip):
        raise proxy_check.LocationUnavailable("no database")

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", resolve)
    monkeypatch.setattr(proxy_check, "locate", unplaceable)

    body = (await client.post("/api/proxy/check", json={})).json()

    assert body["reachable"] is True
    assert body["location"] == {
        "ip": "203.0.113.7",
        "country": None,
        "timezone": None,
        "latitude": None,
        "longitude": None,
    }
    assert [f["level"] for f in body["findings"]] == ["info"]
