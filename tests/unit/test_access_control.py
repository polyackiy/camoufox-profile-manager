"""Grants, account recovery invariants and persistent safe activity."""

import pytest

from camoufox_pm.api.principal import Principal
from camoufox_pm.core.access import AccessControl
from camoufox_pm.core.models import Profile


@pytest.mark.asyncio
async def test_roles_grants_and_revocation(storage):
    await storage.create_user("a", "admin", "hash")
    await storage.create_user("v", "viewer", "hash", "viewer")
    await storage.create_user("o", "operator", "hash", "operator")
    profile = Profile(name="assigned")
    await storage.save_profile(profile)
    access = AccessControl(storage)
    viewer = Principal("session", "viewer", "viewer")
    operator = Principal("session", "operator", "operator")
    assert not await access.can_profile(viewer, profile.id)
    await storage.set_profile_grant("viewer", profile.id, True)
    await storage.set_profile_grant("operator", profile.id)
    assert await access.can_profile(viewer, profile.id)
    assert await access.can_profile(viewer, profile.id, "export")
    assert not await access.can_profile(viewer, profile.id, "launch")
    assert await access.can_profile(operator, profile.id, "launch")
    assert not await access.can_profile(operator, profile.id, "export")
    await storage.delete_profile_grant("operator", profile.id)
    assert not await access.can_profile(operator, profile.id)
    await storage.delete_user("viewer")
    assert not await access.can_profile(viewer, profile.id)


@pytest.mark.asyncio
async def test_last_admin_and_atomic_audit(storage):
    actor = Principal("session", "admin")
    await storage.create_user("a", "admin", "sensitive-hash", actor=actor)
    with pytest.raises(ValueError, match="last administrator"):
        await storage.delete_user("admin", actor=actor)
    with pytest.raises(ValueError, match="last administrator"):
        await storage.update_user_access("admin", role="viewer", actor=actor)
    assert (await storage.get_user_by_username("admin"))["role"] == "administrator"
    rows = await storage.list_audit()
    assert len(rows) == 1
    assert rows[0]["action"] == "user.create"
    assert "sensitive-hash" not in str(rows)
    await storage.create_user("b", "other", "hash")
    assert await storage.delete_user("admin", actor=actor)
    assert (await storage.list_audit())[0]["action"] == "user.delete"


@pytest.mark.asyncio
async def test_schedule_authority_identity_is_persisted(storage):
    from camoufox_pm.core.models import Schedule

    profile = Profile(name="scheduled")
    await storage.save_profile(profile)
    schedule = Schedule(
        profile_id=profile.id,
        action="launch",
        kind="interval",
        interval_minutes=60,
        actor_kind="session",
        actor_username="operator",
        actor_user_id="immutable-user-id",
    )
    await storage.save_schedule(schedule)
    loaded = await storage.get_schedule(schedule.id)
    assert loaded.actor_user_id == "immutable-user-id"
    assert loaded.actor_username == "operator"


@pytest.mark.asyncio
async def test_activity_retention_is_bounded_and_filter_count_matches(storage):
    connection = storage.db._connection
    connection.executemany(
        "INSERT INTO audit_events(actor_kind,actor_username,action,object_id,result,created_at) VALUES (?,?,?,?,?,?)",
        [("system", None, "old", None, "success", "2026-10-13T00:00:00+00:00")] * 10005,
    )
    connection.commit()
    await storage.append_audit(Principal("system"), "new", "abcdefgh", "failure")
    assert await storage.count_audit() == 10000
    assert await storage.count_audit(action="new", result="failure") == 1
    latest = await storage.list_audit(limit=1)
    assert latest[0]["action"] == "new"
