"""Integration tests for the profiles API."""

import pytest


@pytest.mark.asyncio
async def test_create_and_get_profile(client):
    created = await client.post("/api/profiles", json={"name": "acct-1"})
    assert created.status_code == 201
    profile_id = created.json()["id"]

    fetched = await client.get(f"/api/profiles/{profile_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "acct-1"


@pytest.mark.asyncio
async def test_list_profiles_paginated(client):
    for i in range(3):
        await client.post("/api/profiles", json={"name": f"p{i}"})
    listed = await client.get("/api/profiles", params={"page": 1, "per_page": 2})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 3
    assert len(body["profiles"]) == 2
    assert body["has_next"] is True


@pytest.mark.asyncio
async def test_search_profiles_by_name(client):
    await client.post("/api/profiles", json={"name": "facebook-main"})
    await client.post("/api/profiles", json={"name": "twitter-alt"})
    found = await client.get("/api/profiles", params={"search": "facebook"})
    names = [p["name"] for p in found.json()["profiles"]]
    assert names == ["facebook-main"]


@pytest.mark.asyncio
async def test_update_profile(client):
    created = await client.post("/api/profiles", json={"name": "before"})
    profile_id = created.json()["id"]
    updated = await client.put(f"/api/profiles/{profile_id}", json={"name": "after"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "after"


@pytest.mark.asyncio
async def test_delete_profile(client):
    created = await client.post("/api/profiles", json={"name": "temp"})
    profile_id = created.json()["id"]
    deleted = await client.delete(f"/api/profiles/{profile_id}")
    assert deleted.status_code == 200
    missing = await client.get(f"/api/profiles/{profile_id}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_unknown_profile_returns_404(client):
    response = await client.get("/api/profiles/does-not-exist")
    assert response.status_code == 404
