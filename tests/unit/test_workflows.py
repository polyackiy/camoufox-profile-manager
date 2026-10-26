"""Execution boundaries: ownership, cancellation, authorization and durable receipts."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from camoufox_pm.api.principal import Principal
from camoufox_pm.core import profile_manager as pm_module
from camoufox_pm.core.leases import ProfileLocked
from camoufox_pm.core.workflow_models import WorkflowDefinition, WorkflowStart, WorkflowStep
from camoufox_pm.core.workflow_store import WorkflowConflict
from camoufox_pm.core.workflows import WorkflowEngine, workflow_owner


@pytest.fixture
async def runtime(profile_manager, monkeypatch):
    events = []
    hold_launch = asyncio.Event()
    hold_launch.set()
    hold_navigation = asyncio.Event()
    hold_navigation.set()
    navigation_started = asyncio.Event()
    launched = asyncio.Event()
    failures = set()

    class Page:
        def __init__(self, profile_id):
            self.profile_id = profile_id

        async def goto(self, url, **kwargs):
            events.append(("navigate", self.profile_id, url))
            navigation_started.set()
            await hold_navigation.wait()
            if self.profile_id in failures:
                raise RuntimeError("private-cookie-password https://secret.example/?token=SECRET")
            return SimpleNamespace(status=200)

    class Context:
        def __init__(self, profile_id):
            self.profile_id = profile_id

        async def new_page(self):
            events.append(("page", self.profile_id))
            return Page(self.profile_id)

    async def launch(profile_id, options, on_exit=None):
        events.append(("launch", profile_id, options))
        launched.set()
        await hold_launch.wait()

        async def terminate():
            events.append(("close", profile_id))

        session = SimpleNamespace(
            process_id=None, context=Context(profile_id), terminate=terminate, on_exit=None
        )
        profile_manager.browser_sessions.active_sessions[profile_id] = session
        return session

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", lambda *_a, **_k: {})
    engine = WorkflowEngine(profile_manager.storage, profile_manager)
    value = SimpleNamespace(
        engine=engine,
        manager=profile_manager,
        events=events,
        hold_launch=hold_launch,
        hold_navigation=hold_navigation,
        navigation_started=navigation_started,
        launched=launched,
        failures=failures,
    )
    yield value
    hold_launch.set()
    hold_navigation.set()
    await engine.stop()
    await profile_manager.close_all_browsers()


async def definition(runtime, **overrides):
    definition = WorkflowDefinition(
        name="Local demo",
        steps=[
            {"name": "Open", "action": "launch"},
            {
                "name": "Visit",
                "action": "navigate",
                "url": "https://example.test/?explicit=configuration",
            },
            {"name": "Finish", "action": "close"},
        ],
        **overrides,
    )
    return runtime.engine.store.save(
        definition, await workflow_owner(runtime.manager.storage, Principal("anonymous_local"))
    )


async def targets(runtime, count=1):
    profiles = [await runtime.manager.create_profile(name=f"Demo {i}") for i in range(count)]
    return [{"profile_id": profile.id, "row_version": profile.row_version} for profile in profiles]


async def start(runtime, value, selected=None, **kwargs):
    request = WorkflowStart(
        request_id=str(uuid4()),
        revision=value["revision"],
        targets=selected or await targets(runtime),
        **kwargs,
    )
    run = await runtime.engine.submit(value, request)
    return run, request


async def finished(runtime, run):
    task = runtime.engine.tasks.get(run["id"])
    if task:
        await asyncio.wait_for(task, 5)
    return runtime.engine.store.run(run["id"])


async def observed_navigations(runtime, count):
    async def observed():
        while sum(event[0] == "navigate" for event in runtime.events) < count:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(observed(), 10)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,x",
        "about:config",
        "http://user:private@proxy.test/",
        "http://example.test:bad",
        "http://example.test/\n",
        "http://example.test\\@other.test/",
    ],
)
def test_destination_validation_refuses_executable_local_and_credential_urls(url):
    with pytest.raises(ValidationError):
        WorkflowStep(name="Visit", action="navigate", url=url)


@pytest.mark.parametrize(
    "changes",
    [
        {"concurrency": 5},
        {"concurrency": True},
        {"steps": [{"name": "X", "action": "shell"}]},
        {"steps": [{"name": "X", "action": "close"}, {"name": "Y", "action": "launch"}]},
        {"schedule_enabled": True},
        {
            "steps": [
                {"name": "X", "action": "launch", "url": "https://example.test"},
                {"name": "Y", "action": "close"},
            ]
        },
    ],
)
def test_bounded_declarations_refuse_unknown_operations_and_launch_arguments(changes):
    with pytest.raises(ValidationError):
        WorkflowDefinition(
            **{
                "name": "Demo",
                "steps": [
                    {"name": "Start", "action": "launch"},
                    {"name": "Stop", "action": "close"},
                ],
                **changes,
            }
        )


async def test_ready_context_navigation_then_close_and_receipt_redaction(runtime):
    value = await definition(runtime)
    run, request = await start(runtime, value)
    done = await finished(runtime, run)
    assert done["state"] == "completed"
    assert [event[0] for event in runtime.events] == ["launch", "page", "navigate", "close"]
    assert "args" not in runtime.events[0][2]
    assert "example.test" not in json.dumps(done)
    assert "url" not in json.dumps(done)
    assert not runtime.manager.browser_sessions.active_sessions
    assert (await runtime.manager.storage.get_lease(request.targets[0].profile_id))[0] is None
    assert [step["state"] for step in done["targets"][0]["steps"]] == ["completed"] * 3


async def test_lost_response_idempotency_and_changed_intent_conflict(runtime):
    value = await definition(runtime)
    run, request = await start(runtime, value)
    await finished(runtime, run)
    await runtime.engine.submit(value, request)
    assert sum(event[0] == "launch" for event in runtime.events) == 1
    changed = WorkflowStart(**{**request.model_dump(), "targets": await targets(runtime)})
    with pytest.raises(WorkflowConflict):
        await runtime.engine.submit(value, changed)


async def test_cancel_drains_accepted_launch_and_never_navigates(runtime):
    value = await definition(runtime)
    runtime.hold_launch.clear()
    run, _ = await start(runtime, value, await targets(runtime, 2))
    await asyncio.wait_for(runtime.launched.wait(), 10)
    runtime.engine.cancel(run["id"])
    assert not runtime.engine.tasks[run["id"]].done()
    runtime.hold_launch.set()
    done = await finished(runtime, run)
    assert done["state"] == "cancelled"
    assert [event[0] for event in runtime.events] == ["launch", "close"]
    assert not runtime.manager.browser_sessions.active_sessions


async def test_cancel_navigation_waits_for_it_and_does_not_start_the_next_target(runtime):
    value = await definition(runtime)
    runtime.hold_navigation.clear()
    run, _ = await start(runtime, value, await targets(runtime, 3))
    await asyncio.wait_for(runtime.navigation_started.wait(), 10)
    runtime.engine.cancel(run["id"])
    assert not runtime.engine.tasks[run["id"]].done()
    runtime.hold_navigation.set()
    done = await finished(runtime, run)
    assert done["state"] == "cancelled"
    assert sum(event[0] == "launch" for event in runtime.events) == 1


async def test_browser_and_edit_admission_cannot_take_over_a_workflow(runtime):
    value = await definition(runtime)
    runtime.hold_navigation.clear()
    run, request = await start(runtime, value)
    await asyncio.wait_for(runtime.navigation_started.wait(), 10)
    profile_id = request.targets[0].profile_id
    with pytest.raises(ProfileLocked):
        await runtime.manager.launch_browser(profile_id)
    with pytest.raises(ProfileLocked):
        await runtime.manager.update_profile(profile_id, {"name": "Racing edit"})
    runtime.hold_navigation.set()
    assert (await finished(runtime, run))["state"] == "completed"


async def test_preexisting_browser_is_not_adopted_or_closed(runtime):
    value = await definition(runtime)
    selected = await targets(runtime)
    await runtime.manager.launch_browser(selected[0]["profile_id"])
    original = runtime.manager.browser_sessions.active_sessions[selected[0]["profile_id"]]
    run, _ = await start(runtime, value, selected)
    assert (await finished(runtime, run))["targets"][0]["state"] == "skipped"
    assert runtime.manager.browser_sessions.active_sessions[selected[0]["profile_id"]] is original
    assert [event[0] for event in runtime.events] == ["launch"]


async def test_failure_history_is_sanitized_and_retry_never_repeats_successful_targets(runtime):
    value = await definition(runtime)
    selected = await targets(runtime, 2)
    runtime.failures.add(selected[1]["profile_id"])
    run, _ = await start(runtime, value, selected)
    done = await finished(runtime, run)
    assert done["state"] == "partial"
    assert "SECRET" not in json.dumps(done)
    assert "private-cookie" not in json.dumps(done)
    assert [step["state"] for step in done["targets"][1]["steps"]] == [
        "completed",
        "failed",
        "skipped",
    ]
    with pytest.raises(WorkflowConflict):
        await start(runtime, value, selected, retry_of=run["id"], replay_confirmed=True)
    runtime.failures.clear()
    retry, _ = await start(runtime, value, selected[1:], retry_of=run["id"], replay_confirmed=True)
    assert (await finished(runtime, retry))["state"] == "completed"
    assert (
        sum(
            event[0] == "launch" and event[1] == selected[0]["profile_id"]
            for event in runtime.events
        )
        == 1
    )


async def test_new_account_revokes_anonymous_execution_before_navigation(runtime):
    value = await definition(runtime)
    runtime.hold_launch.clear()
    run, _ = await start(runtime, value)
    await asyncio.wait_for(runtime.launched.wait(), 10)
    await runtime.manager.storage.create_user(str(uuid4()), "owner", "unused-test-hash")
    runtime.hold_launch.set()
    done = await finished(runtime, run)
    assert done["targets"][0]["state"] == "denied"
    assert [event[0] for event in runtime.events] == ["launch", "close"]


async def test_concurrency_is_bounded_and_other_instance_cannot_reconcile_live_jobs(runtime):
    value = await definition(runtime, concurrency=2)
    runtime.hold_navigation.clear()
    run, _ = await start(runtime, value, await targets(runtime, 5))
    await asyncio.wait_for(runtime.navigation_started.wait(), 10)
    await observed_navigations(runtime, 2)
    assert sum(event[0] == "navigate" for event in runtime.events) == 2
    second = WorkflowEngine(runtime.manager.storage, runtime.manager)
    with pytest.raises(WorkflowConflict, match="unavailable"):
        await second.start()
    assert runtime.engine.store.run(run["id"])["state"] == "running"
    await second.stop()
    runtime.hold_navigation.set()
    assert (await finished(runtime, run))["state"] == "completed"


async def test_profile_change_before_dispatch_is_refused(runtime):
    value = await definition(runtime)
    selected = await targets(runtime)
    await runtime.manager.update_profile(selected[0]["profile_id"], {"name": "Changed"})
    with pytest.raises(WorkflowConflict, match="profile changed"):
        await start(runtime, value, selected)
    assert runtime.events == []


async def test_startup_marks_active_receipts_interrupted_without_replay(runtime):
    value = await definition(runtime)
    run_id = str(uuid4())
    runtime.engine.store.insert_run(
        {
            "id": run_id,
            "workflow_id": value["id"],
            "revision": 1,
            "state": "running",
            "targets": [
                {
                    "profile_id": "old",
                    "state": "running",
                    "steps": [{"state": "completed"}, {"state": "running"}, {"state": "queued"}],
                }
            ],
        }
    )
    await runtime.engine.start()
    done = runtime.engine.store.run(run_id)
    assert done["state"] == "interrupted"
    assert [step["state"] for step in done["targets"][0]["steps"]] == [
        "completed",
        "interrupted",
        "interrupted",
    ]
    assert runtime.events == []


async def test_timing_missed_startup_is_recorded_once_and_next_occurrence_runs(runtime):
    selected = await targets(runtime)
    value = await definition(
        runtime,
        timing={"kind": "interval", "interval_minutes": 5},
        schedule_enabled=True,
        scheduled_targets=selected,
    )
    runtime.engine.store.advance(value, (datetime.now() - timedelta(hours=3)).isoformat())
    await runtime.engine.start()
    assert [run["state"] for run in runtime.engine.store.runs(value["id"])] == ["missed"]
    assert runtime.events == []
    await runtime.engine.tick(datetime.now() + timedelta(minutes=6))
    active = next(run for run in runtime.engine.store.runs(value["id"]) if run["state"] == "queued")
    assert (await finished(runtime, active))["state"] == "completed"


async def test_stale_definition_cannot_insert_a_run_receipt(runtime):
    value = await definition(runtime)
    current = WorkflowDefinition(**value["definition"])
    runtime.engine.store.save(current, value["owner"], (value["id"], 1))
    with pytest.raises(WorkflowConflict):
        await start(runtime, value)
    assert runtime.events == []


async def test_pruned_receipt_still_prevents_implicit_replay(runtime):
    value = await definition(runtime)
    first, request = await start(runtime, value)
    await finished(runtime, first)
    for _ in range(30):
        run, _ = await start(runtime, value, [target.model_dump() for target in request.targets])
        await finished(runtime, run)
    assert runtime.engine.store.run(first["id"]) is None
    assert len(runtime.engine.store.runs(value["id"])) == 30
    count = len(runtime.events)
    with pytest.raises(WorkflowConflict, match="history expired"):
        await runtime.engine.submit(value, request)
    assert len(runtime.events) == count


async def test_global_slots_and_active_run_limit_bound_multiple_workflows(runtime):
    runtime.hold_navigation.clear()
    for _ in range(4):
        value = await definition(runtime, concurrency=2)
        await start(runtime, value, await targets(runtime, 2))
    await observed_navigations(runtime, 4)
    assert sum(event[0] == "navigate" for event in runtime.events) == 4
    value = await definition(runtime)
    with pytest.raises(WorkflowConflict, match="Four workflow"):
        await start(runtime, value)
    runtime.hold_navigation.set()
    await asyncio.gather(*list(runtime.engine.tasks.values()))


async def test_concurrent_submissions_cannot_exceed_installation_run_limit(runtime, monkeypatch):
    runtime.hold_navigation.clear()
    values = [await definition(runtime) for _ in range(8)]
    selected = [await targets(runtime) for _ in values]
    authorize = runtime.engine.authorized
    pending = 0
    barrier = asyncio.Event()

    async def delayed_authorization(*args):
        nonlocal pending
        pending += 1
        if pending >= 8:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), 5)
        return await authorize(*args)

    monkeypatch.setattr(runtime.engine, "authorized", delayed_authorization)
    replies = await asyncio.wait_for(
        asyncio.gather(
            *(start(runtime, value, rows) for value, rows in zip(values, selected, strict=True)),
            return_exceptions=True,
        ),
        10,
    )
    assert sum(isinstance(reply, WorkflowConflict) for reply in replies) == 4
    assert len(runtime.engine.tasks) == 4
    runtime.hold_navigation.set()
    await asyncio.gather(*list(runtime.engine.tasks.values()))


async def test_definition_limit_is_enforced_in_store_for_concurrent_api_admission(runtime):
    for _ in range(100):
        await definition(runtime)
    with pytest.raises(WorkflowConflict, match="100 workflows"):
        await definition(runtime)
    assert len(runtime.engine.store.list()) == 100


async def test_maintenance_cannot_begin_until_cancelled_actions_drain(runtime, tmp_path):
    from camoufox_pm.core.maintenance import MaintenanceBlocked, MaintenanceGate

    gate = MaintenanceGate(tmp_path / "maintenance.sqlite3")
    runtime.engine.maintenance_gate = gate
    runtime.hold_navigation.clear()
    value = await definition(runtime)
    run, _ = await start(runtime, value)
    await asyncio.wait_for(runtime.navigation_started.wait(), 10)
    with pytest.raises(MaintenanceBlocked):
        gate.acquire("update-owner")
    runtime.engine.cancel(run["id"])
    with pytest.raises(MaintenanceBlocked):
        gate.acquire("update-owner")
    runtime.hold_navigation.set()
    assert (await finished(runtime, run))["state"] == "cancelled"
    gate.acquire("update-owner")
    gate.release("update-owner")


async def test_cancelled_wait_is_not_reported_as_completed(runtime):
    value = await definition(runtime)
    value["definition"]["steps"][1] = {"name": "Wait", "action": "wait", "seconds": 60}
    runtime.engine.store.save(
        WorkflowDefinition(**value["definition"]), value["owner"], (value["id"], 1)
    )
    value = runtime.engine.store.get(value["id"])
    run, _ = await start(runtime, value)

    async def waiting():
        while runtime.engine.store.run(run["id"])["targets"][0]["steps"][1]["state"] != "running":
            await asyncio.sleep(0.001)

    await asyncio.wait_for(waiting(), 3)
    runtime.engine.cancel(run["id"])
    done = await finished(runtime, run)
    assert [step["state"] for step in done["targets"][0]["steps"]] == [
        "completed",
        "cancelled",
        "cancelled",
    ]
    assert not runtime.manager.browser_sessions.active_sessions


async def test_launch_deadline_preserves_task_admission_and_releases_failed_lease(runtime):
    runtime.engine.launch_timeout = 0.01
    runtime.hold_launch.clear()
    value = await definition(runtime)
    run, request = await start(runtime, value)
    done = await finished(runtime, run)
    assert runtime.launched.is_set(), "Launch must run inside its existing admission task"
    assert done["targets"][0]["state"] == "failed"
    assert (await runtime.manager.storage.get_lease(request.targets[0].profile_id))[0] is None
    assert not runtime.manager.browser_sessions.active_sessions
