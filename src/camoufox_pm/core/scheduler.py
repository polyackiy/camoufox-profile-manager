"""Run schedules against profiles from inside the app process.

A plain asyncio loop over the schedules table — no APScheduler, no crontab.
The app already owns the browser sessions, so a scheduled launch has to happen
in this process and go through the same ``ProfileManager`` a manual one does;
an external scheduler could only talk to the API and would be one more thing
to install and keep running.

What is schedulable is deliberately narrow: opening a profile's browser, and
moving its pinned fingerprint onto the installed browser version. Hardware
regeneration is not — see ``ScheduleAction`` in ``models.py`` for why.

Time is read through an injectable ``now`` callable so tests can drive the
clock instead of sleeping. All datetimes are naive local time, like the rest
of the codebase: the process runs next to its browsers, and "09:00" means
09:00 on this machine's clock.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from loguru import logger

from .database import StorageManager
from .models import (
    Schedule,
    ScheduleAction,
    ScheduleKind,
    ScheduleRun,
    ScheduleRunOutcome,
)
from .profile_manager import ProfileManager


def next_run_after(schedule: Schedule, after: datetime) -> datetime:
    """The first time this schedule should fire strictly after ``after``.

    Always computed forward from ``after`` rather than from the previous fire
    time, so a late or missed run can never produce a next run in the past —
    which would make the schedule fire again immediately, forever.
    """
    if schedule.kind == ScheduleKind.INTERVAL:
        assert schedule.interval_minutes  # validated on the model
        return after + timedelta(minutes=schedule.interval_minutes)

    assert schedule.at_time  # validated on the model
    hour, minute = (int(part) for part in schedule.at_time.split(":"))
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    allowed = set(schedule.days) if schedule.days else set(range(7))
    while candidate.weekday() not in allowed:
        candidate += timedelta(days=1)
    return candidate


class TaskScheduler:
    """Fire due schedules, record what happened, and never die doing it."""

    def __init__(
        self,
        storage: StorageManager,
        profile_manager: ProfileManager,
        now: Callable[[], datetime] = datetime.now,
        poll_seconds: float = 5.0,
    ) -> None:
        self.storage = storage
        self.profiles = profile_manager
        self._now = now
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        # Launch schedules with run_minutes: profile id -> when to close its
        # browser. In memory only — if the process dies, its child browsers die
        # with it, so there is nothing left to close after a restart.
        self._timed_closes: dict[str, datetime] = {}

    def clock(self) -> datetime:
        """The scheduler's idea of now, so callers plan with the clock tests inject."""
        return self._now()

    async def start(self) -> None:
        """Reconcile what fell due while we were down, then start the loop."""
        await self.reconcile_missed()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        logger.info("Task scheduler started")

    async def stop(self) -> None:
        """Stop the loop. Safe to call without start()."""
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Task scheduler stopped")

    async def reconcile_missed(self) -> None:
        """Skip — never replay — occurrences that fell while the process was down.

        A scheduled browser launch is only meaningful at its time: warming a
        profile at 09:00 the next morning because the machine was off at 03:00
        is at best pointless, and replaying a backlog would open every missed
        browser at once on startup. A version refresh simply happens at its
        next occurrence instead. The gap is recorded as one ``missed`` run per
        schedule — one row, however many occurrences fell in it — so it is
        visible in the history rather than silently absorbed.
        """
        now = self._now()
        for schedule in await self.storage.list_schedules():
            if not schedule.enabled:
                continue
            if schedule.next_run_at is not None and schedule.next_run_at <= now:
                await self.storage.log_schedule_run(
                    ScheduleRun(
                        schedule_id=schedule.id,
                        started_at=now,
                        finished_at=now,
                        outcome=ScheduleRunOutcome.MISSED,
                        message=(
                            f"Due {schedule.next_run_at:%Y-%m-%d %H:%M} while the app "
                            "was not running; waiting for the next occurrence"
                        ),
                    )
                )
            if schedule.next_run_at is None or schedule.next_run_at <= now:
                schedule.next_run_at = next_run_after(schedule, now)
                schedule.updated_at = now
                await self.storage.save_schedule(schedule)

    async def _loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                logger.error(f"Scheduler tick failed: {exc}")
            try:
                # asyncio.TimeoutError: builtins.TimeoutError only matches it on 3.11+.
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:  # noqa: UP041 - py310 support
                pass

    async def tick(self) -> None:
        """One pass: close timed-out sessions, then fire everything due.

        Due schedules run sequentially, each with its own error handling — one
        that raises is recorded and the next still runs. Reading the table
        every pass costs a few rows and means an edit made through the API is
        picked up within one poll, with no cache to invalidate.
        """
        now = self._now()

        for profile_id, close_at in list(self._timed_closes.items()):
            if close_at <= now:
                del self._timed_closes[profile_id]
                try:
                    # A no-op if the user already closed the window themselves.
                    await self.profiles.close_browser(profile_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Timed close of {profile_id} failed: {exc}")

        for schedule in await self.storage.list_schedules():
            if not schedule.enabled or schedule.next_run_at is None:
                continue
            if schedule.next_run_at <= now:
                # Advance before executing, so a run that crashes the process
                # cannot leave a past next_run_at that fires again on startup.
                schedule.next_run_at = next_run_after(schedule, now)
                schedule.updated_at = now
                await self.storage.save_schedule(schedule)
                await self.execute(schedule)

    async def run_now(self, schedule: Schedule) -> ScheduleRun:
        """Execute a schedule immediately, leaving its next planned run alone.

        Works on a disabled schedule too: pressing "run now" is the user doing
        the task by hand, not the timer firing.
        """
        return await self.execute(schedule)

    async def execute(self, schedule: Schedule) -> ScheduleRun:
        """Run one schedule's action and record how it went."""
        started = self._now()
        outcome = ScheduleRunOutcome.ERROR
        message: str | None = None

        try:
            profile = await self.profiles.get_profile(schedule.profile_id)
            if profile is None:
                # Deleting a profile deletes its schedules, so this is a race,
                # not a normal path — but a schedule that can never succeed
                # again must not keep firing every occurrence forever.
                message = "Profile no longer exists; schedule disabled"
                schedule.enabled = False
                schedule.updated_at = started
                await self.storage.save_schedule(schedule)
            elif schedule.action == ScheduleAction.LAUNCH:
                if self.profiles.browser_sessions.is_running(schedule.profile_id):
                    outcome = ScheduleRunOutcome.SKIPPED
                    message = "Browser already running; left it alone"
                else:
                    await self.profiles.launch_browser(schedule.profile_id)
                    outcome = ScheduleRunOutcome.OK
                    message = "Browser launched"
                    if schedule.run_minutes:
                        self._timed_closes[schedule.profile_id] = started + timedelta(
                            minutes=schedule.run_minutes
                        )
                        message += f"; closing after {schedule.run_minutes} min"
            else:  # ScheduleAction.REFRESH_BROWSER
                refreshed = await self.profiles.refresh_browser_version(schedule.profile_id)
                outcome = ScheduleRunOutcome.OK
                if refreshed is not None and refreshed.fingerprint is not None:
                    from . import fingerprint_store

                    major = fingerprint_store.browser_major(refreshed.fingerprint)
                    message = f"Pin moved onto Firefox {major}" if major else "Pin refreshed"
                else:
                    message = "Pin refreshed"
        except Exception as exc:  # noqa: BLE001 - one bad run must not kill the loop
            message = str(exc) or exc.__class__.__name__
            logger.warning(f"Schedule {schedule.id} ({schedule.action}) failed: {message}")

        run = ScheduleRun(
            schedule_id=schedule.id,
            started_at=started,
            finished_at=self._now(),
            outcome=outcome,
            message=message,
        )
        return await self.storage.log_schedule_run(run)
