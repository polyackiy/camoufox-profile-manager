"""Row-level lease tests.

The lease is what makes two CFPM instances (two machines, one shared
PostgreSQL) safe: launching, exporting, and editing a profile must refuse
while another holder's lease is alive, and must be decidable from the
database alone — the in-process session dict is invisible to the other
machine.

Two layers live here:

- backend tests (marked ``postgres``, run with ``-m postgres`` against a real
  PostgreSQL). The concurrent-acquire test opens two real connections on two
  threads, waits for both to be ready on a barrier, and only then races the
  same conditional UPDATE — a mocked backend or a same-thread race would
  prove nothing.
- backend-free tests (run in the default suite): the same lease semantics
  through the SQLite backend, whose conditional-UPDATE statements are the
  same shape, plus the manager-level refusals.

Timezone note: Postgres returns ``lock_expires`` as an aware datetime, SQLite
as a naive local-time string. Every test that plants an expiry writes it with
the backend's own clock (``now()`` on Postgres, ``datetime('now')`` on
SQLite), so the assertion never depends on this machine's timezone.
"""

import asyncio
import threading
from contextlib import contextmanager

import pytest

from camoufox_pm.core import browser_session as browser_session_module
from camoufox_pm.core import fingerprint_store
from camoufox_pm.core.errors import ProfileLocked, StaleWriteError, make_lease_holder
from camoufox_pm.core.models import Profile

HOLDER_A = "desktop:100:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
HOLDER_B = "laptop:200:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
TTL = 120


@pytest.fixture
def launches(pg_profile_manager, monkeypatch):
    """Capture launch options without starting a browser.

    Same shape as the fixture in test_profile_manager: fingerprint resolution
    reads the installed browser, which CI does not have.
    """
    recorded: list[dict] = []

    class FakeSession:
        process_id = 4242

        async def terminate(self):
            pass

    async def fake_launch(profile_id, options, on_exit=None):
        recorded.append(options)
        pg_profile_manager.browser_sessions.active_sessions[profile_id] = FakeSession()
        return FakeSession()

    monkeypatch.setattr(pg_profile_manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(fingerprint_store, "resolve", lambda *_args, **_kw: {})
    return recorded


# -- Postgres backend: atomic acquire ----------------------------------------------


@pytest.mark.postgres
async def test_concurrent_acquire_produces_exactly_one_winner(postgres_backend):
    """Two connections racing on a free profile: exactly one acquires.

    This is the core claim of the lease — correct under READ COMMITTED because
    the UPDATE's row lock serialises the writers — so it is executed with two
    real connections on two threads, held on a barrier until both statements
    are queued, then released. A same-event-loop "race" would serialise the
    statements before the server ever sees them.
    """
    await postgres_backend.save_profile(Profile(id="race1", name="raced"))

    with _racing_holders(
        postgres_backend.db_url, "race1", [HOLDER_A, HOLDER_B], TTL, postgres_backend._test_schema
    ) as winners:
        # Exactly one, and which one is whichever statement the server ran
        # first — both winning is the bug, not either single winner.
        assert winners() in ([HOLDER_A], [HOLDER_B])

    assert (await postgres_backend.get_lease("race1"))[0] in (HOLDER_A, HOLDER_B)


@pytest.mark.postgres
async def test_the_loser_of_a_race_sees_the_winners_lease(postgres_backend):
    """Losing must leave the winner's lease standing, not take the row apart."""
    await postgres_backend.save_profile(Profile(id="race2", name="raced"))
    assert await postgres_backend.acquire_lease("race2", HOLDER_A, ttl_seconds=TTL) is True

    with _racing_holders(
        postgres_backend.db_url, "race2", [HOLDER_B, HOLDER_A], TTL, postgres_backend._test_schema
    ) as winners:
        # The holder arm of the guard makes the winner re-acquiring its own
        # lease a second success; the other holder must still lose.
        assert sorted(winners()) == [HOLDER_A]

    assert (await postgres_backend.get_lease("race2"))[0] == HOLDER_A


@pytest.mark.postgres
async def test_an_expired_lease_is_acquirable_by_another_holder(postgres_backend):
    await postgres_backend.save_profile(Profile(id="expired1", name="abandoned"))
    assert await postgres_backend.acquire_lease("expired1", HOLDER_A, ttl_seconds=TTL)

    # The holder is gone; the lease lapses. Written with the server's clock so
    # the test does not depend on this machine's timezone or clock skew.
    postgres_backend._connection.execute(
        "UPDATE profiles SET lock_expires = now() - interval '1 second' WHERE id = %s",
        ("expired1",),
    )

    assert await postgres_backend.acquire_lease("expired1", HOLDER_B, ttl_seconds=TTL)
    assert (await postgres_backend.get_lease("expired1"))[0] == HOLDER_B


@pytest.mark.postgres
async def test_reacquiring_your_own_lease_is_idempotent(postgres_backend):
    await postgres_backend.save_profile(Profile(id="mine1", name="restart"))
    assert await postgres_backend.acquire_lease("mine1", HOLDER_A, ttl_seconds=TTL)
    # A restart on the same host mints a new uuid, but a holder that kept its
    # id (or re-acquired before expiry) must not lock itself out.
    assert await postgres_backend.acquire_lease("mine1", HOLDER_A, ttl_seconds=TTL)
    assert (await postgres_backend.get_lease("mine1"))[0] == HOLDER_A


@pytest.mark.postgres
async def test_a_held_lease_blocks_other_holders_until_expiry(postgres_backend):
    await postgres_backend.save_profile(Profile(id="held1", name="busy"))
    assert await postgres_backend.acquire_lease("held1", HOLDER_A, ttl_seconds=3600)
    assert await postgres_backend.acquire_lease("held1", HOLDER_B, ttl_seconds=TTL) is False
    # The refusal must not have taken the lease apart.
    assert (await postgres_backend.get_lease("held1"))[0] == HOLDER_A


# -- Postgres backend: optimistic-concurrency saves --------------------------------


@pytest.mark.postgres
async def test_saving_with_a_stale_row_version_raises(postgres_backend):
    await postgres_backend.save_profile(Profile(id="stale1", name="orig"))
    first = await postgres_backend.get_profile("stale1")

    winner = await postgres_backend.get_profile("stale1")
    winner.name = "winner"
    await postgres_backend.save_profile(winner, expected_row_version=winner.row_version)

    first.name = "loser"
    with pytest.raises(StaleWriteError):
        await postgres_backend.save_profile(first, expected_row_version=first.row_version)

    # The winner's value must survive the refused write.
    assert (await postgres_backend.get_profile("stale1")).name == "winner"


@pytest.mark.postgres
async def test_concurrent_versioned_saves_leave_exactly_one_standing(postgres_backend):
    """The whole-row clobber two web UIs would produce, refused instead."""
    await postgres_backend.save_profile(Profile(id="stale2", name="orig"))
    left = await postgres_backend.get_profile("stale2")
    right = await postgres_backend.get_profile("stale2")

    left.name = "edited-on-desktop"
    right.name = "edited-on-laptop"

    await postgres_backend.save_profile(left, expected_row_version=left.row_version)
    with pytest.raises(StaleWriteError):
        await postgres_backend.save_profile(right, expected_row_version=right.row_version)

    assert (await postgres_backend.get_profile("stale2")).name == "edited-on-desktop"


# -- Postgres backend: heartbeat -----------------------------------------------------


@pytest.mark.postgres
async def test_the_heartbeat_renews_only_its_own_leases(postgres_backend):
    await postgres_backend.save_profile(Profile(id="beat1", name="ours"))
    await postgres_backend.save_profile(Profile(id="beat2", name="theirs"))

    assert await postgres_backend.acquire_lease("beat1", HOLDER_A, ttl_seconds=1)
    assert await postgres_backend.acquire_lease("beat2", HOLDER_B, ttl_seconds=3600)

    renewed = await postgres_backend.renew_lease(["beat1", "beat2"], HOLDER_A, ttl_seconds=120)
    assert renewed == 1

    # Ours was pushed out well past the old expiry; theirs is untouched.
    ours = await postgres_backend.get_lease("beat1")
    theirs = await postgres_backend.get_lease("beat2")
    assert theirs[0] == HOLDER_B
    assert ours[1] is not None


# -- Backend-free: the same semantics through SQLite ---------------------------------


async def test_sqlite_backend_acquires_and_refuses_the_same_way(storage):
    profile = Profile(name="held")
    await storage.save_profile(profile)

    assert await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=TTL) is True
    assert await storage.acquire_lease(profile.id, HOLDER_B, ttl_seconds=TTL) is False
    assert await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=TTL) is True
    assert (await storage.get_lease(profile.id))[0] == HOLDER_A


async def test_a_sqlite_lease_is_visible_to_another_connection(storage, tmp_path):
    """A lease left uncommitted is invisible to every other process.

    This is the regression test for the missing ``commit()`` in the SQLite
    lease methods: without it the lease lived only inside this connection's
    open transaction, so a second CFPM instance looking at the same file
    saw the profile as free — the exact failure the lease exists to prevent.
    """
    profile = Profile(name="cross-conn")
    await storage.save_profile(profile)
    assert await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=TTL) is True

    import sqlite3

    other = sqlite3.connect(str(tmp_path / "test.db"))
    try:
        row = other.execute("SELECT locked_by FROM profiles WHERE id = ?", (profile.id,)).fetchone()
    finally:
        other.close()
    assert row is not None and row[0] == HOLDER_A


async def test_sqlite_backend_frees_an_expired_lease(storage):
    profile = Profile(name="abandoned")
    await storage.save_profile(profile)
    await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=TTL)

    # The backend's own clock, so the comparison in the acquire guard reads
    # the same units it wrote.
    storage.db._connection.execute(
        "UPDATE profiles SET lock_expires = datetime('now', '-1 second') WHERE id = ?",
        (profile.id,),
    )
    storage.db._connection.commit()

    assert await storage.acquire_lease(profile.id, HOLDER_B, ttl_seconds=TTL) is True


async def test_the_sqlite_backend_refuses_a_stale_row_version(storage):
    profile = Profile(name="orig")
    await storage.save_profile(profile)
    first = await storage.get_profile(profile.id)

    winner = await storage.get_profile(profile.id)
    winner.name = "winner"
    await storage.save_profile(winner, expected_row_version=winner.row_version)

    first.name = "loser"
    with pytest.raises(StaleWriteError):
        await storage.save_profile(first, expected_row_version=first.row_version)

    assert (await storage.get_profile(profile.id)).name == "winner"


async def test_a_versioned_save_bumps_and_propagates_the_version(storage):
    profile = Profile(name="v")
    await storage.save_profile(profile)
    assert profile.row_version == 0

    await storage.update_profile(profile, expected_row_version=0)
    assert profile.row_version == 1

    reloaded = await storage.get_profile(profile.id)
    assert reloaded.row_version == 1
    await storage.update_profile(reloaded, expected_row_version=reloaded.row_version)
    assert (await storage.get_profile(profile.id)).row_version == 2


async def test_an_unversioned_save_never_resets_the_row_version(storage):
    profile = Profile(name="v")
    await storage.save_profile(profile)
    await storage.update_profile(profile, expected_row_version=0)
    assert (await storage.get_profile(profile.id)).row_version == 1

    await storage.save_profile(profile)
    assert (await storage.get_profile(profile.id)).row_version == 1


async def test_a_save_never_steals_or_clears_another_holders_lease(storage):
    profile = Profile(name="leased")
    await storage.save_profile(profile)
    await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=TTL)

    editor = await storage.get_profile(profile.id)
    editor.notes = "edited while leased elsewhere"
    await storage.save_profile(editor)

    stored = await storage.get_profile(profile.id)
    assert stored.notes == "edited while leased elsewhere"
    assert (await storage.get_lease(profile.id))[0] == HOLDER_A


async def test_release_only_removes_your_own_lease(storage):
    profile = Profile(name="leased")
    await storage.save_profile(profile)
    await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=TTL)

    assert await storage.release_lease(profile.id, HOLDER_B) is False
    assert (await storage.get_lease(profile.id))[0] == HOLDER_A

    assert await storage.release_lease(profile.id, HOLDER_A) is True
    assert (await storage.get_lease(profile.id))[0] is None


async def test_the_holders_list_reports_expiry(storage):
    profile = Profile(name="held")
    await storage.save_profile(profile)
    await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=3600)

    holders = await storage.get_lease_holders()
    assert [h["id"] for h in holders] == [profile.id]
    assert holders[0]["locked_by"] == HOLDER_A
    assert holders[0]["expired"] is False


async def test_the_holders_list_sees_an_expired_lease(storage):
    profile = Profile(name="lapsed")
    await storage.save_profile(profile)
    await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=3600)
    storage.db._connection.execute(
        "UPDATE profiles SET lock_expires = datetime('now', '-1 second') WHERE id = ?",
        (profile.id,),
    )
    storage.db._connection.commit()

    holders = await storage.get_lease_holders()
    assert holders[0]["expired"] is True


async def test_force_release_clears_a_lease(storage):
    profile = Profile(name="stuck")
    await storage.save_profile(profile)
    await storage.acquire_lease(profile.id, HOLDER_A, ttl_seconds=3600)

    assert await storage.force_release_lease(profile.id) == HOLDER_A
    assert (await storage.get_lease(profile.id))[0] is None
    assert await storage.force_release_lease(profile.id) is None


async def test_holder_ids_are_host_pid_uuid(storage):
    holder = make_lease_holder()
    host, pid, uuid = holder.split(":")
    assert host
    assert pid.isdigit()
    assert len(uuid) == 36 and uuid.count("-") == 4
    assert make_lease_holder() != holder


# -- The manager refuses to launch another holder's profile --------------------------


@pytest.mark.postgres
async def test_launching_a_leased_profile_raises_profile_locked(pg_profile_manager, launches):
    profile = await pg_profile_manager.create_profile(name="theirs")
    other = make_lease_holder()
    assert await pg_profile_manager.storage.acquire_lease(profile.id, other, ttl_seconds=3600)

    with pytest.raises(ProfileLocked):
        await pg_profile_manager.launch_browser(profile.id)
    # The refusal happened before anything opened.
    assert launches == []


@pytest.mark.postgres
async def test_launching_a_free_profile_acquires_the_lease(pg_profile_manager, launches):
    profile = await pg_profile_manager.create_profile(name="ours")
    await pg_profile_manager.launch_browser(profile.id)
    lease = await pg_profile_manager.storage.get_lease(profile.id)
    assert lease[0] == pg_profile_manager.lease_holder


@pytest.mark.postgres
async def test_closing_the_browser_releases_the_lease(pg_profile_manager, launches):
    profile = await pg_profile_manager.create_profile(name="mine")
    await pg_profile_manager.launch_browser(profile.id)
    assert (await pg_profile_manager.storage.get_lease(profile.id))[0] is not None

    await pg_profile_manager.close_browser(profile.id)
    assert (await pg_profile_manager.storage.get_lease(profile.id))[0] is None


# -- A failed launch must hand the lease back ---------------------------------------


@pytest.mark.postgres
async def test_a_failed_launch_releases_the_lease(pg_profile_manager, launches, monkeypatch):
    profile = await pg_profile_manager.create_profile(name="doomed")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(pg_profile_manager.browser_sessions, "launch", boom)

    with pytest.raises(RuntimeError):
        await pg_profile_manager.launch_browser(profile.id)

    # Free for anyone, not just us: another machine must be able to take it.
    assert (await pg_profile_manager.storage.get_lease(profile.id))[0] is None
    assert await pg_profile_manager.storage.acquire_lease(profile.id, HOLDER_B, ttl_seconds=3600)


@pytest.mark.postgres
async def test_a_stale_write_during_launch_releases_the_lease(pg_profile_manager, launches):
    """StaleWriteError is this branch's own failure mode, and it fires exactly
    in the two-machines scenario the lease exists for — so its path out of a
    half-launch must leave the profile usable on both machines."""
    profile = await pg_profile_manager.create_profile(name="edited-elsewhere")
    # Another writer bumps the row version after launch_browser has read it.
    original_update = pg_profile_manager.storage.update_profile

    async def stale_update(edited, **kwargs):
        rival = await pg_profile_manager.storage.get_profile(edited.id)
        await original_update(rival, expected_row_version=rival.row_version)
        return await original_update(edited, **kwargs)

    pg_profile_manager.storage.update_profile = stale_update
    try:
        with pytest.raises(StaleWriteError):
            await pg_profile_manager.launch_browser(profile.id)
    finally:
        del pg_profile_manager.storage.update_profile

    assert (await pg_profile_manager.storage.get_lease(profile.id))[0] is None
    assert await pg_profile_manager.storage.acquire_lease(profile.id, HOLDER_B, ttl_seconds=3600)


@pytest.mark.postgres
async def test_a_successful_launch_keeps_the_lease(pg_profile_manager, launches):
    """The browser owns the lease until it exits; a fix that released on
    success would race the very two-instance window the lease closes."""
    profile = await pg_profile_manager.create_profile(name="healthy")
    await pg_profile_manager.launch_browser(profile.id)

    lease = await pg_profile_manager.storage.get_lease(profile.id)
    assert lease[0] == pg_profile_manager.lease_holder


@pytest.mark.postgres
async def test_a_failed_launch_keeps_the_lease_of_a_live_session(pg_profile_manager, monkeypatch):
    """Two concurrent launches, one profile, one process — same holder id.

    The acquire is idempotent for our own holder, so both calls pass it, and
    both pass the is_running check. When the loser then fails, its except
    block must not clear the lease under the survivor's live browser: the
    release is guarded on "no session for this profile is running", which is
    decidable locally, not by the per-process holder id alone.
    """
    manager = pg_profile_manager
    profile = await manager.create_profile(name="contested")

    # Two launches in flight before either registers a session: both acquire
    # (idempotent for our holder), both pass the is_running check, then the
    # winner's browser comes up while the loser's launch fails — the
    # reviewer's interleaving. The awaited storage calls are the yield points
    # that let the two calls interleave.
    real_update = manager.storage.update_profile
    attempts: list = []

    async def unversioned_update(edited, **kwargs):
        # The two calls each read the row before the other wrote it; the
        # version conflict is a different failure mode with its own test.
        return await real_update(edited)

    winner_started = asyncio.Event()
    loser_failed = asyncio.Event()

    # Patch the BROWSER, not launch(): the real launch() has to run so its own
    # _starting bookkeeping and finally are exercised. A test that marks
    # _starting itself only proves its own fake — deleting the production mark
    # would leave such a test green.
    class FakeCamoufox:
        def __init__(self, **_kwargs):
            pass

        async def start(self):
            attempts.append(1)
            if len(attempts) == 1:
                # Held INSIDE start(): no session is registered yet, which is
                # the window where the guard used to lose the lease.
                winner_started.set()
                await loser_failed.wait()
                return object()
            await winner_started.wait()
            try:
                raise RuntimeError("second launch exploded")
            finally:
                # Released after the raise, so the loser's real finally runs
                # while the winner is still inside its own start().
                loser_failed.set()

    monkeypatch.setattr(fingerprint_store, "resolve", lambda *_a, **_kw: {})
    monkeypatch.setattr(browser_session_module, "AsyncCamoufox", FakeCamoufox)
    monkeypatch.setattr(browser_session_module, "CAMOUFOX_AVAILABLE", True)
    monkeypatch.setattr(
        browser_session_module.BrowserSessionManager, "_register_close_handler", lambda *a: None
    )
    monkeypatch.setattr(browser_session_module, "_resolve_process_id", lambda *_a, **_kw: None)
    monkeypatch.setattr(manager.storage, "update_profile", unversioned_update)

    results = await asyncio.gather(
        manager.launch_browser(profile.id),
        manager.launch_browser(profile.id),
        return_exceptions=True,
    )
    outcomes = sorted(r["status"] if isinstance(r, dict) else type(r).__name__ for r in results)
    assert outcomes == ["BrowserLaunchError", "launched"]
    assert len(attempts) == 2

    # The survivor's browser is still running and still owns its lease.
    assert manager.browser_sessions.is_running(profile.id) is True
    lease = await manager.storage.get_lease(profile.id)
    assert lease[0] == manager.lease_holder


@pytest.mark.postgres
async def test_a_cancelled_launch_releases_the_lease(pg_profile_manager, launches, monkeypatch):
    """A client disconnecting mid-launch cancels the coroutine; CancelledError
    derives from BaseException, so a plain ``except Exception`` misses it and
    the lease would outlive a launch that produced no browser."""
    manager = pg_profile_manager
    profile = await manager.create_profile(name="cancelled")

    async def hang(profile_id, options, on_exit=None):
        await asyncio.Event().wait()  # never completes; cancelled from outside

    monkeypatch.setattr(manager.browser_sessions, "launch", hang)

    task = asyncio.create_task(manager.launch_browser(profile.id))
    # Let the task run past the acquire and into the hanging launch, so the
    # cancellation lands where the client-disconnect would: mid-launch.
    await asyncio.sleep(0.1)
    assert (await manager.storage.get_lease(profile.id))[0] is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (await manager.storage.get_lease(profile.id))[0] is None


# -- Shutdown releases every lease this process holds -------------------------------


@pytest.mark.postgres
async def test_shutdown_releases_leases_held_by_this_process(pg_profile_manager):
    """The lifespan shutdown path must hand every lease back: a lease that
    outlives its process locks the profile against the whole fleet (including
    a restarted self) for the full TTL."""
    manager = pg_profile_manager
    ours = await manager.create_profile(name="ours")
    theirs = await manager.create_profile(name="theirs")
    await manager.storage.acquire_lease(ours.id, manager.lease_holder, ttl_seconds=3600)
    await manager.storage.acquire_lease(theirs.id, HOLDER_B, ttl_seconds=3600)

    await manager.release_all_leases()

    assert (await manager.storage.get_lease(ours.id))[0] is None
    # Another holder's lease is guarded by the holder id and must survive.
    assert (await manager.storage.get_lease(theirs.id))[0] == HOLDER_B


@pytest.mark.postgres
async def test_shutdown_release_survives_one_failing_profile_and_never_raises(
    pg_profile_manager, monkeypatch
):
    manager = pg_profile_manager
    stuck = await manager.create_profile(name="stuck")
    fine = await manager.create_profile(name="fine")
    await manager.storage.acquire_lease(stuck.id, manager.lease_holder, ttl_seconds=3600)
    await manager.storage.acquire_lease(fine.id, manager.lease_holder, ttl_seconds=3600)

    real_release = manager.storage.release_lease

    async def flaky_release(profile_id, holder):
        if profile_id == stuck.id:
            raise RuntimeError("database went away")
        return await real_release(profile_id, holder)

    monkeypatch.setattr(manager.storage, "release_lease", flaky_release)

    # Must not raise, and must still have released the healthy one.
    await manager.release_all_leases()
    assert (await manager.storage.get_lease(fine.id))[0] is None


# -- Export is fleet-safe ------------------------------------------------------------


@pytest.mark.postgres
async def test_exporting_a_profile_leased_elsewhere_is_refused(
    pg_profile_manager, tmp_path, monkeypatch
):
    """The in-process dict cannot see another machine's browser; the lease can."""
    profile = await pg_profile_manager.create_profile(name="traveler")
    other = make_lease_holder()
    await pg_profile_manager.storage.acquire_lease(profile.id, other, ttl_seconds=3600)

    async def refuse(*_args, **_kwargs):
        raise AssertionError("export must refuse before reading any profile data")

    monkeypatch.setattr("camoufox_pm.core.profile_archive.export_profile", refuse)

    with pytest.raises(ProfileLocked):
        await pg_profile_manager.export_profile(profile.id, tmp_path / "export.zip")


@pytest.mark.postgres
async def test_exporting_an_expired_lease_is_allowed(pg_profile_manager, tmp_path, monkeypatch):
    profile = await pg_profile_manager.create_profile(name="abandoned")
    await pg_profile_manager.storage.acquire_lease(
        profile.id, "dead-host:1:cccccccc-cccc-cccc-cccc-cccccccccccc", ttl_seconds=3600
    )
    storage = pg_profile_manager.storage
    # The backend's own clock, as everywhere an expiry is planted here.
    storage._connection.execute(
        "UPDATE profiles SET lock_expires = now() - interval '1 second' WHERE id = %s",
        (profile.id,),
    )
    destination = tmp_path / "out.zip"
    await pg_profile_manager.export_profile(profile.id, destination)
    assert destination.exists()


# -- Heartbeat -----------------------------------------------------------------------


@pytest.mark.postgres
async def test_the_heartbeat_closes_browsers_whose_lease_was_lost(pg_profile_manager, launches):
    storage = pg_profile_manager.storage
    first = await pg_profile_manager.create_profile(name="heartbeat-a")
    second = await pg_profile_manager.create_profile(name="heartbeat-b")

    await pg_profile_manager.launch_browser(first.id)
    await pg_profile_manager.launch_browser(second.id)

    # Another machine takes one of the two leases while we are not looking.
    assert await storage.release_lease(first.id, pg_profile_manager.lease_holder)
    assert await storage.acquire_lease(first.id, HOLDER_B, ttl_seconds=3600)

    await pg_profile_manager.browser_sessions._renew_leases()

    # The stolen lease's browser is closed and its session forgotten; the
    # other one keeps running.
    assert pg_profile_manager.browser_sessions.is_running(first.id) is False
    assert pg_profile_manager.browser_sessions.is_running(second.id) is True


# -- The concurrent-acquire race harness ---------------------------------------------


class _RacingHolder(threading.Thread):
    """One lease candidate: its own connection, waiting on a shared barrier."""

    def __init__(self, db_url: str, profile_id: str, holder: str, ttl: int, barrier, schema: str):
        super().__init__()
        self.db_url = db_url
        self.profile_id = profile_id
        self.holder = holder
        self.ttl = ttl
        self.barrier = barrier
        self.schema = schema
        self.won: bool | None = None
        self.error: Exception | None = None

    def run(self):
        # Deferred: psycopg is an optional dependency; this thread only runs
        # inside the postgres-marked tests, never in the default SQLite suite.
        import psycopg
        from psycopg.rows import dict_row

        try:
            conn = psycopg.connect(
                self.db_url,
                row_factory=dict_row,
                client_encoding="UTF8",
                options=f"-c search_path={self.schema},public",
            )
            conn.autocommit = True
            self.barrier.wait()
            cursor = conn.execute(
                """
                UPDATE profiles
                   SET locked_by = %s,
                       lock_expires = now() + %s * interval '1 second'
                 WHERE id = %s
                   AND (locked_by IS NULL OR lock_expires < now() OR locked_by = %s)
                RETURNING id
                """,
                (self.holder, self.ttl, self.profile_id, self.holder),
            )
            self.won = cursor.fetchone() is not None
            conn.close()
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            self.error = exc


@contextmanager
def _racing_holders(db_url: str, profile_id: str, holders: list[str], ttl: int, schema: str):
    """Start one thread per holder, release them together, hand out a results fn.

    The barrier is the contention: every thread has an open connection and is
    blocked on the same latch before any statement runs, so the server really
    sees two racing UPDATEs on one row. The schema pin keeps every thread on
    the scratch schema — a fresh connection's default search_path would land
    on ``public.profiles`` instead.
    """
    barrier = threading.Barrier(len(holders))
    racers = [_RacingHolder(db_url, profile_id, holder, ttl, barrier, schema) for holder in holders]
    for racer in racers:
        racer.start()
    for racer in racers:
        # Join before handing out the results fn: inside the with-block the
        # event loop is free, but a racer that has not run yet has won=None.
        racer.join(timeout=10)

    def results():
        for racer in racers:
            if racer.error is not None:
                raise racer.error
        return [racer.holder for racer in racers if racer.won]

    yield results


# -- Operator-facing surfaces: a refused lease, a bad TTL, a logged DSN ---------


def test_lease_ttl_below_the_heartbeat_interval_is_refused():
    """A TTL at or under the 30s renewal expires leases under live browsers.

    Zero or negative silently turns mutual exclusion off entirely, which is
    worse than a loud failure at startup.
    """
    from pydantic import ValidationError

    from camoufox_pm.config import Settings

    for bad in (0, -1, 30, 59):
        with pytest.raises(ValidationError):
            Settings(lease_ttl=bad)
    assert Settings(lease_ttl=60).lease_ttl == 60


def test_a_dsn_password_never_reaches_the_log_in_any_accepted_form():
    """psycopg takes the password in the netloc, a query parameter or a
    keyword/value DSN; every one of them used to be logged verbatim (CWE-532).
    """
    from camoufox_pm.core.database import _mask_dsn

    secret = "hunter2"
    for dsn in (
        f"postgresql://u:{secret}@h:5432/db",
        f"postgresql://u@h:5432/db?password={secret}",
        f"postgresql:///db?password={secret}&host=h",
        f"host=h password={secret} user=u",
    ):
        assert secret not in _mask_dsn(dsn), dsn


@pytest.mark.postgres
async def test_launching_a_leased_profile_is_a_conflict_not_a_server_error(
    pg_profile_manager, launches, monkeypatch
):
    """A profile held by another instance is a 409, not a 500.

    The lease refusing a launch is the design working; reporting it as a server
    fault tells a client to file a bug instead of retrying later.
    """
    from fastapi import HTTPException

    from camoufox_pm.api.routes import profiles as profiles_routes

    manager = pg_profile_manager
    profile = await manager.create_profile(name="held elsewhere")
    other = make_lease_holder()
    assert await manager.storage.acquire_lease(profile.id, other, ttl_seconds=3600)

    class _Req:
        headless = False
        window_size = None
        additional_options = None

    monkeypatch.setattr(profiles_routes, "get_profile_manager", lambda: manager)
    with pytest.raises(HTTPException) as caught:
        await profiles_routes.launch_profile(profile.id, _Req())
    assert caught.value.status_code == 409
    assert other in str(caught.value.detail)
