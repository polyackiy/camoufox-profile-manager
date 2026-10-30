"""Browser/update API safety: explicit installs and complete backups before links."""

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.routes import recovery, runtime
from camoufox_pm.config import get_settings
from camoufox_pm.core.backups import ProfileBackupManager
from camoufox_pm.core.browser_install import BrowserStatus
from camoufox_pm.core.updates import RELEASES, UpdateStatus
from camoufox_pm.main import app


@pytest.fixture(autouse=True)
def isolate_auth(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def runtime_client(client):
    backups = ProfileBackupManager(get_profile_manager())
    recovery.set_backup_manager(backups)
    yield client, backups
    recovery.set_backup_manager(None)


@pytest.fixture
def available_update(monkeypatch):
    check = AsyncMock(
        return_value=UpdateStatus(
            latest_version="999.0.0", available=True, release_url=f"{RELEASES}/tag/v999.0.0"
        )
    )
    monkeypatch.setattr(runtime, "check_update", check)
    return check


@pytest.mark.parametrize("prefix", ["/api/v1", "/api"])
async def test_readiness_get_never_starts_installation(client, monkeypatch, prefix):
    installer = Mock()
    installer.status.return_value = BrowserStatus(installed=False, message="Not installed")
    monkeypatch.setattr(runtime, "browser_installer", installer)
    response = await client.get(f"{prefix}/system/browser")
    assert response.status_code == 200
    assert response.json()["data"]["installed"] is False
    assert response.json()["data"]["state"] == "idle"
    installer.start.assert_not_called()


async def test_explicit_post_starts_download_and_returns_pollable_progress(client, monkeypatch):
    installer = Mock()
    installer.start.return_value = BrowserStatus(
        installed=False, state="downloading", progress=42, message="Downloading"
    )
    monkeypatch.setattr(runtime, "browser_installer", installer)
    response = await client.post("/api/v1/system/browser/install")
    assert response.status_code == 200
    assert response.json()["data"]["state"] == "downloading"
    assert response.json()["data"]["progress"] == 42
    installer.start.assert_called_once_with()


async def test_runtime_routes_use_existing_api_auth_guard(client, monkeypatch):
    monkeypatch.setenv("CPM_API_KEY", "test-runtime-key")
    get_settings.cache_clear()
    installer = Mock()
    monkeypatch.setattr(runtime, "browser_installer", installer)
    for path, method in [
        ("/system/browser", "GET"),
        ("/system/browser/install", "POST"),
        ("/system/updates", "GET"),
        ("/system/updates/prepare", "POST"),
    ]:
        response = await client.request(method, f"/api/v1{path}")
        assert response.status_code == 401
    installer.start.assert_not_called()
    installer.status.assert_not_called()


async def test_update_check_failure_is_a_status_result_not_an_available_update(client, monkeypatch):
    monkeypatch.setattr(
        runtime, "check_update", AsyncMock(return_value=UpdateStatus(error="Network unavailable"))
    )
    response = await client.get("/api/v1/system/updates")
    assert response.status_code == 200
    body = response.json()["data"]
    assert not body["available"]
    assert body["error"] == "Network unavailable"


@pytest.mark.parametrize(
    ("update", "status"),
    [(UpdateStatus(error="Network unavailable"), 503), (UpdateStatus(available=False), 409)],
)
async def test_prepare_requires_a_successful_available_release_before_backing_up(
    runtime_client, monkeypatch, update, status
):
    client, backups = runtime_client
    create = AsyncMock()
    monkeypatch.setattr(backups, "create_backups", create)
    monkeypatch.setattr(runtime, "check_update", AsyncMock(return_value=update))
    response = await client.post("/api/v1/system/updates/prepare")
    assert response.status_code == status
    assert "release_url" not in response.text
    create.assert_not_called()


async def create_profile(client, name):
    response = await client.post(
        "/api/v1/profiles", json={"name": name, "generate_fingerprint": False}
    )
    assert response.status_code == 201
    profile = response.json()
    Path(profile["storage_path"], "cookies.sqlite").write_bytes(f"cookies for {name}".encode())
    return profile


async def test_prepare_publishes_official_link_only_after_active_and_trash_backups(
    runtime_client, available_update
):
    client, backups = runtime_client
    active = await create_profile(client, "Active profile")
    trashed = await create_profile(client, "Trashed profile")
    assert (await client.delete(f"/api/v1/profiles/{trashed['id']}")).status_code == 200
    response = await client.post("/api/v1/system/updates/prepare")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["release_url"] == f"{RELEASES}/tag/v999.0.0"
    assert data["backup_count"] == 2
    saved = backups.list_backups()
    assert {item["profile_id"] for item in saved} == {active["id"], trashed["id"]}
    assert {item["reason"] for item in saved} == {"before_update"}
    for item in saved:
        with zipfile.ZipFile(backups.directory / f"{item['id']}.zip") as archive:
            assert (
                archive.read("data/cookies.sqlite")
                == f"cookies for {item['profile_name']}".encode()
            )
    assert (await client.get(f"/api/v1/profiles/{active['id']}")).status_code == 200
    assert len((await client.get("/api/v1/trash/profiles")).json()["data"]["profiles"]) == 1


async def test_partial_backup_with_a_live_lease_never_returns_release_link(
    runtime_client, available_update
):
    client, backups = runtime_client
    closed = await create_profile(client, "Closed")
    leased = await create_profile(client, "Leased")
    storage = get_profile_manager().storage
    assert await storage.acquire_lease(leased["id"], "another-instance", 300)
    response = await client.post("/api/v1/system/updates/prepare")
    assert response.status_code == 409
    assert leased["id"] in response.json()["error"]["message"]
    assert "release_url" not in response.text
    assert {item["profile_id"] for item in backups.list_backups()} == {closed["id"]}
    assert Path(leased["storage_path"], "cookies.sqlite").read_bytes() == b"cookies for Leased"


async def test_missing_profile_data_is_reported_as_backup_failure_without_download(
    runtime_client, available_update
):
    client, backups = runtime_client
    profile = await create_profile(client, "Missing data")
    directory = Path(profile["storage_path"])
    (directory / "cookies.sqlite").unlink()
    directory.rmdir()
    response = await client.post("/api/v1/system/updates/prepare")
    assert response.status_code == 409
    assert "missing" in response.json()["error"]["message"].lower()
    assert "release_url" not in response.text
    assert backups.list_backups() == []


async def test_unexpected_backup_error_returns_no_update_link(
    runtime_client, available_update, monkeypatch
):
    _, backups = runtime_client
    monkeypatch.setattr(
        backups, "create_backups", AsyncMock(side_effect=OSError("disk unavailable"))
    )
    # Exercise the server's real failure response rather than ASGITransport's default re-raise.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/system/updates/prepare")
    assert response.status_code == 500
    assert "release_url" not in response.text
    assert f"{RELEASES}/tag/" not in response.text
