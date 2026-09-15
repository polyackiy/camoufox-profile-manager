"""Two clients editing one profile over the API: the loser is told, not ignored."""

import pytest


async def _create(client, name: str = "shared") -> dict:
    return (await client.post("/api/profiles", json={"name": name})).json()


@pytest.mark.asyncio
async def test_a_profile_reports_the_version_it_is_at(client):
    created = await _create(client)

    assert created["row_version"] == 0
    fetched = (await client.get(f"/api/profiles/{created['id']}")).json()
    assert fetched["row_version"] == 0


@pytest.mark.asyncio
async def test_saving_with_the_current_version_succeeds_and_moves_it_on(client):
    created = await _create(client)

    response = await client.put(
        f"/api/profiles/{created['id']}",
        json={"name": "renamed", "row_version": created["row_version"]},
    )

    assert response.status_code == 200
    assert response.json()["row_version"] == created["row_version"] + 1


@pytest.mark.asyncio
async def test_the_second_client_to_save_gets_a_conflict(client):
    """The lost update, over HTTP: both load the form, both press Save."""
    created = await _create(client)
    first = (await client.get(f"/api/profiles/{created['id']}")).json()
    second = (await client.get(f"/api/profiles/{created['id']}")).json()

    ok = await client.put(
        f"/api/profiles/{created['id']}",
        json={"name": "renamed by the first client", "row_version": first["row_version"]},
    )
    conflict = await client.put(
        f"/api/profiles/{created['id']}",
        json={"notes": "written by the second client", "row_version": second["row_version"]},
    )

    assert ok.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "stale_write"

    # The first client's change is intact: nothing was half-applied.
    stored = (await client.get(f"/api/profiles/{created['id']}")).json()
    assert stored["name"] == "renamed by the first client"
    assert stored["notes"] is None


@pytest.mark.asyncio
async def test_the_refused_client_can_reload_and_save(client):
    """A conflict is recoverable without losing the edit: reload, reapply, save."""
    created = await _create(client)
    stale_version = created["row_version"]
    await client.put(f"/api/profiles/{created['id']}", json={"name": "moved on"})

    refused = await client.put(
        f"/api/profiles/{created['id']}", json={"notes": "mine", "row_version": stale_version}
    )
    assert refused.status_code == 409

    current = (await client.get(f"/api/profiles/{created['id']}")).json()
    retried = await client.put(
        f"/api/profiles/{created['id']}",
        json={"notes": "mine", "row_version": current["row_version"]},
    )

    assert retried.status_code == 200
    assert retried.json()["notes"] == "mine"
    assert retried.json()["name"] == "moved on"


@pytest.mark.asyncio
async def test_a_client_that_sends_no_version_is_still_served(client):
    """0.4.x clients predate this field and must keep working."""
    created = await _create(client)
    await client.put(f"/api/profiles/{created['id']}", json={"name": "changed elsewhere"})

    response = await client.put(f"/api/profiles/{created['id']}", json={"notes": "no version"})

    assert response.status_code == 200
    assert response.json()["notes"] == "no version"


@pytest.mark.asyncio
async def test_a_conflict_is_not_reported_as_a_server_fault(client):
    """409 and a stable code, so a client can act on it rather than show a stack."""
    created = await _create(client)
    await client.put(f"/api/profiles/{created['id']}", json={"name": "moved on"})

    response = await client.put(
        f"/api/profiles/{created['id']}", json={"notes": "stale", "row_version": 0}
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "stale_write"
    assert "changed by someone else" in body["error"]["message"]
    # detail mirrors error.message for clients on FastAPI's default shape.
    assert body["detail"] == body["error"]["message"]
