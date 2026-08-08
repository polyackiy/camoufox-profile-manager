"""Integration tests for the profiles API."""

import pytest

from camoufox_pm.core import fingerprint_store


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
async def test_a_profile_round_trips_through_an_archive(client):
    """Export then import must reproduce the profile, with an id of its own."""
    created = await client.post(
        "/api/profiles",
        json={
            "name": "warmed-up",
            "notes": "months of history",
            "browser_settings": {"os": "macos", "timezone": "Europe/Berlin"},
            "proxy_config": {"type": "http", "server": "1.2.3.4:8080", "password": "secret"},
        },
    )
    original = created.json()

    exported = await client.get(f"/api/profiles/{original['id']}/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert "warmed-up.camoufox.zip" in exported.headers["content-disposition"]

    imported = await client.post(
        "/api/profiles/import",
        files={"file": ("warmed-up.camoufox.zip", exported.content, "application/zip")},
    )
    assert imported.status_code == 201
    restored = imported.json()

    assert restored["name"] == "warmed-up"
    assert restored["notes"] == "months of history"
    assert restored["browser_settings"]["timezone"] == "Europe/Berlin"
    assert restored["proxy_config"]["server"] == "1.2.3.4:8080"
    assert restored["id"] != original["id"], "an import must not collide with its source"
    # The source's group id is local to that instance; it must not dangle here.
    assert restored["group"] is None


@pytest.mark.asyncio
async def test_importing_a_pinned_profile_keeps_the_machine(client):
    """The point of the archive: the same identity comes back."""
    from camoufox_pm.api.dependencies import get_profile_manager

    created = await client.post("/api/profiles", json={"name": "pinned-export"})
    manager = get_profile_manager()
    profile = await manager.get_profile(created.json()["id"])
    profile.fingerprint = {
        "navigator.userAgent": "UA/1.0",
        "navigator.hardwareConcurrency": 12,
        "screen.width": 2560,
        "screen.height": 1440,
    }
    await manager.storage.update_profile(profile)

    exported = await client.get(f"/api/profiles/{profile.id}/export")
    imported = await client.post(
        "/api/profiles/import",
        files={"file": ("p.zip", exported.content, "application/zip")},
    )

    fingerprint = imported.json()["fingerprint"]
    assert fingerprint["user_agent"] == "UA/1.0"
    assert fingerprint["hardware_concurrency"] == 12
    assert fingerprint["screen"] == "2560x1440"


@pytest.mark.asyncio
async def test_import_can_rename(client):
    created = await client.post("/api/profiles", json={"name": "original"})
    exported = await client.get(f"/api/profiles/{created.json()['id']}/export")

    imported = await client.post(
        "/api/profiles/import",
        params={"name": "renamed on import"},
        files={"file": ("p.zip", exported.content, "application/zip")},
    )
    assert imported.json()["name"] == "renamed on import"


@pytest.mark.asyncio
async def test_importing_rubbish_is_a_400(client):
    response = await client.post(
        "/api/profiles/import",
        files={"file": ("notes.txt", b"this is not a zip", "text/plain")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_exporting_an_unknown_profile_is_a_404(client):
    response = await client.get("/api/profiles/does-not-exist/export")
    assert response.status_code == 404


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


@pytest.mark.browser
@pytest.mark.asyncio
async def test_creating_from_a_preset_pins_that_device(client):
    """The pinned machine must be the device the user picked, not a generated one.

    Marked ``browser``: pinning resolves a full Camoufox config, which needs the
    browser binary. Without the marker this would be skipped by the fast suite
    and absent from the browser suite — covered by neither.
    """
    if not fingerprint_store.can_resolve():
        pytest.skip("pinning a preset needs the Camoufox browser on disk")
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


@pytest.mark.browser
@pytest.mark.asyncio
async def test_explicit_settings_still_beat_the_preset(client):
    if not fingerprint_store.can_resolve():
        pytest.skip("pinning a preset needs the Camoufox browser on disk")

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
async def test_preset_without_the_browser_says_so(client, monkeypatch):
    """The catalogue reads without the browser; pinning does not.

    Creating the profile anyway would give the user a generated machine instead
    of the device they picked, and never tell them.
    """
    monkeypatch.setattr(fingerprint_store, "can_resolve", lambda: False)

    listed = await client.get("/api/fingerprints/presets", params={"os": "windows"})
    assert listed.json()["data"]["presets"], "the catalogue ships with the package"

    response = await client.post(
        "/api/profiles", json={"name": "no-browser", "fingerprint_preset": "windows:0"}
    )
    assert response.status_code == 400
    assert "camoufox fetch" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_preset_that_cannot_be_pinned_creates_nothing(client, monkeypatch):
    """Resolution can fail for reasons other than a missing browser.

    Creating the profile anyway would leave it unpinned, and its first launch
    would quietly assign a generated machine instead of the chosen device — the
    exact outcome the preset feature exists to prevent.
    """
    monkeypatch.setattr(fingerprint_store, "can_resolve", lambda: True)
    monkeypatch.setattr(fingerprint_store, "resolve", lambda *a, **k: {})

    before = (await client.get("/api/profiles")).json()["total"]
    response = await client.post(
        "/api/profiles", json={"name": "unpinnable", "fingerprint_preset": "windows:0"}
    )

    assert response.status_code == 400
    assert "Could not pin" in response.json()["detail"]
    after = (await client.get("/api/profiles")).json()["total"]
    assert after == before, "a profile that could not be pinned must not be created"


@pytest.mark.asyncio
async def test_a_refused_import_leaves_nothing_behind(client, monkeypatch):
    """A rejected archive must not strand a profile directory nobody can see."""
    from camoufox_pm.api.dependencies import get_profile_manager
    from camoufox_pm.core import profile_archive

    manager = get_profile_manager()
    profiles_dir = manager.profiles_dir

    # A real archive with real browser data in it, refused part-way through
    # extraction. Without data files the archive is empty and nothing is refused.
    created = await client.post("/api/profiles", json={"name": "source"})
    source = await manager.get_profile(created.json()["id"])
    source_dir = profiles_dir / f"profile_{source.id}"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "cookies.sqlite").write_bytes(b"C" * 4000)

    exported = await client.get(f"/api/profiles/{source.id}/export")
    monkeypatch.setattr(profile_archive, "_MAX_EXTRACTED_BYTES", 10)

    before = set(profiles_dir.glob("*"))
    response = await client.post(
        "/api/profiles/import", files={"file": ("p.zip", exported.content, "application/zip")}
    )
    assert response.status_code == 400

    after = set(profiles_dir.glob("*"))
    assert after == before, f"refused import left {after - before}"


@pytest.mark.asyncio
async def test_unknown_preset_is_rejected(client):
    response = await client.post(
        "/api/profiles", json={"name": "nope", "fingerprint_preset": "windows:99999"}
    )
    assert response.status_code == 400
    assert "preset" in response.json()["detail"].lower()


STALE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:100.0) Gecko/20100101 Firefox/100.0"
FRESH_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:199.0) Gecko/20100101 Firefox/199.0"


@pytest.mark.asyncio
async def test_refresh_browser_version_keeps_the_machine(client, monkeypatch):
    """A pin must be able to move to a newer browser without changing device.

    A pin never ages on its own, so a profile kept for months keeps advertising
    the browser it was created with — and a browser several releases behind is
    itself unusual enough to notice.

    ``resolve`` is stubbed so this covers the wiring without needing the browser
    binary; ``tests/browser`` exercises it against a real Camoufox.
    """
    from camoufox_pm.api.dependencies import get_profile_manager

    # A newly resolved fingerprint describes a completely different machine.
    monkeypatch.setattr(
        fingerprint_store,
        "resolve",
        lambda *a, **k: {
            "navigator.userAgent": FRESH_UA,
            "navigator.hardwareConcurrency": 2,
            "screen.width": 800,
            "screen.height": 600,
            "webGl:renderer": "ANGLE (Intel, Intel(R) HD Graphics)",
            "canvas:seed": 999999,
            "fonts:spacing_seed": 5,
        },
    )
    monkeypatch.setattr(fingerprint_store, "installed_major", lambda: 199)

    created = await client.post("/api/profiles", json={"name": "ageing"})
    profile_id = created.json()["id"]

    manager = get_profile_manager()
    profile = await manager.get_profile(profile_id)
    # A pin as it would look after months on an older release.
    profile.fingerprint = {
        "navigator.userAgent": STALE_UA,
        "navigator.hardwareConcurrency": 12,
        "screen.width": 2560,
        "screen.height": 1440,
        "webGl:renderer": "ANGLE (NVIDIA, GeForce GTX 980)",
        "canvas:seed": 424242,
        "fonts:spacing_seed": 777,
    }
    await manager.storage.update_profile(profile)

    stale = (await client.get(f"/api/profiles/{profile_id}")).json()["fingerprint"]
    assert stale["browser_major"] == 100
    assert stale["browser_outdated"] is True

    response = await client.post(f"/api/profiles/{profile_id}/refresh-browser")
    assert response.status_code == 200
    refreshed = response.json()["fingerprint"]

    assert refreshed["browser_major"] == 199, "the browser should have moved forward"
    assert refreshed["browser_outdated"] is False
    # The device is unchanged.
    assert refreshed["screen"] == "2560x1440"
    assert refreshed["hardware_concurrency"] == 12
    assert refreshed["gpu"] == "ANGLE (NVIDIA, GeForce GTX 980)"

    stored = await manager.get_profile(profile_id)
    assert stored.fingerprint["canvas:seed"] == 424242, "changing the seed would alter the canvas"
    assert stored.fingerprint["fonts:spacing_seed"] == 777


@pytest.mark.asyncio
async def test_refresh_browser_version_needs_a_pin_first(client):
    """Refreshing an unpinned profile would silently invent a machine."""
    created = await client.post("/api/profiles", json={"name": "unpinned"})
    response = await client.post(f"/api/profiles/{created.json()['id']}/refresh-browser")
    assert response.status_code == 400
    assert "launch it once" in response.json()["detail"]


@pytest.mark.asyncio
async def test_refresh_browser_version_on_a_missing_profile_is_404(client):
    response = await client.post("/api/profiles/does-not-exist/refresh-browser")
    assert response.status_code == 404


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
