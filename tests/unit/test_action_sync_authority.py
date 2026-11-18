"""Action synchronization requires the original administrator and live cohort leases."""

from datetime import datetime, timedelta

import pytest

from camoufox_pm.api.principal import Principal
from camoufox_pm.core.action_sync_authority import ActionSyncAuthority
from camoufox_pm.core.auth import hash_token
from camoufox_pm.core.browser_session import BrowserSession
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.models import BrowserSettings, Profile

HOLDER = "manager-holder"
ACTOR = Principal("session", "Admin")
SESSION_HASH = hash_token("test-session-token")


@pytest.fixture
async def cohort(storage):
    await storage.create_user("admin-id", "Admin", "unused-password-hash")
    await storage.create_user("backup-id", "Backup", "unused-password-hash")
    await storage.create_session(SESSION_HASH, "admin-id", datetime.now() + timedelta(hours=1))
    sessions = tuple(BrowserSession(name, None) for name in ("leader", "first", "second"))
    for session in sessions:
        profile = Profile(
            id=session.profile_id, name=session.profile_id, browser_settings=BrowserSettings()
        )
        await storage.save_profile(profile)
        assert await storage.acquire_lease(profile.id, HOLDER, 120)
    return sessions


async def _authenticate(storage):
    authority = await ActionSyncAuthority.authenticate(storage, HOLDER, ACTOR, SESSION_HASH)
    assert authority is not None
    return authority


async def _bind(storage, cohort):
    authority = await _authenticate(storage)
    bound = await authority.bind_sessions(cohort)
    assert bound is not None
    return bound


async def test_authorizes_the_exact_bound_cohort(storage, cohort):
    authority = await _authenticate(storage)
    assert not await authority.authorize(cohort)
    bound = await authority.bind_sessions(cohort)
    assert bound is not None
    assert await bound.authorize(cohort)
    assert not await bound.authorize(cohort[:-1])
    assert not await bound.authorize((*cohort[:-1], BrowserSession("second", None)))
    cohort[1].profile_id = "second"
    assert not await bound.authorize(cohort)


@pytest.mark.parametrize(
    ("principal", "session_hash"),
    [
        (Principal("legacy_api_key", "Admin"), SESSION_HASH),
        (Principal("anonymous_local", "Admin"), SESSION_HASH),
        (Principal("system", "Admin"), SESSION_HASH),
        (None, SESSION_HASH),
        (ACTOR, None),
        (ACTOR, hash_token("unregistered-test-token")),
        (Principal("session"), SESSION_HASH),
        (Principal("session", "Backup"), SESSION_HASH),
        (Principal("session", "Admin", "operator"), SESSION_HASH),
    ],
)
async def test_authentication_requires_the_administrator_cookie_identity(
    storage, cohort, principal, session_hash
):
    assert await ActionSyncAuthority.authenticate(storage, HOLDER, principal, session_hash) is None


async def test_cookie_revocation_is_visible_from_another_connection(storage, cohort, tmp_path):
    authority = await _authenticate(storage)
    bound = await authority.bind_sessions(cohort)
    assert bound is not None
    other = StorageManager(str(tmp_path / "test.db"))
    await other.initialize()
    try:
        assert await other.delete_session(SESSION_HASH)
        assert not await authority.is_authenticated()
        assert await authority.bind_sessions(cohort) is None
        assert not await bound.authorize(cohort)
    finally:
        await other.close()


async def test_role_demotion_ends_authority_even_if_the_cookie_row_survives(storage, cohort):
    bound = await _bind(storage, cohort)
    assert await storage.update_user_access("Admin", role="operator")
    # The normal demotion revokes sessions too. Restore the old binding to
    # independently exercise the requirement to consult the current role.
    await storage.create_session(SESSION_HASH, "admin-id", datetime.now() + timedelta(hours=1))
    assert await ActionSyncAuthority.authenticate(storage, HOLDER, ACTOR, SESSION_HASH) is None
    assert not await bound.authorize(cohort)


async def test_recreating_the_same_name_does_not_inherit_bound_authority(storage, cohort):
    bound = await _bind(storage, cohort)
    assert await storage.delete_user("Admin")
    await storage.create_user("replacement-id", "Admin", "unused-password-hash")
    # Deliberately reuse the exact hash with the replacement to isolate the
    # immutable ID check from the token and name checks.
    await storage.create_session(
        SESSION_HASH, "replacement-id", datetime.now() + timedelta(hours=1)
    )
    assert not await bound.is_authenticated()
    assert not await bound.authorize(cohort)


async def test_switching_the_cookie_user_ends_authority(storage, cohort):
    bound = await _bind(storage, cohort)
    assert await storage.delete_session(SESSION_HASH)
    await storage.create_session(SESSION_HASH, "backup-id", datetime.now() + timedelta(hours=1))
    assert not await bound.authorize(cohort)


@pytest.mark.parametrize("expires_at", ["2000-01-01T00:00:00", "9999-01-01", "invalid"])
async def test_invalid_or_expired_cookie_ends_authority(storage, cohort, expires_at):
    bound = await _bind(storage, cohort)
    with storage.db._connection as connection:
        connection.execute(
            "UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (expires_at, SESSION_HASH)
        )
    assert not await bound.is_authenticated()
    assert not await bound.authorize(cohort)


@pytest.mark.parametrize(
    ("holder", "lock_expires"),
    [
        ("other-holder", "9999-01-01 00:00:00"),
        (None, None),
        (HOLDER, "2000-01-01 00:00:00"),
        (HOLDER, "9999-02-30 00:00:00"),
        (HOLDER, "invalid"),
    ],
)
async def test_each_member_requires_an_unexpired_lease_from_this_manager(
    storage, cohort, holder, lock_expires
):
    authority = await _authenticate(storage)
    bound = await authority.bind_sessions(cohort)
    assert bound is not None
    with storage.db._connection as connection:
        connection.execute(
            "UPDATE profiles SET locked_by = ?, lock_expires = ? WHERE id = 'second'",
            (holder, lock_expires),
        )
    assert await authority.bind_sessions(cohort) is None
    assert not await bound.authorize(cohort)


async def test_trashed_profile_is_denied_even_if_its_lease_survives(storage, cohort):
    bound = await _bind(storage, cohort)
    assert await storage.set_profile_trashed("leader", True)
    assert not await bound.authorize(cohort)
