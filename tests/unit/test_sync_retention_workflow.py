"""Owner-only retention decisions cross the real authenticated relay boundary."""

# ruff: noqa: F811
import pytest

from camoufox_pm.sync.engine import EngineError
from tests.unit.test_sync_apply import area  # noqa: F401
from tests.unit.test_sync_runtime import lifecycle_runtime, runtime  # noqa: F401


async def prepare(runtime, area):
    first = await runtime.enable("owner-user", area.profile.id)
    await runtime.handoff("owner-user", area.profile.id)
    return first["checkpoint"]["revision_id"]


async def test_owner_schedule_and_cancel_retains_profile(lifecycle_runtime, area):
    runtime = lifecycle_runtime
    old = await prepare(runtime, area)
    listing = await runtime.retention_list("owner-user", area.profile.id)
    assert [item["revision_id"] for item in listing["revisions"]] == [old]
    result = await runtime.retention_schedule(
        "owner-user", area.profile.id, revisions=[old], acknowledge_cleanup=True
    )
    assert result["state"] == "scheduled"
    again = await runtime.retention_schedule(
        "owner-user", area.profile.id, revisions=[old], acknowledge_cleanup=True
    )
    assert again == result
    cancelled = await runtime.retention_cancel(
        "owner-user", area.profile.id, decision_id=result["decision"]["body"]["decision_id"]
    )
    assert cancelled["state"] == "cancelled"
    assert (await runtime.retention_list("owner-user", area.profile.id))["decisions"][0][
        "state"
    ] == "cancelled"
    assert (area.old / "cookies.sqlite").read_bytes() == b"old local cookies"


async def test_current_revision_and_missing_confirmation_cannot_schedule(lifecycle_runtime, area):
    runtime = lifecycle_runtime
    state = await runtime.enable("owner-user", area.profile.id)
    with pytest.raises(EngineError):
        await runtime.retention_schedule(
            "owner-user",
            area.profile.id,
            revisions=[state["checkpoint"]["revision_id"]],
            acknowledge_cleanup=True,
        )
    with pytest.raises(EngineError):
        await runtime.retention_schedule(
            "owner-user",
            area.profile.id,
            revisions=[state["checkpoint"]["revision_id"]],
            acknowledge_cleanup=False,
        )
    assert not runtime.service.control.list("retention/")


async def test_demoted_or_non_authority_cannot_schedule(lifecycle_runtime, area):
    runtime = lifecycle_runtime
    old = await prepare(runtime, area)
    record, owner = runtime.service._read("owner-user")
    runtime.service.control.update(
        record.key,
        owner.model_copy(update={"authority": False}).model_dump(mode="json"),
        record.revision,
    )
    with pytest.raises(EngineError):
        await runtime.retention_schedule(
            "owner-user", area.profile.id, revisions=[old], acknowledge_cleanup=True
        )
    assert not runtime.service.control.list("retention/")


async def test_cancellation_needs_exact_signed_acknowledgement(
    lifecycle_runtime, area, monkeypatch
):
    from camoufox_pm.sync.client import RelayClient

    runtime = lifecycle_runtime
    old = await prepare(runtime, area)
    scheduled = await runtime.retention_schedule(
        "owner-user", area.profile.id, revisions=[old], acknowledge_cleanup=True
    )
    decision_id = scheduled["decision"]["body"]["decision_id"]
    original = RelayClient.retention_cancel
    monkeypatch.setattr(RelayClient, "retention_cancel", lambda *_: scheduled)
    with pytest.raises(EngineError, match="pending"):
        await runtime.retention_cancel("owner-user", area.profile.id, decision_id=decision_id)
    assert runtime.service.control.get("retention/" + decision_id).payload["cancellation"]
    listing = await runtime.retention_list("owner-user", area.profile.id)
    assert listing["decisions"][0]["state"] == "cancellation_pending"
    monkeypatch.setattr(RelayClient, "retention_cancel", original)
    assert (await runtime.retention_cancel("owner-user", area.profile.id, decision_id=decision_id))[
        "state"
    ] == "cancelled"
