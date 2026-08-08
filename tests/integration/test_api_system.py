"""Integration tests for the system API, including its two destructive endpoints."""

import shutil

import pytest

from camoufox_pm.api import dependencies
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.config import get_settings
from camoufox_pm.core.browser_session import BrowserSession


class FakeCamoufox:
    """Stands in for a running AsyncCamoufox and records that it was closed."""

    def __init__(self):
        self.exits = 0

    async def __aexit__(self, *_exc):
        self.exits += 1


@pytest.fixture
def storage_dir(client, tmp_path, monkeypatch):
    """Point the settings at the same database and directory the client uses.

    The working directory is moved as well, so that code reading the relative
    default (``./data``) cannot reach anything real.
    """
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "api.db"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def make_orphan(root, name: str = "deadbeef", size: int = 4096):
    """Create a profile directory that no database row refers to."""
    directory = root / "profiles" / f"profile_{name}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cookies.sqlite").write_bytes(b"x" * size)
    return directory


@pytest.mark.asyncio
async def test_status_reports_what_is_there(client):
    await client.post("/api/profiles", json={"name": "one"})
    await client.post("/api/profiles", json={"name": "two"})
    await client.post("/api/groups", json={"name": "Social"})

    body = (await client.get("/api/system/status")).json()

    assert body["total_profiles"] == 2
    assert body["total_groups"] == 1
    assert body["running_browsers"] == 0


@pytest.mark.asyncio
async def test_status_counts_a_running_browser(client):
    await client.post("/api/profiles", json={"name": "one"})
    manager = get_profile_manager()
    manager.browser_sessions.active_sessions["p1"] = BrowserSession("p1", camoufox=FakeCamoufox())

    body = (await client.get("/api/system/status")).json()

    assert body["running_browsers"] == 1


@pytest.mark.asyncio
async def test_the_diagnostic_reports_orphaned_and_missing_directories(client, storage_dir):
    created = (await client.post("/api/profiles", json={"name": "lost"})).json()
    shutil.rmtree(storage_dir / "profiles" / f"profile_{created['id']}")
    make_orphan(storage_dir)

    body = (await client.get("/api/system/profiles/diagnostic")).json()

    assert body["total_profiles_in_db"] == 1
    assert body["orphaned_directories"] == 1
    assert body["missing_directories"] == 1
    assert body["issues_found"] == 2


@pytest.mark.asyncio
async def test_cleanup_removes_the_orphan_and_keeps_the_live_profile(client, storage_dir):
    """Regression: this endpoint deleted every profile directory on disk.

    The cleanup was built with its own defaults — ``./data`` and
    ``./data/profiles.db`` — rather than the configured database. Under any
    other database file name it read an empty one, found no profiles to match
    the directories against, and removed all of them.
    """
    live = (await client.post("/api/profiles", json={"name": "live"})).json()
    live_dir = storage_dir / "profiles" / f"profile_{live['id']}"
    (live_dir / "cookies.sqlite").write_bytes(b"session")
    orphan = make_orphan(storage_dir)

    body = (await client.post("/api/system/profiles/cleanup")).json()

    assert body["orphaned_removed"] == 1
    assert not orphan.exists()
    assert (live_dir / "cookies.sqlite").read_bytes() == b"session"


@pytest.mark.asyncio
async def test_a_dry_run_reports_the_orphan_without_removing_it(client, storage_dir):
    await client.post("/api/profiles", json={"name": "live"})
    orphan = make_orphan(storage_dir)

    body = (await client.post("/api/system/profiles/cleanup?dry_run=true")).json()

    assert body["dry_run"] is True
    assert body["orphaned_removed"] == 1
    assert body["freed_space_mb"] == pytest.approx(4096 / (1024 * 1024))
    assert orphan.exists()


@pytest.mark.asyncio
async def test_restart_closes_every_running_browser(client):
    manager = get_profile_manager()
    camoufoxes = [FakeCamoufox(), FakeCamoufox()]
    for profile_id, camoufox in zip(("p1", "p2"), camoufoxes, strict=True):
        manager.browser_sessions.active_sessions[profile_id] = BrowserSession(
            profile_id, camoufox=camoufox
        )

    response = await client.post("/api/system/restart")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert [c.exits for c in camoufoxes] == [1, 1]
    assert manager.browser_sessions.list_active() == []


@pytest.mark.asyncio
async def test_health_reports_the_database_and_the_profile_count(client):
    await client.post("/api/profiles", json={"name": "one"})

    body = (await client.get("/health")).json()

    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert body["profiles_count"] == 1


@pytest.mark.asyncio
async def test_health_answers_when_storage_is_unavailable(client, monkeypatch):
    """This is what a container orchestrator polls. It has to answer 'unhealthy'
    rather than raise, or a failing instance looks the same as an unreachable one."""
    monkeypatch.setattr(dependencies, "_profile_manager", None)

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "unhealthy"
    assert response.json()["database"] == "disconnected"


@pytest.mark.asyncio
async def test_the_config_endpoint_reports_secrets_as_set_but_never_their_values(
    client, monkeypatch
):
    """The Settings screen has to show whether encryption and the key are on.
    Reporting them means serving both secrets to anyone who reaches the API."""
    monkeypatch.setenv("CPM_API_KEY", "top-secret-key")
    monkeypatch.setenv("CPM_SECRET_KEY", "top-secret-encryption")
    get_settings.cache_clear()
    try:
        response = await client.get("/api/system/config", headers={"X-API-Key": "top-secret-key"})

        assert response.json()["data"]["api_key_set"] is True
        assert response.json()["data"]["encryption_enabled"] is True
        assert "top-secret-key" not in response.text
        assert "top-secret-encryption" not in response.text
    finally:
        get_settings.cache_clear()
