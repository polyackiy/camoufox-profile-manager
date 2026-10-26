"""Real API sessions and storage enforce workflow isolation and safe receipts."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from camoufox_pm.api import dependencies
from camoufox_pm.core import auth
from tests.integration.test_profile_authorization import PASSWORD, setup_actor

DEFINITION = {
    "name": "Demo",
    "steps": [
        {"name": "Start", "action": "launch"},
        {
            "name": "Visit",
            "action": "navigate",
            "url": "https://example.test/?token=definition-only",
        },
        {"name": "Finish", "action": "close"},
    ],
}


@pytest.fixture
def browser_stub(client, monkeypatch):
    manager = dependencies.get_profile_manager()
    visits = []
    hold = asyncio.Event()
    hold.set()
    launched = asyncio.Event()

    async def launch(profile_id, options, on_exit=None):
        launched.set()
        await hold.wait()

        async def goto(url, **kwargs):
            visits.append(url)
            return SimpleNamespace(status=200)

        async def new_page():
            return SimpleNamespace(goto=goto)

        async def terminate():
            pass

        session = SimpleNamespace(
            context=SimpleNamespace(new_page=new_page),
            process_id=None,
            terminate=terminate,
            on_exit=None,
        )
        manager.browser_sessions.active_sessions[profile_id] = session
        return session

    async def geo(*_args):
        pass

    monkeypatch.setattr(manager.browser_sessions, "launch", launch)
    monkeypatch.setattr(
        "camoufox_pm.core.profile_manager.fingerprint_store.resolve", lambda *_a, **_k: {}
    )
    monkeypatch.setattr("camoufox_pm.core.profile_manager.proxy_check.prepare_proxy_geoip", geo)
    monkeypatch.setattr(
        "camoufox_pm.core.profile_manager.proxy_check.fill_what_geoip_would_have", geo
    )
    return SimpleNamespace(visits=visits, hold=hold, launched=launched)


async def workflow(client, **fields):
    response = await client.post("/api/v1/workflows", json={**DEFINITION, **fields})
    assert response.status_code == 201, response.text
    assert "owner" not in response.json()
    return response.json()


def request_for(value, *profiles, **changes):
    return {
        "request_id": str(uuid4()),
        "revision": value["revision"],
        "targets": [
            {"profile_id": profile.id, "row_version": profile.row_version} for profile in profiles
        ],
        **changes,
    }


async def complete(run_id):
    engine = dependencies.get_scheduler().workflows
    task = engine.tasks.get(run_id)
    if task:
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_definition_cas_validation_and_delete(client):
    created = await workflow(client)
    changed = await client.put(
        "/api/v1/workflows/" + created["id"], json={**DEFINITION, "name": "Updated", "revision": 1}
    )
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    assert (
        await client.put("/api/v1/workflows/" + created["id"], json={**DEFINITION, "revision": 1})
    ).status_code == 409
    assert (
        await client.delete("/api/v1/workflows/" + created["id"] + "?revision=1")
    ).status_code == 409
    assert (
        await client.delete("/api/v1/workflows/" + created["id"] + "?revision=2")
    ).status_code == 200
    assert (await client.get("/api/v1/workflows")).json()["workflows"] == []
    events = await dependencies.get_storage_manager().list_audit(action="delete_workflow")
    assert events[0]["object_id"] == created["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
@pytest.mark.parametrize(
    "bad_step",
    [
        {"name": "Bad", "action": "shell", "command": "secret-command"},
        {"name": "Bad", "action": "navigate", "url": "https://user:PRIVATE@example.test/"},
    ],
)
async def test_validation_never_echoes_credentials_or_unknown_parameters(client, prefix, bad_step):
    response = await client.post(
        prefix + "/workflows",
        json={**DEFINITION, "steps": [DEFINITION["steps"][0], bad_step, DEFINITION["steps"][-1]]},
    )
    assert response.status_code == 422
    assert "PRIVATE" not in response.text and "secret-command" not in response.text
    assert "input" not in response.json()["error"]["details"][0]


@pytest.mark.asyncio
async def test_operator_requires_all_target_grants_and_receipts_are_sanitized(
    client, monkeypatch, browser_stub
):
    manager, own, other = await setup_actor(client, "operator", monkeypatch)
    value = await workflow(client)
    bad = await client.post(
        f"/api/v1/workflows/{value['id']}/runs", json=request_for(value, own, other)
    )
    assert bad.status_code == 404
    assert browser_stub.visits == []
    body = request_for(value, own)
    response = await client.post(f"/api/v1/workflows/{value['id']}/runs", json=body)
    assert response.status_code == 202, response.text
    await complete(body["request_id"])
    replay = await client.post(f"/api/v1/workflows/{value['id']}/runs", json=body)
    assert replay.status_code == 202
    assert len(browser_stub.visits) == 1
    history = await client.get(f"/api/v1/workflows/{value['id']}/runs")
    assert history.json()["runs"][0]["state"] == "completed"
    assert "definition-only" not in history.text and "proxy-secret" not in history.text
    assert "url" not in history.text
    await manager.storage.delete_profile_grant("member", own.id)
    assert (await client.get(f"/api/v1/workflows/{value['id']}/runs")).json()["runs"] == []
    assert (
        await client.get(f"/api/v1/workflows/{value['id']}/runs/{body['request_id']}")
    ).status_code == 404


@pytest.mark.asyncio
async def test_viewer_cannot_define_workflows(client, monkeypatch):
    await setup_actor(client, "viewer", monkeypatch)
    assert (await client.post("/api/v1/workflows", json=DEFINITION)).status_code == 403
    assert (await client.get("/api/v1/workflows")).status_code == 403


@pytest.mark.asyncio
async def test_definitions_are_private_even_between_administrators(client, monkeypatch):
    manager, own, _ = await setup_actor(client, "operator", monkeypatch)
    value = await workflow(client)
    await client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert (await client.get("/api/v1/workflows")).json()["workflows"] == []
    assert (
        await client.put(f"/api/v1/workflows/{value['id']}", json={**DEFINITION, "revision": 1})
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/workflows/{value['id']}/runs", json=request_for(value, own))
    ).status_code == 404
    assert (await client.delete(f"/api/v1/workflows/{value['id']}?revision=1")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", ["grant", "session", "role", "identity"])
async def test_revocation_during_launch_stops_before_navigation(
    client, monkeypatch, browser_stub, revoked
):
    manager, own, _ = await setup_actor(client, "operator", monkeypatch)
    value = await workflow(client)
    browser_stub.hold.clear()
    body = request_for(value, own)
    response = await client.post(f"/api/v1/workflows/{value['id']}/runs", json=body)
    assert response.status_code == 202
    try:
        await asyncio.wait_for(browser_stub.launched.wait(), 10)
        if revoked == "grant":
            await manager.storage.delete_profile_grant("member", own.id)
        elif revoked == "session":
            for session in manager.storage.db._connection.execute(
                "SELECT token_hash FROM sessions"
            ).fetchall():
                await manager.storage.delete_session(session["token_hash"])
        elif revoked == "role":
            await manager.storage.update_user_access("member", role="viewer")
        else:
            await manager.storage.delete_user("member")
            await manager.storage.create_user(
                auth.new_user_id(), "member", auth.hash_password(PASSWORD), role="operator"
            )
            await manager.storage.set_profile_grant("member", own.id)
    finally:
        browser_stub.hold.set()
    await complete(body["request_id"])
    saved = dependencies.get_scheduler().workflows.store.run(body["request_id"])
    assert saved["targets"][0]["state"] == "denied"
    assert browser_stub.visits == []
    assert not manager.browser_sessions.active_sessions


@pytest.mark.asyncio
async def test_explicit_schedule_requires_current_targets_and_is_off_by_default(
    client, monkeypatch
):
    _, own, other = await setup_actor(client, "operator", monkeypatch)
    value = await workflow(client)
    assert value["next_run_at"] is None
    assert value["definition"]["schedule_enabled"] is False
    bad = await client.post(
        "/api/v1/workflows",
        json={
            **DEFINITION,
            "schedule_enabled": True,
            "timing": {"kind": "interval", "interval_minutes": 30},
            "scheduled_targets": [{"profile_id": other.id, "row_version": other.row_version}],
        },
    )
    assert bad.status_code == 404
    good = await workflow(
        client,
        schedule_enabled=True,
        timing={"kind": "daily", "at_time": "09:00", "days": [0, 4]},
        scheduled_targets=[{"profile_id": own.id, "row_version": own.row_version}],
    )
    assert good["next_run_at"]
