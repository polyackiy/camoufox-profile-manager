"""Integration tests for the schedule endpoints."""

import pytest

from camoufox_pm.api import dependencies


async def make_profile(client, name="scheduled") -> str:
    response = await client.post("/api/v1/profiles", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


async def make_schedule(client, profile_id: str, **overrides) -> dict:
    body = {
        "profile_id": profile_id,
        "action": "launch",
        "kind": "interval",
        "interval_minutes": 60,
        **overrides,
    }
    response = await client.post("/api/v1/schedules", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_a_schedule_is_created_planned_and_listed(client):
    profile_id = await make_profile(client)
    created = await make_schedule(client, profile_id)

    assert created["profile_name"] == "scheduled"
    assert created["enabled"] is True
    assert created["next_run_at"] is not None
    assert created["last_run"] is None

    listing = await client.get("/api/v1/schedules")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["schedules"][0]["id"] == created["id"]


@pytest.mark.asyncio
async def test_a_schedule_for_a_missing_profile_is_refused(client):
    response = await client.post(
        "/api/v1/schedules",
        json={"profile_id": "ghost", "action": "launch", "kind": "interval", "interval_minutes": 5},
    )
    assert response.status_code == 400
    assert "not found" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_an_incomplete_expression_fails_as_request_validation(client):
    profile_id = await make_profile(client)
    response = await client.post(
        "/api/v1/schedules",
        json={"profile_id": profile_id, "action": "launch", "kind": "daily"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_updating_the_timing_replans_and_renames_do_not(client):
    profile_id = await make_profile(client)
    created = await make_schedule(client, profile_id)
    schedule_id = created["id"]

    switched = await client.put(
        f"/api/v1/schedules/{schedule_id}",
        json={"kind": "daily", "at_time": "09:00", "days": [0, 4]},
    )
    assert switched.status_code == 200, switched.text
    body = switched.json()
    assert body["kind"] == "daily"
    assert body["at_time"] == "09:00"
    assert body["next_run_at"] != created["next_run_at"]

    # Disabling clears the plan; nothing should fire while paused.
    paused = await client.put(f"/api/v1/schedules/{schedule_id}", json={"enabled": False})
    assert paused.json()["enabled"] is False
    assert paused.json()["next_run_at"] is None


@pytest.mark.asyncio
async def test_switching_kind_without_its_field_is_refused(client):
    profile_id = await make_profile(client)
    created = await make_schedule(client, profile_id)

    response = await client.put(f"/api/v1/schedules/{created['id']}", json={"kind": "daily"})
    assert response.status_code == 400
    assert "at_time" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_deleting_a_schedule_and_missing_ids_answer_correctly(client):
    profile_id = await make_profile(client)
    created = await make_schedule(client, profile_id)

    deleted = await client.delete(f"/api/v1/schedules/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True

    assert (await client.get(f"/api/v1/schedules/{created['id']}")).status_code == 404
    assert (await client.delete(f"/api/v1/schedules/{created['id']}")).status_code == 404
    assert (await client.get("/api/v1/schedules/nope/runs")).status_code == 404


@pytest.mark.asyncio
async def test_run_now_records_an_outcome_and_history_serves_it(client, monkeypatch):
    profile_id = await make_profile(client)
    created = await make_schedule(client, profile_id)

    manager = dependencies.get_profile_manager()

    class FakeSession:
        process_id = 4242

        async def terminate(self):
            pass

    async def fake_launch(pid, options, on_exit=None):
        manager.browser_sessions.active_sessions[pid] = FakeSession()
        return FakeSession()

    from camoufox_pm.core import profile_manager as pm_module

    monkeypatch.setattr(manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", lambda *_a, **_k: {})

    ran = await client.post(f"/api/v1/schedules/{created['id']}/run")
    assert ran.status_code == 200, ran.text
    assert ran.json()["outcome"] == "ok"

    # A second run finds the browser open and says so instead of failing.
    again = await client.post(f"/api/v1/schedules/{created['id']}/run")
    assert again.json()["outcome"] == "skipped"

    history = await client.get(f"/api/v1/schedules/{created['id']}/runs")
    assert history.status_code == 200
    outcomes = [run["outcome"] for run in history.json()["runs"]]
    assert outcomes == ["skipped", "ok"]

    # The manual runs did not move the planned next run.
    fetched = await client.get(f"/api/v1/schedules/{created['id']}")
    assert fetched.json()["next_run_at"] == created["next_run_at"]
    assert fetched.json()["last_run"]["outcome"] == "skipped"

    await manager.browser_sessions.close(profile_id)


@pytest.mark.asyncio
async def test_deleting_the_profile_deletes_its_schedules(client):
    profile_id = await make_profile(client)
    created = await make_schedule(client, profile_id)

    assert (await client.delete(f"/api/v1/profiles/{profile_id}")).status_code == 200
    assert (await client.get(f"/api/v1/schedules/{created['id']}")).status_code == 404
