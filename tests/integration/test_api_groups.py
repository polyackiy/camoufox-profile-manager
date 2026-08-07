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
