"""Profile leases: one instance at a time may drive a profile.

The storage tests below drive two ``StorageManager`` objects over one database
file — two SQLite connections, which is what two processes on one machine are.
The manager tests then check that the launch, close, export and shutdown paths
take and hand back the lease at the right moments.
"""

import asyncio

import pytest

from camoufox_pm.config import Settings
from camoufox_pm.core import profile_manager as pm_module
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.leases import ProfileLocked, lease_expired, make_lease_holder
from camoufox_pm.core.models import BrowserSettings, Profile

OTHER = "other-host:999:c0ffee"
TTL = 120


async def _profile(storage: StorageManager, name: str = "p") -> Profile:
    profile = Profile(id=f"pf-{name}", name=name, browser_settings=BrowserSettings())
    await storage.save_profile(profile)
    return profile


def _expire_lease(storage: StorageManager, profile_id: str) -> None:
    """Backdate a lease's expiry.

    Written directly rather than by waiting: the alternative is a test that
    sleeps out a real TTL, and SQLite's ``datetime()`` only has one-second
    resolution, so a zero-second lease would be a coin flip inside its own
    first second.
    """
    storage.db._connection.execute(  # noqa: SLF001 - no public way to forge a clock
        "UPDATE profiles SET lock_expires = datetime('now', '-10 seconds') WHERE id = ?",
        (profile_id,),
    )
    storage.db._connection.commit()  # noqa: SLF001


# -- The lease itself ------------------------------------------------------------


async def test_a_free_profile_can_be_leased(storage):
    profile = await _profile(storage)

    assert await storage.acquire_lease(profile.id, "me", TTL) is True
    assert (await storage.get_lease(profile.id))[0] == "me"


async def test_a_leased_profile_is_refused_to_everyone_else(storage):
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, "me", TTL)

    assert await storage.acquire_lease(profile.id, OTHER, TTL) is False
    # And the refusal left the lease exactly as it was.
    assert (await storage.get_lease(profile.id))[0] == "me"


async def test_a_lease_is_visible_to_another_connection(storage, tmp_path):
    """The point of the whole feature: a second instance sees the first's lease."""
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, "me", TTL)

    second_instance = StorageManager(str(tmp_path / "test.db"))
    await second_instance.initialize()
    try:
        assert await second_instance.acquire_lease(profile.id, OTHER, TTL) is False
        assert (await second_instance.get_lease(profile.id))[0] == "me"
    finally:
        await second_instance.close()


async def test_an_expired_lease_is_taken_over(storage):
    """A machine that died without releasing must not lock a profile forever."""
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, OTHER, TTL)
    _expire_lease(storage, profile.id)

    assert await storage.acquire_lease(profile.id, "me", TTL) is True
    assert (await storage.get_lease(profile.id))[0] == "me"


async def test_re_acquiring_your_own_lease_succeeds(storage):
    """A restart on the same host must not have to wait out a TTL it set itself."""
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, "me", TTL)

    assert await storage.acquire_lease(profile.id, "me", TTL) is True


async def test_acquiring_a_profile_that_does_not_exist_fails(storage):
    assert await storage.acquire_lease("ghost", "me", TTL) is False
    assert await storage.get_lease("ghost") is None


async def test_releasing_only_clears_your_own_lease(storage):
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, OTHER, TTL)

    assert await storage.release_lease(profile.id, "me") is False
    assert (await storage.get_lease(profile.id))[0] == OTHER


async def test_renewal_extends_only_your_own_leases(storage):
    mine = await _profile(storage, "mine")
    theirs = await _profile(storage, "theirs")
    await storage.acquire_lease(mine.id, "me", TTL)
    await storage.acquire_lease(theirs.id, OTHER, TTL)

    assert await storage.renew_lease([mine.id, theirs.id], "me", TTL) == 1


async def test_renewing_nothing_is_not_an_error(storage):
    assert await storage.renew_lease([], "me", TTL) == 0


async def test_saving_a_profile_never_clears_its_lease(storage):
    """Renaming a profile used to rewrite the whole row, lease included."""
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, OTHER, TTL)

    profile.name = "renamed"
    await storage.save_profile(profile)

    assert (await storage.get_lease(profile.id))[0] == OTHER


async def test_force_release_clears_any_lease_and_names_its_holder(storage):
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, OTHER, TTL)

    assert await storage.force_release_lease(profile.id) == OTHER
    assert (await storage.get_lease(profile.id)) == (None, None)
    assert await storage.force_release_lease(profile.id) is None


async def test_the_holder_list_separates_live_leases_from_expired_ones(storage):
    live = await _profile(storage, "live")
    dead = await _profile(storage, "dead")
    await storage.acquire_lease(live.id, "me", TTL)
    await storage.acquire_lease(dead.id, OTHER, TTL)
    _expire_lease(storage, dead.id)

    holders = {entry["id"]: entry for entry in await storage.get_lease_holders()}

    assert holders[live.id]["expired"] is False
    assert holders[dead.id]["expired"] is True


def test_an_unreadable_expiry_counts_as_still_held():
    """The safe direction: never take over a lease we cannot read."""
    assert lease_expired("not a timestamp") is False
    assert lease_expired(None) is False


def test_holder_ids_are_host_pid_and_a_uuid(storage):
    first, second = make_lease_holder(), make_lease_holder()

    assert first.count(":") == 2
    # Two holders in one process differ: the pid alone cannot separate them.
    assert first != second


def test_a_lease_ttl_below_the_heartbeat_interval_is_refused():
    with pytest.raises(ValueError, match="at least 60 seconds"):
        Settings(lease_ttl=30)


# -- Launching, closing, exporting -----------------------------------------------


@pytest.fixture
def launches(profile_manager, monkeypatch):
    """Record launches instead of starting a browser."""
    recorded: list[dict] = []

    class FakeSession:
        process_id = 4242

        async def terminate(self):
            pass

    async def fake_launch(profile_id, options, on_exit=None):
        recorded.append(options)
        profile_manager.browser_sessions.active_sessions[profile_id] = FakeSession()
        return FakeSession()

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", lambda *_a, **_kw: {})
    return recorded


async def test_launching_takes_the_lease(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")

    await profile_manager.launch_browser(profile.id)

    assert (await profile_manager.storage.get_lease(profile.id))[0] == profile_manager.lease_holder


async def test_launching_a_profile_leased_elsewhere_is_refused(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.storage.acquire_lease(profile.id, OTHER, TTL)

    with pytest.raises(ProfileLocked) as raised:
        await profile_manager.launch_browser(profile.id)

    assert raised.value.holder == OTHER
    assert launches == []


async def test_launching_an_unknown_profile_is_not_reported_as_leased(profile_manager, launches):
    """A missing row and a held lease both change zero rows; they are not the same."""
    with pytest.raises(ValueError, match="not found"):
        await profile_manager.launch_browser("nope")


async def test_closing_the_browser_hands_the_lease_back(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)

    await profile_manager.close_browser(profile.id)

    assert (await profile_manager.storage.get_lease(profile.id))[0] is None


async def test_a_failed_launch_hands_the_lease_back(profile_manager, launches, monkeypatch):
    """A lease outliving a launch that produced nothing is worse than no lease."""
    profile = await profile_manager.create_profile(name="p")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", boom)

    with pytest.raises(RuntimeError):
        await profile_manager.launch_browser(profile.id)

    assert (await profile_manager.storage.get_lease(profile.id))[0] is None


async def test_a_cancelled_launch_hands_the_lease_back(profile_manager, launches, monkeypatch):
    """A client disconnecting mid-launch cancels the coroutine, not just raises."""
    profile = await profile_manager.create_profile(name="p")

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await profile_manager.launch_browser(profile.id)

    assert (await profile_manager.storage.get_lease(profile.id))[0] is None


async def test_a_failed_launch_keeps_the_lease_of_a_browser_that_is_still_up(
    profile_manager, launches, monkeypatch
):
    """Two launches share one holder id, so the loser must not unlock the winner."""
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("second launch fails")

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", boom)
    # The early "already running" return would not reach the launch at all, so
    # drop the local session record while leaving the lease: what remains is a
    # live browser this instance still owns.
    monkeypatch.setattr(profile_manager.browser_sessions, "is_running", lambda _id: False)

    with pytest.raises(RuntimeError):
        await profile_manager.launch_browser(profile.id)

    holder = (await profile_manager.storage.get_lease(profile.id))[0]
    assert holder == profile_manager.lease_holder


async def test_relaunching_an_already_running_profile_keeps_the_lease(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)

    result = await profile_manager.launch_browser(profile.id)

    assert result["status"] == "already_running"
    assert (await profile_manager.storage.get_lease(profile.id))[0] == profile_manager.lease_holder


async def test_exporting_a_profile_leased_elsewhere_is_refused(profile_manager, tmp_path, launches):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.storage.acquire_lease(profile.id, OTHER, TTL)

    with pytest.raises(ProfileLocked):
        await profile_manager.export_profile(profile.id, tmp_path / "out.zip")


async def test_exporting_is_allowed_once_the_other_lease_has_expired(
    profile_manager, tmp_path, launches
):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.storage.acquire_lease(profile.id, OTHER, TTL)
    _expire_lease(profile_manager.storage, profile.id)

    await profile_manager.export_profile(profile.id, tmp_path / "out.zip")

    assert (tmp_path / "out.zip").exists()


# -- Keeping the lease alive ------------------------------------------------------


async def test_the_heartbeat_renews_the_lease_of_a_running_browser(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)
    _expire_lease(profile_manager.storage, profile.id)

    await profile_manager.browser_sessions._renew_leases()  # noqa: SLF001

    assert profile_manager.browser_sessions.is_running(profile.id)
    holders = {e["id"]: e for e in await profile_manager.storage.get_lease_holders()}
    assert holders[profile.id]["expired"] is False


async def test_a_stolen_lease_closes_our_browser(profile_manager, launches):
    """Whoever holds it now is already driving the identity; racing them is the bug."""
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)
    await profile_manager.storage.force_release_lease(profile.id)
    await profile_manager.storage.acquire_lease(profile.id, OTHER, TTL)

    await profile_manager.browser_sessions._renew_leases()  # noqa: SLF001

    assert profile_manager.browser_sessions.is_running(profile.id) is False
    # The takeover stands: our teardown must not clear the new holder's lease.
    assert (await profile_manager.storage.get_lease(profile.id))[0] == OTHER


async def test_one_stolen_lease_does_not_close_the_other_browsers(profile_manager, launches):
    kept = await profile_manager.create_profile(name="kept")
    stolen = await profile_manager.create_profile(name="stolen")
    await profile_manager.launch_browser(kept.id)
    await profile_manager.launch_browser(stolen.id)
    await profile_manager.storage.force_release_lease(stolen.id)
    await profile_manager.storage.acquire_lease(stolen.id, OTHER, TTL)

    await profile_manager.browser_sessions._renew_leases()  # noqa: SLF001

    assert profile_manager.browser_sessions.is_running(kept.id) is True
    assert profile_manager.browser_sessions.is_running(stolen.id) is False


# -- Shutdown ---------------------------------------------------------------------


async def test_shutdown_releases_the_leases_this_process_holds(profile_manager, launches):
    """A restart must not lock an instance out of its own profiles for a full TTL."""
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)
    await profile_manager.browser_sessions.close_all()

    await profile_manager.release_all_leases()

    assert await profile_manager.storage.get_lease_holders() == []


async def test_shutdown_leaves_another_instances_lease_alone(profile_manager, launches):
    theirs = await profile_manager.create_profile(name="theirs")
    await profile_manager.storage.acquire_lease(theirs.id, OTHER, TTL)

    await profile_manager.release_all_leases()

    assert (await profile_manager.storage.get_lease(theirs.id))[0] == OTHER


async def test_shutdown_keeps_the_lease_of_a_browser_that_is_still_up(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)

    await profile_manager.release_all_leases()

    assert (await profile_manager.storage.get_lease(profile.id))[0] == profile_manager.lease_holder
