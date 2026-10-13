"""Automatic transmission never guesses a locked vault password."""
# ruff: noqa: F811

from camoufox_pm.core.sync_background import drain_sync_queue
from camoufox_pm.core.sync_queue import queue_closed_profile
from tests.unit.test_sync_apply import area  # noqa: F401
from tests.unit.test_sync_runtime import runtime  # noqa: F401


async def test_native_queue_transmits_and_releases(runtime, area):
    runtime, _ = runtime
    await runtime.enable("owner-user", area.profile.id)
    await queue_closed_profile(area.manager, area.profile.id)
    assert (
        await drain_sync_queue(area.manager, area.maintenance, runtime_factory=lambda *_: runtime)
        == 1
    )
    binding = runtime.service.control.get("profile/" + area.profile.id).payload
    assert binding["phase"] == "released"
    assert (
        await drain_sync_queue(area.manager, area.maintenance, runtime_factory=lambda *_: runtime)
        == 0
    )


async def test_encrypted_vault_waits_for_explicit_unlock(runtime, area, monkeypatch):
    runtime, _ = runtime
    await runtime.enable("owner-user", area.profile.id)
    await queue_closed_profile(area.manager, area.profile.id)
    record, owner = runtime.service._read("owner-user")
    runtime.service.control.update(
        "installation",
        owner.model_copy(update={"vault_kind": "encrypted_file"}).model_dump(mode="json"),
        record.revision,
    )

    async def forbidden(*_, **__):
        raise AssertionError("Must not try passwords in the background")

    monkeypatch.setattr(runtime, "handoff", forbidden)
    assert (
        await drain_sync_queue(area.manager, area.maintenance, runtime_factory=lambda *_: runtime)
        == 0
    )
    assert runtime.service.control.get("profile/" + area.profile.id).payload["handoff"]


async def test_completed_and_failing_queue_entries_do_not_starve_new_work(
    runtime, area, monkeypatch
):
    runtime, _ = runtime
    control = runtime.service.control
    for index in range(64):
        control.create(f"runtime/dirty/done{index:03}", {"phase": "complete"})
        control.create(f"runtime/dirty/fail{index:03}", {"phase": "pending"})
        control.create(f"profile/fail{index:03}", {"handoff": True})
    control.create("runtime/dirty/healthy", {"phase": "pending"})
    control.create("profile/healthy", {"handoff": True})
    seen = []

    async def handoff(user_id, local_id):
        seen.append(local_id)
        if local_id.startswith("fail"):
            raise ValueError("offline")
        record = control.get("runtime/dirty/" + local_id)
        control.update(record.key, {**record.payload, "phase": "complete"}, record.revision)

    monkeypatch.setattr(runtime, "handoff", handoff)
    for _ in range(9):
        await drain_sync_queue(area.manager, area.maintenance, runtime_factory=lambda *_: runtime)
    assert "healthy" in seen
    assert not any(value.startswith("done") for value in seen)
