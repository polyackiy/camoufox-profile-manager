"""Integration tests for the groups API."""

import pytest


@pytest.mark.asyncio
async def test_create_and_list_group(client):
    created = await client.post("/api/groups", json={"name": "Social"})
    assert created.status_code == 201
    group_id = created.json()["id"]

    listed = await client.get("/api/groups")
    assert listed.status_code == 200
    assert any(g["id"] == group_id for g in listed.json()["groups"])


@pytest.mark.asyncio
async def test_group_profile_count(client):
    group = (await client.post("/api/groups", json={"name": "Work"})).json()
    await client.post("/api/profiles", json={"name": "w1", "group": group["id"]})
    await client.post("/api/profiles", json={"name": "w2", "group": group["id"]})

    fetched = await client.get(f"/api/groups/{group['id']}")
    assert fetched.json()["profile_count"] == 2


@pytest.mark.asyncio
async def test_delete_group(client):
    group = (await client.post("/api/groups", json={"name": "Temp"})).json()
    deleted = await client.delete(f"/api/groups/{group['id']}")
    assert deleted.status_code == 200
    missing = await client.get(f"/api/groups/{group['id']}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_group_keeps_its_profiles(client):
    """A group is a label on a profile, not a container that owns it."""
    group = (await client.post("/api/groups", json={"name": "Doomed"})).json()
    profile = (
        await client.post("/api/profiles", json={"name": "survivor", "group": group["id"]})
    ).json()

    await client.delete(f"/api/groups/{group['id']}")

    fetched = await client.get(f"/api/profiles/{profile['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["group"] is None


@pytest.mark.asyncio
async def test_updating_a_group_changes_only_what_was_sent(client):
    group = (
        await client.post("/api/groups", json={"name": "Before", "description": "keep me"})
    ).json()

    updated = await client.put(f"/api/groups/{group['id']}", json={"name": "After"})

    assert updated.status_code == 200
    assert updated.json()["name"] == "After"
    assert updated.json()["description"] == "keep me"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [("put", "/api/groups/nope"), ("delete", "/api/groups/nope"), ("get", "/api/groups/nope")],
)
async def test_an_unknown_group_is_a_404(client, method, path):
    request = getattr(client, method)
    response = await (request(path, json={"name": "x"}) if method == "put" else request(path))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_group_name_must_not_be_blank(client):
    assert (await client.post("/api/groups", json={"name": ""})).status_code == 422
