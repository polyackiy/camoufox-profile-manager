"""A closed browser cannot be reopened between durable handoff and upload."""
# ruff: noqa: F811

from camoufox_pm.core.sync_queue import queue_closed_profile
from camoufox_pm.sync.engine import may_launch_from_control
from tests.unit.test_sync_apply import area  # noqa: F401
from tests.unit.test_sync_runtime import runtime  # noqa: F401


async def test_close_queue_blocks_launch_without_reading_vault(runtime, area, monkeypatch):
    runtime, _ = runtime
    await runtime.enable("owner-user", area.profile.id)
    assert may_launch_from_control(runtime.service, "owner-user", area.profile.id)

    def forbidden(*args, **kwargs):
        raise AssertionError("Closing a browser must not unlock keys")

    monkeypatch.setattr(runtime.service, "_vault", forbidden)
    assert await queue_closed_profile(area.manager, area.profile.id)
    assert not may_launch_from_control(runtime.service, "owner-user", area.profile.id)
    assert (
        runtime.service.control.get("runtime/dirty/" + area.profile.id).payload["reason"]
        == "browser_closed"
    )
    assert await queue_closed_profile(area.manager, area.profile.id)


async def test_unsynchronized_close_creates_no_control_state(area):
    assert not await queue_closed_profile(area.manager, area.profile.id)
    assert not (area.manager.data_dir / "sync-control").exists()
