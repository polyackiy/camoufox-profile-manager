"""The proxy check over HTTP, with the network stubbed.

The rules themselves are covered in tests/unit/test_proxy_check.py; these tests
are about the endpoints — that a saved profile is compared against its own
settings, that an unsaved one can be checked while it is still being typed, and
that a dead proxy is reported rather than raised.
"""

import asyncio

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


@pytest.mark.asyncio
async def test_a_check_is_kept_on_the_profile(client, exit_in_tokyo):
    """The list shows a proxy's last answer, so the answer has to outlive the request."""
    created = await client.post(
        "/api/profiles",
        json={"name": "kept", "browser_settings": {"timezone": "Asia/Tokyo"}},
    )
    profile_id = created.json()["id"]
    assert created.json()["proxy_check"] is None, "a new profile has never been checked"

    checked = await client.post(f"/api/profiles/{profile_id}/check-proxy")
    assert checked.json()["checked_at"] is not None

    stored = (await client.get(f"/api/profiles/{profile_id}")).json()["proxy_check"]
    assert stored["reachable"] is True
    assert stored["ip"] == "203.0.113.7"
    assert stored["country"] == "JP"
    assert stored["latency_ms"] == 42
    assert stored["checked_at"] == checked.json()["checked_at"]


@pytest.mark.asyncio
async def test_a_dead_proxy_is_kept_too(client, dead_proxy):
    """Red is a result. A row that forgets it looks unchecked, which is worse."""
    created = await client.post("/api/profiles", json={"name": "dead"})
    profile_id = created.json()["id"]

    await client.post(f"/api/profiles/{profile_id}/check-proxy")

    stored = (await client.get(f"/api/profiles/{profile_id}")).json()["proxy_check"]
    assert stored["reachable"] is False
    assert stored["error"]
    assert stored["ip"] is None


@pytest.mark.asyncio
async def test_changing_the_proxy_drops_the_stored_check(client, exit_in_tokyo):
    """An answer from the proxy that was there says nothing about the one now."""
    created = await client.post(
        "/api/profiles",
        json={
            "name": "moved",
            "proxy_config": {"type": "http", "server": "first.example.com:8080"},
        },
    )
    profile_id = created.json()["id"]
    await client.post(f"/api/profiles/{profile_id}/check-proxy")
    assert (await client.get(f"/api/profiles/{profile_id}")).json()["proxy_check"] is not None

    await client.put(
        f"/api/profiles/{profile_id}",
        json={"proxy_config": {"type": "http", "server": "second.example.com:8080"}},
    )

    assert (await client.get(f"/api/profiles/{profile_id}")).json()["proxy_check"] is None


@pytest.mark.asyncio
async def test_removing_the_proxy_drops_the_stored_check(client, exit_in_tokyo):
    """No proxy is a different exit address, not the absence of a question."""
    created = await client.post(
        "/api/profiles",
        json={
            "name": "unproxied",
            "proxy_config": {"type": "http", "server": "first.example.com:8080"},
        },
    )
    profile_id = created.json()["id"]
    await client.post(f"/api/profiles/{profile_id}/check-proxy")

    await client.put(f"/api/profiles/{profile_id}", json={"proxy_config": None})

    assert (await client.get(f"/api/profiles/{profile_id}")).json()["proxy_check"] is None


@pytest.mark.asyncio
async def test_an_unrelated_edit_keeps_the_stored_check(client, exit_in_tokyo):
    """Renaming a profile does not make its proxy's answer stale."""
    created = await client.post(
        "/api/profiles",
        json={"name": "before", "proxy_config": {"type": "http", "server": "p.example.com:8080"}},
    )
    profile_id = created.json()["id"]
    await client.post(f"/api/profiles/{profile_id}/check-proxy")

    await client.put(f"/api/profiles/{profile_id}", json={"name": "after"})

    assert (await client.get(f"/api/profiles/{profile_id}")).json()["proxy_check"] is not None


@pytest.mark.asyncio
async def test_a_clone_starts_unchecked(client, exit_in_tokyo):
    """The copy shares the proxy, but it is not the profile that was checked."""
    created = await client.post(
        "/api/profiles",
        json={"name": "source", "proxy_config": {"type": "http", "server": "p.example.com:8080"}},
    )
    source_id = created.json()["id"]
    await client.post(f"/api/profiles/{source_id}/check-proxy")

    clone = await client.post(f"/api/profiles/{source_id}/clone", json={"new_name": "copy"})

    assert clone.json()["proxy_check"] is None


@pytest.fixture
def slow_exit(monkeypatch):
    """A check that takes long enough for the user to edit the profile under it."""

    async def resolve(proxy, timeout=proxy_check.DEFAULT_TIMEOUT):
        await asyncio.sleep(0.3)
        return TOKYO.ip, 42

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", resolve)
    monkeypatch.setattr(proxy_check, "locate", lambda ip: TOKYO)


@pytest.mark.asyncio
async def test_an_edit_during_a_check_is_not_reverted(client, slow_exit):
    """A check can take thirty seconds against a proxy that never answers.

    Writing back the profile it read before that wait would undo whatever the
    user did in the meantime — which is not hypothetical: the natural flow is a
    bulk check, a red row, and a rename or a new proxy while the rest still runs.
    """
    created = await client.post(
        "/api/profiles",
        json={"name": "before", "proxy_config": {"type": "http", "server": "p.example.com:8080"}},
    )
    profile_id = created.json()["id"]

    checking = asyncio.create_task(client.post(f"/api/profiles/{profile_id}/check-proxy"))
    await asyncio.sleep(0.1)
    await client.put(f"/api/profiles/{profile_id}", json={"name": "renamed", "notes": "typed"})
    await checking

    profile = (await client.get(f"/api/profiles/{profile_id}")).json()
    assert profile["name"] == "renamed", "the rename was reverted by the check"
    assert profile["notes"] == "typed"
    assert profile["proxy_check"] is not None, "the check still belongs to this proxy"


@pytest.mark.asyncio
async def test_a_proxy_changed_during_a_check_keeps_no_answer(client, slow_exit):
    """The answer describes the proxy that was there when the question was asked."""
    created = await client.post(
        "/api/profiles",
        json={"name": "moved", "proxy_config": {"type": "http", "server": "old.example.com:8080"}},
    )
    profile_id = created.json()["id"]

    checking = asyncio.create_task(client.post(f"/api/profiles/{profile_id}/check-proxy"))
    await asyncio.sleep(0.1)
    await client.put(
        f"/api/profiles/{profile_id}",
        json={"proxy_config": {"type": "http", "server": "new.example.com:8080"}},
    )
    answer = await checking

    profile = (await client.get(f"/api/profiles/{profile_id}")).json()
    assert profile["proxy_config"]["server"] == "new.example.com:8080", "the edit was reverted"
    assert profile["proxy_check"] is None, "an answer about the old proxy was kept"
    # The caller still gets its answer; it is simply recorded nowhere.
    assert answer.json()["reachable"] is True
    assert answer.json()["checked_at"] is None


@pytest.mark.asyncio
async def test_a_check_does_not_count_as_an_edit(client, exit_in_tokyo):
    """A bulk check must not make a whole selection look modified."""
    created = await client.post("/api/profiles", json={"name": "untouched"})
    profile_id = created.json()["id"]
    before = created.json()["updated_at"]

    await client.post(f"/api/profiles/{profile_id}/check-proxy")

    assert (await client.get(f"/api/profiles/{profile_id}")).json()["updated_at"] == before


@pytest.mark.asyncio
async def test_an_imported_profile_starts_unchecked(client, exit_in_tokyo):
    """An archive can carry an answer given on another machine, on another network."""
    created = await client.post(
        "/api/profiles",
        json={"name": "source", "proxy_config": {"type": "http", "server": "p.example.com:8080"}},
    )
    source_id = created.json()["id"]
    await client.post(f"/api/profiles/{source_id}/check-proxy")

    exported = await client.get(f"/api/profiles/{source_id}/export")
    imported = await client.post(
        "/api/profiles/import",
        files={"file": ("p.zip", exported.content, "application/zip")},
    )

    assert imported.status_code == 201, imported.text
    assert imported.json()["proxy_check"] is None


@pytest.mark.asyncio
async def test_an_unreadable_stored_check_costs_a_dot_not_the_list(client, exit_in_tokyo):
    """The column is cosmetic; it must not be able to take the profiles screen down."""
    from camoufox_pm.api.dependencies import get_profile_manager

    created = await client.post("/api/profiles", json={"name": "corrupt"})
    profile_id = created.json()["id"]
    await client.post(f"/api/profiles/{profile_id}/check-proxy")

    connection = get_profile_manager().storage.db._connection
    connection.execute(
        "UPDATE profiles SET proxy_check = ? WHERE id = ?", ("{not json", profile_id)
    )
    connection.commit()

    listed = await client.get("/api/profiles")
    assert listed.status_code == 200
    assert any(row["id"] == profile_id for row in listed.json()["profiles"])
    assert (await client.get(f"/api/profiles/{profile_id}")).json()["proxy_check"] is None
