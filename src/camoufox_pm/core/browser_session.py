"""Browser session lifecycle: launch, monitor, and close Camoufox instances.

Extracted from ``profile_manager`` so profile CRUD and browser control have
clear, independently testable responsibilities.

Cleanup is driven primarily by Playwright's ``close``/``disconnected`` events, so
a user closing the browser window is detected and the session is torn down. OS
process polling is only a best-effort fallback: with a persistent context the
resolvable pid is Playwright's driver process, not Firefox, so it is not a
reliable window-close signal on its own.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import psutil
from loguru import logger

from ..config import get_settings
from .database import StorageManager

try:
    from camoufox.async_api import AsyncCamoufox

    CAMOUFOX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without camoufox installed
    AsyncCamoufox = None  # type: ignore[assignment, misc]
    CAMOUFOX_AVAILABLE = False

ExitHandler = Callable[[str], Awaitable[None]]


class BrowserLaunchError(RuntimeError):
    """Raised when a Camoufox browser fails to launch."""


def _resolve_process_id(obj: Any) -> int | None:
    """Best-effort resolution of a driver/browser OS process id.

    Playwright does not expose this publicly, so this walks known internal
    attributes and returns ``None`` if none are available. It never fabricates a
    placeholder pid. Note: for a persistent context this is the Playwright driver
    process, used only as a forceful-kill fallback.
    """
    candidates = (
        lambda b: b._browser_process.pid,  # noqa: SLF001
        lambda b: b.browser._impl._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b._impl._connection._transport._proc.pid,  # noqa: SLF001
    )
    for getter in candidates:
        try:
            pid = getter(obj)
            if pid:
                return int(pid)
        except Exception:  # noqa: BLE001 - private attributes vary across versions
            continue
    return None


class BrowserSession:
    """A single running Camoufox browser tied to a profile."""

    def __init__(self, profile_id: str, camoufox: Any, process_id: int | None = None):
        self.profile_id = profile_id
        self.camoufox = camoufox  # AsyncCamoufox context manager instance
        self.process_id = process_id
        self.started_at = datetime.now()
        self.monitor_task: asyncio.Task | None = None
        self.on_exit: ExitHandler | None = None
        self._terminated = False

    async def terminate(self) -> None:
        """Close the browser and stop its monitor task. Safe to call twice."""
        if self._terminated:
            return
        self._terminated = True
        logger.info(f"Terminating browser session for profile {self.profile_id}")

        # The monitor can be the caller here (_monitor -> _handle_exit -> terminate),
        # and a task cannot await itself; cancelling is enough in that case.
        monitor = self.monitor_task
        if monitor and not monitor.done():
            monitor.cancel()
            if asyncio.current_task() is not monitor:
                try:
                    await monitor
                except asyncio.CancelledError:
                    pass

        if self.camoufox is not None:
            try:
                await self.camoufox.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Error closing browser for {self.profile_id}: {exc}")

        # Best-effort: make sure the driver process is gone.
        if self.process_id:
            try:
                process = psutil.Process(self.process_id)
                process.terminate()
                try:
                    process.wait(timeout=5)
                except psutil.TimeoutExpired:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Error killing process {self.process_id}: {exc}")

    def info(self) -> dict[str, Any]:
        """Return a serializable summary of this session."""
        return {
            "profile_id": self.profile_id,
            "process_id": self.process_id,
            "started_at": self.started_at.isoformat(),
        }


class BrowserSessionManager:
    """Track and control the browsers currently running."""

    def __init__(self, storage: StorageManager | None = None, holder: str | None = None) -> None:
        self.active_sessions: dict[str, BrowserSession] = {}
        # Profile id -> number of launches currently inside camoufox.start().
        # A count, not a set: concurrent launches of one profile must not clear
        # each other's mark. Held from before start() until the session is
        # registered or that launch fails.
        self._starting: dict[str, int] = {}
        # The event loop only holds weak references to tasks, so a teardown
        # suspended inside camoufox.__aexit__ could be garbage-collected and take
        # the primary cleanup path with it. Hold a strong reference until done.
        self._exit_tasks: set[asyncio.Task[None]] = set()
        # Lease renewal. Without a storage backend and holder id (unit tests,
        # tools that only watch processes) the manager behaves exactly as before.
        self._storage = storage
        self._holder = holder
        self._heartbeat_task: asyncio.Task[None] | None = None

    def start_heartbeat(self, interval: float = 30.0) -> None:
        """Begin renewing this instance's leases in the background.

        The heartbeat is what keeps a lease alive while a browser runs; when it
        stops (crash, kill -9), the lease outlives the browser by at most its
        TTL and then frees itself. A stolen lease closes the browser instead of
        being fought over: the other machine is already driving that identity.
        """
        if self._storage is None or self._holder is None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))

    async def stop_heartbeat(self) -> None:
        """Stop the renewal loop. Renewal must not race shutdown."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    async def _heartbeat_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self._renew_leases()
            except Exception as exc:  # noqa: BLE001 - one failed beat must not kill the loop
                logger.warning(f"Lease heartbeat failed: {exc}")

    async def _renew_leases(self) -> None:
        """Renew every active lease; close any browser whose lease was lost."""
        if self._storage is None or self._holder is None or not self.active_sessions:
            return
        profile_ids = list(self.active_sessions)
        # Same TTL source as launch (settings.lease_ttl / CPM_LEASE_TTL): a
        # hardcoded beat here would collapse a longer lease and let it expire
        # under a live browser.
        ttl_seconds = get_settings().lease_ttl
        renewed = await self._storage.renew_lease(
            profile_ids, self._holder, ttl_seconds=ttl_seconds
        )
        if renewed == len(profile_ids):
            return
        # Single-profile renewal to learn exactly which leases survive; the
        # bulk update above already extended the ones that did.
        renewed_ids = set()
        for profile_id in profile_ids:
            if (
                await self._storage.renew_lease([profile_id], self._holder, ttl_seconds=ttl_seconds)
                > 0
            ):
                renewed_ids.add(profile_id)
        for profile_id in profile_ids:
            if profile_id in renewed_ids:
                continue
            logger.warning(
                f"Lease on profile {profile_id} was taken over or expired — "
                "another machine may be driving this identity; closing the browser."
            )
            # Not recovering: the identity is being driven elsewhere.
            await self.close(profile_id)

    def is_running(self, profile_id: str) -> bool:
        """Return whether a browser is currently tracked for the profile."""
        return profile_id in self.active_sessions

    def is_live(self, profile_id: str) -> bool:
        """Whether a browser is running OR starting up for the profile.

        is_running() alone is false while camoufox.start() is still being
        awaited, so a lease released on that answer can be taken from a browser
        that is coming up. Lease decisions must use this; user-facing "is it
        open" answers stay on is_running().
        """
        return profile_id in self.active_sessions or profile_id in self._starting

    def list_active(self) -> list[dict[str, Any]]:
        """Return summaries of the active sessions.

        A pure read: it used to drop sessions whose driver process had gone,
        which skipped ``terminate()`` and the exit handler and so leaked the
        Camoufox context. Teardown belongs to the close event and, failing that,
        to :meth:`_monitor`; both route through ``_handle_exit``.
        """
        return [session.info() for session in self.active_sessions.values()]

    async def launch(
        self,
        profile_id: str,
        launch_options: dict[str, Any],
        on_exit: ExitHandler | None = None,
    ) -> BrowserSession:
        """Launch a Camoufox browser and register a monitored session."""
        if not CAMOUFOX_AVAILABLE:
            raise BrowserLaunchError(
                "Camoufox is not installed. Install it with: pip install 'camoufox[geoip]'"
            )
        if profile_id in self.active_sessions:
            return self.active_sessions[profile_id]

        # Counted, not a set membership: two concurrent launches of one profile
        # both mark it, and whichever leaves first would otherwise clear the
        # marker for the other — the loser raises first, so it would erase the
        # winner's mark while the winner is still inside start().
        self._starting[profile_id] = self._starting.get(profile_id, 0) + 1
        try:
            try:
                camoufox = AsyncCamoufox(**launch_options)
                browser = await camoufox.start()
            except Exception as exc:  # noqa: BLE001
                raise BrowserLaunchError(f"Failed to launch browser: {exc}") from exc

            process_id = _resolve_process_id(browser) or _resolve_process_id(camoufox)
            session = BrowserSession(profile_id, camoufox, process_id)
            session.on_exit = on_exit
            self.active_sessions[profile_id] = session
        finally:
            # Decremented only once the session is registered (or this launch
            # died), so is_live() stays true while any launch is still starting.
            remaining = self._starting.get(profile_id, 1) - 1
            if remaining > 0:
                self._starting[profile_id] = remaining
            else:
                self._starting.pop(profile_id, None)

        # Primary signal: the browser/context closing (e.g. the user closes the window).
        self._register_close_handler(browser, profile_id)
        # Fallback: poll the driver pid, only if we could resolve one.
        if process_id:
            session.monitor_task = asyncio.create_task(self._monitor(profile_id, process_id))

        logger.info(f"Browser launched for profile {profile_id} (pid={process_id})")
        return session

    def _register_close_handler(self, browser: Any, profile_id: str) -> None:
        """Wire Playwright close/disconnect events to session cleanup."""
        loop = asyncio.get_running_loop()

        def _on_close(*_: Any) -> None:
            task = loop.create_task(self._handle_exit(profile_id))
            self._exit_tasks.add(task)
            task.add_done_callback(self._exit_tasks.discard)

        for event in ("close", "disconnected"):
            try:
                browser.on(event, _on_close)
            except Exception:  # noqa: BLE001 - Browser vs BrowserContext expose different events
                continue

    async def _handle_exit(self, profile_id: str) -> None:
        """Tear down a session exactly once and notify the exit handler."""
        session = self.active_sessions.pop(profile_id, None)
        if session is None:
            return
        await session.terminate()
        # Our browser is gone, so the lease should not outlive it — unless it
        # was already taken over, in which case the holder guard keeps the new
        # holder's lease intact.
        if self._storage is not None and self._holder is not None:
            try:
                await self._storage.release_lease(profile_id, self._holder)
            except Exception as exc:  # noqa: BLE001 - teardown must not fail on the lease
                logger.warning(f"Could not release the lease for {profile_id}: {exc}")
        if session.on_exit is not None:
            try:
                await session.on_exit(profile_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"on_exit handler failed for {profile_id}: {exc}")

    async def close(self, profile_id: str) -> bool:
        """Close a single browser. Returns ``False`` if it was not running."""
        session = self.active_sessions.pop(profile_id, None)
        if session is None:
            return False
        await session.terminate()
        return True

    async def close_and_release(self, profile_id: str) -> bool:
        """Close a browser and hand its lease back, only if we still hold it."""
        closed = await self.close(profile_id)
        if closed and self._storage is not None and self._holder is not None:
            await self._storage.release_lease(profile_id, self._holder)
        return closed

    async def close_all(self) -> int:
        """Close every active browser and return how many were closed."""
        count = 0
        for profile_id in list(self.active_sessions.keys()):
            if await self.close_and_release(profile_id):
                count += 1
        return count

    async def _monitor(self, profile_id: str, process_id: int) -> None:
        """Fallback watchdog: clean up if the driver process disappears."""
        while psutil.pid_exists(process_id):
            await asyncio.sleep(10)
        await self._handle_exit(profile_id)
