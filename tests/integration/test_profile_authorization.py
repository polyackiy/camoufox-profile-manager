"""Real account sessions isolate every profile route and collection alias."""

import pytest

from camoufox_pm.api import dependencies
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth

PASSWORD = "strong-test-password"


async def setup_actor(client, role, monkeypatch):
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    get_settings.cache_clear()
    manager = dependencies.get_profile_manager()
    own = await manager.create_profile(
        "Visible",
        generate_fingerprint=False,
        proxy_config={
            "type": "http",
            "server": "localhost:8080",
            "username": "proxy-user",
            "password": "proxy-secret",
        },
    )
    other = await manager.create_profile("Hidden", generate_fingerprint=False)
    storage = dependencies.get_storage_manager()
    await storage.create_user(auth.new_user_id(), "admin", auth.hash_password(PASSWORD))
    await storage.create_user(auth.new_user_id(), "member", auth.hash_password(PASSWORD), role=role)
    await storage.set_profile_grant("member", own.id)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "member", "password": PASSWORD}
    )
    assert login.status_code == 200
    return manager, own, other


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
@pytest.mark.parametrize("role", ["operator", "viewer"])
async def test_profile_lists_details_and_credentials(client, monkeypatch, prefix, role):
    manager, own, other = await setup_actor(client, role, monkeypatch)
    listing = await client.get(prefix + "/profiles", params={"per_page": 1})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    profile = listing.json()["profiles"][0]
    assert profile["id"] == own.id
    assert profile["storage_path"] is None
    assert "proxy-secret" not in listing.text and "proxy-user" not in listing.text
    for suffix in ["", "/stats", "/extensions", "/export", "/cookies/export?row_version=0"]:
        inaccessible = await client.get(prefix + "/profiles/" + other.id + suffix)
        missing = await client.get(prefix + "/profiles/missing" + suffix)
        assert inaccessible.status_code == missing.status_code == 404
    assert (await client.get(prefix + "/profiles/" + own.id + "/export")).status_code == 404
    for route in ["/proxies", "/templates", "/extensions", "/saved-filters", "/system/backups"]:
        assert (await client.get(prefix + route)).status_code == 403


@pytest.mark.asyncio
async def test_bulk_validates_every_id_before_mutation(client, monkeypatch):
    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    result = await client.post(
        "/api/v1/profiles/tags",
        json={
            "profiles": [
                {"id": own.id, "row_version": own.row_version},
                {"id": other.id, "row_version": other.row_version},
            ],
            "add": ["changed"],
        },
    )
    assert result.status_code == 404
    assert (await manager.get_profile(own.id)).tags == []
    result = await client.post(
        "/api/v1/profiles/clear-geography", json={"profile_ids": [own.id, other.id]}
    )
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_operator_edit_preserves_redacted_proxy_credentials(client, monkeypatch):
    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    metadata = (await client.get("/api/v1/profiles/" + own.id)).json()
    result = await client.put(
        "/api/v1/profiles/" + own.id,
        json={"name": "Edited", "proxy_config": metadata["proxy_config"]},
    )
    assert result.status_code == 200
    assert (await manager.get_profile(own.id)).proxy.password == "proxy-secret"
    assert "proxy-secret" not in result.text
    assert (
        await client.put("/api/v1/profiles/" + other.id, json={"name": "Stolen"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_viewer_cannot_edit_launch_transfer_or_delete(client, monkeypatch):
    manager, own, _ = await setup_actor(client, "viewer", monkeypatch)
    for method, suffix, body in [
        ("put", "", {"name": "Edited"}),
        ("delete", "", None),
        ("post", "/launch", {}),
        ("post", "/close", None),
        ("post", "/reset-fingerprint", None),
        ("post", "/check-proxy", None),
        ("post", "/cookies/preview", {"text": "[]", "format": "json"}),
    ]:
        response = await getattr(client, method)(
            "/api/v1/profiles/" + own.id + suffix, **({"json": body} if body is not None else {})
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_excel_export_contains_only_explicitly_exportable_profiles(client, monkeypatch):
    import io

    from openpyxl import load_workbook

    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    await dependencies.get_storage_manager().set_profile_grant("member", own.id, can_export=True)
    response = await client.get("/api/v1/profiles/export/excel")
    assert response.status_code == 200
    workbook = load_workbook(io.BytesIO(response.content))
    cells = str(list(workbook.active.values))
    assert own.id in cells
    assert other.id not in cells


@pytest.mark.asyncio
async def test_group_counts_and_active_close_are_scoped(client, monkeypatch):
    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    group = await manager.create_group("Shared")
    hidden_group = await manager.create_group("Secret group")
    await manager.update_profile(own.id, {"group": group["id"]})
    await manager.update_profile(other.id, {"group": group["id"]})
    groups = (await client.get("/api/v1/groups")).json()
    assert groups["total"] == 1
    assert groups["groups"][0]["profile_count"] == 1
    assert (await client.get("/api/v1/groups/" + hidden_group["id"])).status_code == 404
    from datetime import datetime

    closed = []

    async def active():
        return [
            {
                "profile_id": p.id,
                "process_id": 10,
                "started_at": datetime.now(),
                "options": {"password": "secret"},
            }
            for p in [own, other]
        ]

    async def close(profile_id):
        closed.append(profile_id)

    monkeypatch.setattr(manager, "get_active_browsers", active)
    monkeypatch.setattr(manager, "close_browser", close)
    response = await client.get("/api/v1/browsers/active")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert "secret" not in response.text and other.id not in response.text
    response = await client.post("/api/v1/browsers/close-all")
    assert response.status_code == 200
    assert closed == [own.id]


@pytest.mark.asyncio
async def test_operator_cannot_retarget_proxy_secret_or_link_hidden_group(client, monkeypatch):
    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    group = await manager.create_group("Hidden group")
    response = await client.put(
        "/api/v1/profiles/" + own.id,
        json={"proxy_config": {"type": "http", "server": "attacker:8080"}},
    )
    assert response.status_code == 403
    assert (await manager.get_profile(own.id)).proxy.server == "localhost:8080"
    response = await client.put("/api/v1/profiles/" + own.id, json={"group": group["id"]})
    assert response.status_code == 403
    assert (await manager.get_profile(own.id)).group is None


@pytest.mark.asyncio
async def test_viewer_explicit_export_grant_is_independent_of_edit_permission(client, monkeypatch):
    manager, own, other = await setup_actor(client, "viewer", monkeypatch)
    await dependencies.get_storage_manager().set_profile_grant("member", own.id, can_export=True)
    response = await client.get("/api/v1/profiles/" + own.id)
    assert response.json()["permissions"] == {
        "view": True,
        "edit": False,
        "launch": False,
        "export": True,
    }
    response = await client.get("/api/v1/profiles/" + own.id + "/export")
    assert response.status_code == 200
    assert (
        await client.put("/api/v1/profiles/" + own.id, json={"name": "Edited"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_proxy_check_never_exposes_historical_or_live_error_credentials(client, monkeypatch):
    from datetime import datetime

    from camoufox_pm.core import proxy_check
    from camoufox_pm.core.models import ProxyCheckFinding, ProxyCheckRecord

    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    record = ProxyCheckRecord(
        checked_at=datetime.now(),
        reachable=False,
        error="proxy-secret",
        findings=[
            ProxyCheckFinding(level="error", field="proxy", message="proxy-user proxy-secret")
        ],
    )
    await manager.storage.set_proxy_check(own.id, record)
    response = await client.get("/api/v1/profiles/" + own.id)
    assert response.status_code == 200
    assert "proxy-secret" not in response.text and "proxy-user" not in response.text

    async def check(*args, **kwargs):
        return proxy_check.ProxyCheckResult(
            reachable=False,
            error="proxy-secret",
            findings=[proxy_check.Finding("error", "proxy", "proxy-user proxy-secret")],
        )

    monkeypatch.setattr(proxy_check, "check", check)
    response = await client.post("/api/v1/profiles/" + own.id + "/check-proxy")
    assert response.status_code == 200
    assert "proxy-secret" not in response.text and "proxy-user" not in response.text
    assert "proxy-secret" not in (await client.get("/api/v1/profiles/" + own.id)).text


@pytest.mark.asyncio
async def test_browser_service_failures_and_messages_do_not_echo_secrets(client, monkeypatch):
    manager, own, other = await setup_actor(client, "operator", monkeypatch)

    async def launch_failure(*args, **kwargs):
        raise ValueError("proxy-secret /host/private/profile")

    monkeypatch.setattr(manager, "launch_browser", launch_failure)
    response = await client.post("/api/v1/profiles/" + own.id + "/launch", json={})
    assert response.status_code == 404
    assert "proxy-secret" not in response.text and "/host/private" not in response.text

    async def launch_message(*args, **kwargs):
        return {
            "status": "proxy-secret",
            "message": "proxy-secret /host/private/profile",
            "camoufox_options": {"password": "proxy-secret"},
        }

    monkeypatch.setattr(manager, "launch_browser", launch_message)
    response = await client.post("/api/v1/profiles/" + own.id + "/launch", json={})
    assert response.status_code == 200
    assert "proxy-secret" not in response.text and "/host/private" not in response.text

    async def close_message(*args, **kwargs):
        return {
            "status": "proxy-secret",
            "profile_id": own.id,
            "message": "proxy-secret /host/private",
        }

    monkeypatch.setattr(manager, "close_browser", close_message)
    response = await client.post("/api/v1/profiles/" + own.id + "/close")
    assert response.status_code == 200
    assert "proxy-secret" not in response.text and "/host/private" not in response.text


@pytest.mark.asyncio
async def test_archive_temp_file_is_removed_if_export_revoked_during_generation(
    client, monkeypatch
):
    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    storage = dependencies.get_storage_manager()
    await storage.set_profile_grant("member", own.id, can_export=True)
    paths = []

    async def export(profile_id, destination):
        paths.append(destination)
        destination.write_bytes(b"private archive bytes")
        await storage.set_profile_grant("member", own.id, can_export=False)

    monkeypatch.setattr(manager, "export_profile", export)
    response = await client.get("/api/v1/profiles/" + own.id + "/export")
    assert response.status_code == 404
    assert len(paths) == 1 and not paths[0].exists()
    assert "private archive" not in response.text
