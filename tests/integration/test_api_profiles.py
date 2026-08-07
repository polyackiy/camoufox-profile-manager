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


@pytest.mark.asyncio
async def test_partial_browser_settings_update_keeps_the_rest(client):
    """Editing one field must not reset the rest of the generated fingerprint."""
    created = await client.post("/api/profiles", json={"name": "fp"})
    profile_id = created.json()["id"]
    original = created.json()["browser_settings"]

    updated = await client.put(
        f"/api/profiles/{profile_id}",
        json={"browser_settings": {"timezone": "Asia/Tokyo"}},
    )
    assert updated.status_code == 200
    settings = updated.json()["browser_settings"]

    assert settings["timezone"] == "Asia/Tokyo"
    # Values the client never sent must survive untouched.
    assert settings["screen"] == original["screen"]
    assert settings["locale"] == original["locale"]
    assert settings["device_memory"] == original["device_memory"]


@pytest.mark.asyncio
async def test_create_keeps_generated_values_for_omitted_fields(client):
    """Omitting an optional field must leave the generated fingerprint intact.

    The web form omits blanks on create; sending nulls instead produced profiles
    with no timezone and no geolocation.
    """
    created = await client.post(
        "/api/profiles",
        json={
            "name": "generated",
            "generate_fingerprint": True,
            "browser_settings": {"os": "windows", "window_width": 1280, "window_height": 720},
        },
    )
    assert created.status_code == 201
    settings = created.json()["browser_settings"]

    assert settings["os"] == "windows"
    assert settings["timezone"], "timezone should have been generated"
    assert settings["hardware_concurrency"], "hardware_concurrency should have been generated"


@pytest.mark.asyncio
async def test_create_keeps_the_notes_it_was_given(client):
    """Notes were accepted by the schema, then overwritten with a timestamp."""
    created = await client.post("/api/profiles", json={"name": "noted", "notes": "keep me"})
    assert created.json()["notes"] == "keep me"

    blank = await client.post("/api/profiles", json={"name": "unnoted"})
    assert blank.json()["notes"] is None


@pytest.mark.asyncio
async def test_explicit_null_clears_a_generated_value(client):
    """An explicit null is a deliberate 'none' — used by the geolocation switch."""
    created = await client.post(
        "/api/profiles",
        json={"name": "no-geo", "browser_settings": {"os": "windows", "geolocation": None}},
    )
    assert created.json()["browser_settings"]["geolocation"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,payload", [("notes", None), ("group", None), ("proxy_config", None)]
)
async def test_explicit_null_clears_the_field(client, field, payload):
    """Clearing a field must persist rather than silently report success."""
    created = await client.post(
        "/api/profiles",
        json={
            "name": "clearable",
            "notes": "keep me",
            "proxy_config": {"type": "http", "server": "1.2.3.4:8080"},
        },
    )
    profile_id = created.json()["id"]
    await client.put(f"/api/profiles/{profile_id}", json={"group": "g1"})

    updated = await client.put(f"/api/profiles/{profile_id}", json={field: payload})
    assert updated.status_code == 200
    assert updated.json()[field] is None


@pytest.mark.asyncio
async def test_omitted_fields_are_left_alone(client):
    """The flip side: not sending a field must not clear it."""
    created = await client.post(
        "/api/profiles",
        json={
            "name": "untouched",
            "notes": "keep me",
            "proxy_config": {"type": "http", "server": "1.2.3.4:8080"},
        },
    )
    profile_id = created.json()["id"]

    updated = await client.put(f"/api/profiles/{profile_id}", json={"name": "renamed"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "renamed"
    assert body["notes"] == "keep me"
    assert body["proxy_config"]["server"] == "1.2.3.4:8080"


@pytest.mark.asyncio
async def test_preset_catalogue_is_listed(client):
    response = await client.get("/api/fingerprints/presets", params={"os": "windows"})
    assert response.status_code == 200
    presets = response.json()["data"]["presets"]
    assert presets, "Camoufox ships real device presets"
    first = presets[0]
    assert first["id"].startswith("windows:")
    assert first["os"] == "windows"
    assert first["screen"]


@pytest.mark.asyncio
async def test_creating_from_a_preset_pins_that_device(client):
    """The pinned machine must be the device the user picked, not a generated one."""
    listed = await client.get("/api/fingerprints/presets", params={"os": "windows"})
    preset = listed.json()["data"]["presets"][2]

    created = await client.post(
        "/api/profiles", json={"name": "real-device", "fingerprint_preset": preset["id"]}
    )
    assert created.status_code == 201
    body = created.json()

    assert body["fingerprint"] is not None, "a preset pins the profile straight away"
    assert body["fingerprint"]["screen"] == preset["screen"]
    assert body["fingerprint"]["hardware_concurrency"] == preset["hardware_concurrency"]
    assert body["browser_settings"]["os"] == "windows"


@pytest.mark.asyncio
async def test_explicit_settings_still_beat_the_preset(client):
    listed = await client.get("/api/fingerprints/presets", params={"os": "windows"})
    preset = listed.json()["data"]["presets"][0]

    created = await client.post(
        "/api/profiles",
        json={
            "name": "override-preset",
            "fingerprint_preset": preset["id"],
            "browser_settings": {"hardware_concurrency": 16},
        },
    )
    assert created.json()["fingerprint"]["hardware_concurrency"] == 16


@pytest.mark.asyncio
async def test_unknown_preset_is_rejected(client):
    response = await client.post(
        "/api/profiles", json={"name": "nope", "fingerprint_preset": "windows:99999"}
    )
    assert response.status_code == 400
    assert "preset" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_fingerprint_is_absent_until_the_first_launch(client):
    """A profile has no pinned machine until it is opened for the first time."""
    created = await client.post("/api/profiles", json={"name": "unlaunched"})
    assert created.json()["fingerprint"] is None


@pytest.mark.asyncio
async def test_regenerating_drops_the_pinned_machine(client):
    """Otherwise the profile would keep its old hardware forever."""
    from camoufox_pm.api.dependencies import get_profile_manager

    created = await client.post("/api/profiles", json={"name": "repin"})
    profile_id = created.json()["id"]

    # Pin a machine the way a first launch would, using the manager the client
    # is wired to rather than a second one over a different database.
    manager = get_profile_manager()
    profile = await manager.get_profile(profile_id)
    profile.fingerprint = {"navigator.userAgent": "pinned", "screen.width": 1920}
    await manager.storage.update_profile(profile)

    stored = await client.get(f"/api/profiles/{profile_id}")
    assert stored.json()["fingerprint"]["user_agent"] == "pinned"

    reset = await client.post(f"/api/profiles/{profile_id}/reset-fingerprint")
    assert reset.status_code == 200
    assert reset.json()["fingerprint"] is None


@pytest.mark.asyncio
async def test_invalid_browser_settings_are_rejected_not_500(client):
    """browser_settings is a free-form dict, so bad values must read as 422."""
    created = await client.post("/api/profiles", json={"name": "bad"})
    profile_id = created.json()["id"]

    response = await client.put(
        f"/api/profiles/{profile_id}",
        json={"browser_settings": {"hardware_concurrency": "many"}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_webrtc_mode_is_coerced_to_the_enum(client):
    """A raw string used to survive as str and trip response serialization."""
    created = await client.post(
        "/api/profiles",
        json={"name": "rtc", "browser_settings": {"os": "windows", "webrtc_mode": "none"}},
    )
    assert created.status_code == 201
    assert created.json()["browser_settings"]["webrtc_mode"] == "none"


@pytest.mark.asyncio
async def test_flattened_browser_fields_still_apply(client):
    """The browser_* form of the update request keeps working alongside the nested one."""
    created = await client.post("/api/profiles", json={"name": "flat"})
    profile_id = created.json()["id"]

    updated = await client.put(
        f"/api/profiles/{profile_id}",
        json={"browser_os": "linux", "browser_hardware_concurrency": 12},
    )
    assert updated.status_code == 200
    settings = updated.json()["browser_settings"]
    assert settings["os"] == "linux"
    assert settings["hardware_concurrency"] == 12
