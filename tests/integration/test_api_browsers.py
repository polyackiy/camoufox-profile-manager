"""Integration tests for the browser-control endpoints.

No browser is started: a stand-in session is registered so the endpoints that
report and close them can be checked without a Camoufox binary.
"""

import pytest

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.core.browser_session import BrowserSession


class FakeCamoufox:
    """Stands in for a running AsyncCamoufox and records that it was closed."""

    def __init__(self):
        self.exits = 0

    async def __aexit__(self, *_exc):
        self.exits += 1


def register(profile_id: str) -> FakeCamoufox:
    """Register a running browser for a profile."""
    camoufox = FakeCamoufox()
    get_profile_manager().browser_sessions.active_sessions[profile_id] = BrowserSession(
        profile_id, camoufox=camoufox, process_id=4242
    )
    return camoufox


@pytest.mark.asyncio
async def test_no_browsers_are_reported_when_none_are_running(client):
    body = (await client.get("/api/browsers/active")).json()

    assert body == {"active_browsers": [], "count": 0}


@pytest.mark.asyncio
async def test_a_running_browser_is_listed_with_its_process(client):
    register("p1")

    body = (await client.get("/api/browsers/active")).json()

    assert body["count"] == 1
    assert body["active_browsers"][0]["profile_id"] == "p1"
    assert body["active_browsers"][0]["process_id"] == 4242
    assert body["active_browsers"][0]["started_at"]


@pytest.mark.asyncio
async def test_closing_one_browser_leaves_the_others_running(client):
    first = register("p1")
    second = register("p2")

    result = (await client.post("/api/profiles/p1/close")).json()

    assert result["status"] == "closed"
    assert first.exits == 1
    assert second.exits == 0
    assert (await client.get("/api/browsers/active")).json()["count"] == 1


@pytest.mark.asyncio
async def test_closing_a_browser_that_is_not_running_is_not_an_error(client):
    """The window may already have been closed by hand; saying so beats a 500."""
    response = await client.post("/api/profiles/nope/close")

    assert response.status_code == 200
    assert response.json()["status"] == "not_running"


@pytest.mark.asyncio
async def test_close_all_closes_every_browser_and_counts_them(client):
    browsers = [register("p1"), register("p2"), register("p3")]

    body = (await client.post("/api/browsers/close-all")).json()

    assert body["closed_count"] == 3
    assert [b.exits for b in browsers] == [1, 1, 1]
    assert (await client.get("/api/browsers/active")).json()["count"] == 0


@pytest.mark.asyncio
async def test_launching_an_unknown_profile_is_a_404(client):
    response = await client.post("/api/profiles/nope/launch", json={"headless": True})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_clone_is_created_as_a_new_profile(client):
    created = (await client.post("/api/profiles", json={"name": "source"})).json()

    cloned = await client.post(f"/api/profiles/{created['id']}/clone", json={"new_name": "copy"})

    assert cloned.status_code == 201
    assert cloned.json()["name"] == "copy"
    assert cloned.json()["id"] != created["id"]
    assert (await client.get(f"/api/profiles/{created['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_cloning_an_unknown_profile_is_a_404(client):
    response = await client.post("/api/profiles/nope/clone", json={"new_name": "copy"})

    assert response.status_code == 404
