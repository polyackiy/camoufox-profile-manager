"""Tests for the task scheduler: what runs when, driven by an injected clock.

No test here sleeps. The scheduler reads time through a callable, so these hand
it a clock they control and call ``tick()`` themselves; the background loop is
only glue around that.
"""

from datetime import datetime, timedelta

import pytest

from camoufox_pm.core import profile_manager as pm_module
from camoufox_pm.core.models import Schedule, ScheduleRun, ScheduleRunOutcome
from camoufox_pm.core.scheduler import TaskScheduler, next_run_after

MONDAY = datetime(2026, 8, 3, 10, 0)  # a known Monday, 10:00


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kwargs) -> None:
        self.at += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock(MONDAY)


@pytest.fixture
def scheduler(profile_manager, clock):
    """A scheduler on the fake clock, never started: tests drive tick() directly."""
    return TaskScheduler(profile_manager.storage, profile_manager, now=clock)


@pytest.fixture
def launches(profile_manager, monkeypatch):
    """Capture launches without a browser, as the profile manager tests do."""
    recorded: list[str] = []

    class FakeSession:
        process_id = 4242

    async def fake_launch(profile_id, options, on_exit=None):
        recorded.append(profile_id)
        profile_manager.browser_sessions.active_sessions[profile_id] = FakeSession()
        return FakeSession()

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", lambda *_a, **_k: {})
    return recorded


def make_schedule(profile_id: str, **overrides) -> Schedule:
    fields = {
        "profile_id": profile_id,
        "action": "launch",
        "kind": "interval",
        "interval_minutes": 30,
        **overrides,
    }
    return Schedule(**fields)


# -- When the next run falls ----------------------------------------------------


def test_an_interval_schedule_fires_that_long_from_now():
    schedule = make_schedule("p", interval_minutes=45)
    assert next_run_after(schedule, MONDAY) == MONDAY + timedelta(minutes=45)


def test_a_daily_schedule_fires_later_today_if_the_time_is_still_ahead():
    schedule = make_schedule("p", kind="daily", interval_minutes=None, at_time="14:30")
    assert next_run_after(schedule, MONDAY) == MONDAY.replace(hour=14, minute=30)


def test_a_daily_schedule_whose_time_has_passed_waits_for_tomorrow():
    schedule = make_schedule("p", kind="daily", interval_minutes=None, at_time="09:00")
    assert next_run_after(schedule, MONDAY) == (MONDAY + timedelta(days=1)).replace(
        hour=9, minute=0
    )


def test_a_daily_schedule_skips_days_it_is_not_allowed_on():
    # Monday 10:00, allowed only Friday (4): jumps four days ahead.
    schedule = make_schedule("p", kind="daily", interval_minutes=None, at_time="09:00", days=[4])
    assert next_run_after(schedule, MONDAY) == datetime(2026, 8, 7, 9, 0)


def test_the_expression_must_match_the_kind():
    with pytest.raises(ValueError, match="interval_minutes"):
        make_schedule("p", interval_minutes=None)
    with pytest.raises(ValueError, match="HH:MM"):
        make_schedule("p", kind="daily", interval_minutes=None, at_time="25:99")
    with pytest.raises(ValueError, match="weekdays"):
        make_schedule("p", kind="daily", interval_minutes=None, at_time="09:00", days=[7])
    with pytest.raises(ValueError, match="launch"):
        make_schedule("p", action="refresh_browser", run_minutes=10)


# -- Firing ---------------------------------------------------------------------


async def test_a_due_schedule_fires_and_advances(profile_manager, scheduler, clock, launches):
    profile = await profile_manager.create_profile(name="warmed")
    schedule = make_schedule(profile.id, next_run_at=MONDAY - timedelta(minutes=1))
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.tick()

    assert launches == [profile.id]
    stored = await profile_manager.storage.get_schedule(schedule.id)
    assert stored.next_run_at == MONDAY + timedelta(minutes=30)
    runs = await profile_manager.storage.list_schedule_runs(schedule.id)
    assert [run.outcome for run in runs] == [ScheduleRunOutcome.OK]


async def test_a_schedule_that_is_not_due_yet_does_nothing(
    profile_manager, scheduler, clock, launches
):
    profile = await profile_manager.create_profile(name="later")
    schedule = make_schedule(profile.id, next_run_at=MONDAY + timedelta(minutes=5))
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.tick()

    assert launches == []
    assert await profile_manager.storage.list_schedule_runs(schedule.id) == []


async def test_a_failing_task_is_recorded_and_the_next_one_still_runs(
    profile_manager, scheduler, clock, launches, monkeypatch
):
    """One bad schedule must not take the tick — or the scheduler — down with it."""
    broken = await profile_manager.create_profile(name="broken")
    healthy = await profile_manager.create_profile(name="healthy")

    async def explode(profile_id, **kwargs):
        raise RuntimeError("browser exploded")

    schedule_a = make_schedule(broken.id, next_run_at=MONDAY - timedelta(minutes=1))
    schedule_b = make_schedule(healthy.id, next_run_at=MONDAY - timedelta(minutes=1))
    await profile_manager.storage.save_schedule(schedule_a)
    await profile_manager.storage.save_schedule(schedule_b)

    real_launch = profile_manager.launch_browser

    async def selective(profile_id, **kwargs):
        if profile_id == broken.id:
            return await explode(profile_id, **kwargs)
        return await real_launch(profile_id, **kwargs)

    monkeypatch.setattr(profile_manager, "launch_browser", selective)

    await scheduler.tick()

    failed = await profile_manager.storage.list_schedule_runs(schedule_a.id)
    assert failed[0].outcome == ScheduleRunOutcome.ERROR
    assert "browser exploded" in failed[0].message
    assert launches == [healthy.id]
    # And the failed schedule is still planned, not wedged in the past.
    stored = await profile_manager.storage.get_schedule(schedule_a.id)
    assert stored.next_run_at > MONDAY


async def test_a_launch_is_skipped_while_the_browser_is_already_open(
    profile_manager, scheduler, clock, launches
):
    profile = await profile_manager.create_profile(name="busy")
    await profile_manager.launch_browser(profile.id)
    launches.clear()

    schedule = make_schedule(profile.id, next_run_at=MONDAY - timedelta(minutes=1))
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.tick()

    assert launches == []
    runs = await profile_manager.storage.list_schedule_runs(schedule.id)
    assert runs[0].outcome == ScheduleRunOutcome.SKIPPED
    assert "already running" in runs[0].message


async def test_a_timed_launch_closes_itself_when_its_minutes_are_up(
    profile_manager, scheduler, clock, launches
):
    profile = await profile_manager.create_profile(name="timed")
    schedule = make_schedule(profile.id, run_minutes=15, next_run_at=MONDAY - timedelta(minutes=1))
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.tick()
    assert profile_manager.browser_sessions.is_running(profile.id)

    clock.advance(minutes=14)
    await scheduler.tick()
    assert profile_manager.browser_sessions.is_running(profile.id)

    clock.advance(minutes=2)
    await scheduler.tick()
    assert not profile_manager.browser_sessions.is_running(profile.id)


async def test_a_schedule_whose_profile_is_gone_disables_itself(
    profile_manager, scheduler, clock, launches
):
    """Deleting a profile removes its schedules; this covers the race where one
    fires in between. It must not keep erroring every occurrence forever."""
    schedule = make_schedule("vanished", next_run_at=MONDAY - timedelta(minutes=1))
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.tick()

    stored = await profile_manager.storage.get_schedule(schedule.id)
    assert stored.enabled is False
    runs = await profile_manager.storage.list_schedule_runs(schedule.id)
    assert runs[0].outcome == ScheduleRunOutcome.ERROR
    assert "no longer exists" in runs[0].message


async def test_a_disabled_schedule_never_fires(profile_manager, scheduler, clock, launches):
    profile = await profile_manager.create_profile(name="paused")
    schedule = make_schedule(profile.id, enabled=False, next_run_at=MONDAY - timedelta(minutes=1))
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.tick()

    assert launches == []


async def test_run_now_executes_but_leaves_the_plan_alone(
    profile_manager, scheduler, clock, launches
):
    profile = await profile_manager.create_profile(name="manual")
    planned = MONDAY + timedelta(hours=3)
    schedule = make_schedule(profile.id, next_run_at=planned)
    await profile_manager.storage.save_schedule(schedule)

    run = await scheduler.run_now(schedule)

    assert run.outcome == ScheduleRunOutcome.OK
    assert launches == [profile.id]
    stored = await profile_manager.storage.get_schedule(schedule.id)
    assert stored.next_run_at == planned


async def test_refresh_browser_records_the_failure_when_there_is_no_pin(
    profile_manager, scheduler, clock
):
    """Without a pinned machine there is nothing to refresh; the run says so."""
    profile = await profile_manager.create_profile(name="unpinned")
    schedule = make_schedule(
        profile.id,
        action="refresh_browser",
        next_run_at=MONDAY - timedelta(minutes=1),
    )
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.tick()

    runs = await profile_manager.storage.list_schedule_runs(schedule.id)
    assert runs[0].outcome == ScheduleRunOutcome.ERROR
    assert "no pinned machine" in runs[0].message


# -- Restart behaviour ----------------------------------------------------------


async def test_runs_missed_while_down_are_recorded_once_and_skipped(
    profile_manager, scheduler, clock, launches
):
    """Twelve missed warming launches must not fire at once on startup: the gap
    becomes one visible 'missed' row and the schedule waits for its next slot."""
    profile = await profile_manager.create_profile(name="offline")
    schedule = make_schedule(
        profile.id, interval_minutes=60, next_run_at=MONDAY - timedelta(hours=12)
    )
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.reconcile_missed()

    assert launches == []
    runs = await profile_manager.storage.list_schedule_runs(schedule.id)
    assert [run.outcome for run in runs] == [ScheduleRunOutcome.MISSED]
    stored = await profile_manager.storage.get_schedule(schedule.id)
    assert stored.next_run_at == MONDAY + timedelta(hours=1)

    # And the reconciled schedule does not fire on the first tick either.
    await scheduler.tick()
    assert launches == []


async def test_a_schedule_that_never_ran_gets_a_first_plan_on_startup(
    profile_manager, scheduler, clock
):
    profile = await profile_manager.create_profile(name="fresh")
    schedule = make_schedule(profile.id, next_run_at=None)
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.reconcile_missed()

    stored = await profile_manager.storage.get_schedule(schedule.id)
    assert stored.next_run_at == MONDAY + timedelta(minutes=30)
    assert await profile_manager.storage.list_schedule_runs(schedule.id) == []


async def test_a_future_schedule_survives_a_restart_untouched(profile_manager, scheduler, clock):
    profile = await profile_manager.create_profile(name="pending")
    planned = MONDAY + timedelta(minutes=10)
    schedule = make_schedule(profile.id, next_run_at=planned)
    await profile_manager.storage.save_schedule(schedule)

    await scheduler.reconcile_missed()

    stored = await profile_manager.storage.get_schedule(schedule.id)
    assert stored.next_run_at == planned
    assert await profile_manager.storage.list_schedule_runs(schedule.id) == []


# -- Storage --------------------------------------------------------------------


async def test_a_schedule_round_trips_through_the_database(storage):
    schedule = Schedule(
        profile_id="p1",
        action="launch",
        kind="daily",
        at_time="09:00",
        days=[0, 2, 4],
        run_minutes=20,
        next_run_at=datetime(2026, 8, 5, 9, 0),
    )
    await storage.save_schedule(schedule)

    loaded = await storage.get_schedule(schedule.id)
    assert loaded == schedule
    assert await storage.list_schedules() == [schedule]

    assert await storage.delete_schedule(schedule.id) is True
    assert await storage.get_schedule(schedule.id) is None


async def test_deleting_a_profile_takes_its_schedules_and_history_with_it(profile_manager):
    profile = await profile_manager.create_profile(name="doomed")
    schedule = make_schedule(profile.id)
    storage = profile_manager.storage
    await storage.save_schedule(schedule)
    await storage.log_schedule_run(
        ScheduleRun(schedule_id=schedule.id, outcome=ScheduleRunOutcome.OK)
    )

    await profile_manager.delete_profile(profile.id)

    assert await storage.get_schedule(schedule.id) is None
    assert await storage.list_schedule_runs(schedule.id) == []


async def test_run_history_is_pruned_to_the_newest_twenty(storage):
    for index in range(25):
        await storage.log_schedule_run(
            ScheduleRun(schedule_id="s1", outcome=ScheduleRunOutcome.OK, message=str(index))
        )

    runs = await storage.list_schedule_runs("s1", limit=50)
    assert len(runs) == 20
    assert runs[0].message == "24"
    assert runs[-1].message == "5"


async def test_an_existing_database_gains_the_schedule_tables(tmp_path):
    """An install from before schedules existed opens cleanly and keeps its data."""
    import sqlite3

    from camoufox_pm.core.database import StorageManager

    db_path = tmp_path / "old.db"
    connection = sqlite3.connect(db_path)
    # The profiles table exactly as 0.1.0 created it: no fingerprint column,
    # no schedule tables anywhere.
    connection.execute("""
        CREATE TABLE profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            group_id TEXT,
            status TEXT DEFAULT 'active',
            browser_settings TEXT NOT NULL,
            proxy_config TEXT,
            extensions TEXT,
            storage_path TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP
        )
    """)
    connection.execute(
        "INSERT INTO profiles (id, name, browser_settings, created_at, updated_at) "
        "VALUES ('old1', 'survivor', '{}', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    connection.commit()
    connection.close()

    storage = StorageManager(str(db_path))
    await storage.initialize()
    try:
        survivor = await storage.get_profile("old1")
        assert survivor is not None and survivor.name == "survivor"

        schedule = make_schedule("old1")
        await storage.save_schedule(schedule)
        assert (await storage.get_schedule(schedule.id)) == schedule
    finally:
        await storage.close()
