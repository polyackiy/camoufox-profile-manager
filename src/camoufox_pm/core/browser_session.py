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
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil
from loguru import logger

from ..config import get_settings
from .socks_relay import SocksRelay

if TYPE_CHECKING:
    # Type-only: importing StorageManager at runtime would be a cycle, since
    # database.py has no need of this module at all.
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


BROWSER_EXIT_UNCONFIRMED = (
    "Browser exit could not be confirmed. Close remaining browsers, then retry "
    "Stop or restart the manager."
)
BROWSER_EXITS_UNCONFIRMED = (
    "Some browser exits could not be confirmed. Retry Stop for active profiles."
)


class BrowserTerminationError(RuntimeError):
    """Exit could not be confirmed; retain the tracked session and lease."""


class _OwnedProcessTree:
    """Pin driver/descendant birth identities before SDK teardown loses ancestry."""

    def __init__(self, pid: int | None):
        self.root: psutil.Process | None = None
        self.children: set[psutil.Process] = set()
        self.uncertain = False
        if pid is not None:
            try:
                candidate = psutil.Process(pid)
                if candidate.pid == os.getpid() or not any(
                    parent.pid == os.getpid() for parent in candidate.parents()
                ):
                    self.uncertain = True
                else:
                    self.root = candidate
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True
        self.refresh()

    def refresh(self) -> None:
        if self.root is None:
            return
        try:
            self.children.update(self.root.children(recursive=True))
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            self.uncertain = True

    def _wait(self, processes: list[psutil.Process]) -> list[psutil.Process]:
        try:
            # Exited/reused identities must not make us wait on a new PID owner.
            current = [process for process in processes if process.is_running()]
            return psutil.wait_procs(current, timeout=5)[1]
        except psutil.Error:
            # Try every remaining owned signal even if one exit is unreadable.
            # No private process exception text leaves the lifecycle boundary.
            self.uncertain = True
            return processes

    def finish(self) -> None:
        # Give already-closing descendants their own grace period before any
        # Windows TerminateProcess/SIGTERM fallback. Never scan/kill by name.
        self.refresh()
        alive = self._wait(list(self.children))
        targets = alive + ([self.root] if self.root is not None else [])
        for process in targets:
            try:
                process.terminate()  # psutil checks the pinned PID/birth identity.
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True
        alive = self._wait(targets)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True
        alive = self._wait(alive)
        if alive or self.uncertain:
            raise BrowserTerminationError(BROWSER_EXIT_UNCONFIRMED)


def _browser_launch_options(launch_options: dict[str, Any]) -> dict[str, Any]:
    """Preserve declared screens and keep Linux desktop input methods usable.

    Playwright's focus test mode prevents native IME composition from committing
    in headed Firefox. PyInstaller also prepends its bundled libraries to
    LD_LIBRARY_PATH; those GTK/GLib versions can be incompatible with the host's
    input-method module. Restore the pre-bundle path for the browser child only.
    Explicit preferences and environments remain under the caller's control.
    """
    options = launch_options.copy()
    config = options.get("config")
    if (
        not options.get("headless", False)
        and "screen" not in options
        and isinstance(config, dict)
        and all(
            type(config.get(key)) is int and config[key] > 0
            for key in ("screen.width", "screen.height")
        )
    ):
        from camoufox.fingerprints import Screen

        # This profile already declares its screen. Do not add unrelated host
        # monitor bounds to fpgen's intermediate sample: some display/model
        # combinations raise RestrictiveConstraints before the saved config is
        # applied. The public empty Screen disables only that implicit bound;
        # it preserves the pin and any explicitly supplied Screen constraint.
        options["screen"] = Screen()
    if sys.platform != "linux":
        return options

    if not options.get("headless", False):
        prefs = dict(options.get("firefox_user_prefs") or {})
        prefs.setdefault("focusmanager.testmode", False)
        options["firefox_user_prefs"] = prefs

    if getattr(sys, "frozen", False) and options.get("env") is None:
        env = os.environ.copy()
        original_path = env.get("LD_LIBRARY_PATH_ORIG")
        if original_path:
            env["LD_LIBRARY_PATH"] = original_path
        else:
            env.pop("LD_LIBRARY_PATH", None)
        options["env"] = env
    return options


def _replace_preferences(path: Path, content: bytes) -> None:
    """Replace a small preferences file without leaving a partial write."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.close()
            if path.exists():
                temporary.chmod(path.stat().st_mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def _linux_ime_startup_preferences(options: dict[str, Any]) -> Iterator[None]:
    """Apply focus preferences before GTK creates its input context.

    Playwright 1.60 sends firefox_user_prefs over Browser.enable after startup.
    That is too late for native IME focus: candidates appear but do not commit.
    Temporarily append the preference to this profile's user.js so Firefox
    reads it at startup, then restore the original file after launch or failure.
    """
    directory = options.get("user_data_dir")
    if (
        sys.platform != "linux"
        or options.get("headless")
        or not options.get("persistent_context")
        or not directory
    ):
        yield
        return

    path = Path(directory) / "user.js"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_bytes() if path.exists() else None
    preference = options["firefox_user_prefs"]["focusmanager.testmode"]
    content = (original or b"") + (
        f'\nuser_pref("focusmanager.testmode", {json.dumps(preference)});\n'
    ).encode()
    _replace_preferences(path, content)
    try:
        yield
    finally:
        try:
            # Preserve a concurrent edit, or a preferences file rewritten by an
            # older Playwright release, instead of silently overwriting it.
            if path.exists() and path.read_bytes() == content:
                if original is None:
                    path.unlink()
                else:
                    _replace_preferences(path, original)
        except OSError as exc:
            # Cleanup must not orphan a browser which has already started.
            logger.warning(f"Could not restore browser startup preferences: {exc}")


def _resolve_process_id(obj: Any) -> int | None:
    """Best-effort resolution of a driver/browser OS process id.

    Playwright does not expose this publicly, so this walks known internal
    attributes and returns ``None`` if none are available. It never fabricates a
    placeholder pid. Note: for a persistent context this is the Playwright driver
    process, used only as a forceful-kill fallback.
    """
    candidates = (
        lambda b: b._browser_process.pid,  # noqa: SLF001
        lambda b: b.browser._impl_obj._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b._impl_obj._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b._connection._transport._proc.pid,  # noqa: SLF001
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


async def _drain_cleanup(task: asyncio.Task) -> Any:
    """Let owned teardown finish even when its request is cancelled repeatedly."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class BrowserSession:
    """A single running Camoufox browser tied to a profile."""

    def __init__(
        self,
        profile_id: str,
        camoufox: Any,
        process_id: int | None = None,
        socks_relay: SocksRelay | None = None,
    ):
        self.profile_id = profile_id
        self.camoufox = camoufox  # AsyncCamoufox context manager instance
        self.process_id = process_id
        self._owned_processes = _OwnedProcessTree(process_id)
        self.started_at = datetime.now()
        self.monitor_task: asyncio.Task | None = None
        self.on_exit: ExitHandler | None = None
        self.on_stopped: ExitHandler | None = None
        self._terminated = False
        self.socks_relay = socks_relay
        self._termination_task: asyncio.Task[None] | None = None
        self.context: Any = None

    async def terminate(self) -> None:
        """Drain one strongly owned teardown, including after caller cancellation."""
        if self._terminated:
            return
        if self._termination_task is None or self._termination_task.done():
            self._termination_task = asyncio.create_task(self._terminate())
        await _drain_cleanup(self._termination_task)

    async def _terminate(self) -> None:
        logger.info(f"Terminating browser session for profile {self.profile_id}")
        # Do not await the monitor: it may itself be waiting for this teardown.
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()

        self._owned_processes.refresh()
        cancelled = False
        if self.socks_relay is not None:
            try:
                await self.socks_relay.close()
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                logger.warning(f"Proxy relay close failed: {type(exc).__name__}")

        if self.camoufox is not None:
            try:
                await self.camoufox.__aexit__(None, None, None)
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                logger.warning(f"Browser context close failed: {type(exc).__name__}")

        # Blocking process waits must not freeze API/event-loop admission. The
        # strongly owned task drains this worker even after caller cancellation.
        await asyncio.to_thread(self._owned_processes.finish)
        self._terminated = True
        if cancelled:
            raise asyncio.CancelledError

    def info(self) -> dict[str, Any]:
        """Return a serializable summary of this session."""
        return {
            "profile_id": self.profile_id,
            "process_id": self.process_id,
            "started_at": self.started_at.isoformat(),
        }


class BrowserSessionManager:
    """Track and control the browsers currently running."""

    def __init__(self, storage: "StorageManager | None" = None, holder: str | None = None) -> None:
        self.active_sessions: dict[str, BrowserSession] = {}
        # Profile id -> how many launches are currently inside camoufox.start().
        # A count rather than a set, because two concurrent launches of one
        # profile both mark it and the first to leave would otherwise clear the
        # mark while the other is still starting.
        self._starting: dict[str, int] = {}
        # The event loop only holds weak references to tasks, so a teardown
        # suspended inside camoufox.__aexit__ could be garbage-collected and take
        # the primary cleanup path with it. Hold a strong reference until done.
        self._exit_tasks: set[asyncio.Task[None]] = set()
        self._closing: dict[str, asyncio.Task[bool]] = {}
        self._release_after_close: set[str] = set()
        # Lease bookkeeping. Without a storage and a holder id (unit tests, and
        # anything that only watches processes) every lease call below is a
        # no-op and this class behaves exactly as it did before leases existed.
        self._storage = storage
        self._holder = holder
        self.on_stopped: ExitHandler | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    def start_heartbeat(self, interval: float = 30.0) -> None:
        """Begin renewing this process's leases in the background.

        The heartbeat is the difference between "this profile is running" and
        "this profile was running when someone last crashed": while it beats
        the lease stays alive, and when it stops — SIGKILL, a power cut — the
        lease outlives the browser by at most its TTL and then frees itself.
        """
        if self._storage is None or self._holder is None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))

    async def stop_heartbeat(self) -> None:
        """Stop the renewal loop, and wait for the beat in flight to finish."""
        if self._heartbeat_task is None:
            return
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
            except Exception as exc:  # noqa: BLE001 - one bad beat must not stop the loop
                logger.warning(f"Lease heartbeat failed: {exc}")

    async def _renew_leases(self) -> None:
        """Renew every lease we hold, and close any browser whose lease is gone."""
        if self._storage is None or self._holder is None or not self.active_sessions:
            return
        profile_ids = list(self.active_sessions)
        # The configured TTL, not a constant: a hardcoded value here would
        # shorten a deliberately long lease on every beat and let it expire
        # under a browser that is still running.
        ttl_seconds = get_settings().lease_ttl
        renewed = await self._storage.renew_lease(profile_ids, self._holder, ttl_seconds)
        if renewed == len(profile_ids):
            return
        # Something was taken from us. The bulk update already renewed whatever
        # survived; ask one at a time only to learn which ones those were.
        for profile_id in profile_ids:
            if await self._storage.renew_lease([profile_id], self._holder, ttl_seconds) > 0:
                continue
            logger.warning(
                f"Lost the lease on profile {profile_id} — another instance may be "
                "driving this identity; closing the browser."
            )
            # No attempt to take it back: whoever holds it now is already
            # running the profile, and racing them is the corruption itself.
            await self.close(profile_id)

    def is_running(self, profile_id: str) -> bool:
        """Return whether a browser is currently tracked for the profile."""
        return profile_id in self.active_sessions

    def is_live(self, profile_id: str) -> bool:
        """Whether a browser is running *or* still starting for the profile.

        ``is_running`` is false for the whole time ``camoufox.start()`` is
        being awaited, so a lease released on that answer can be taken away
        from a browser that is coming up. Lease decisions use this; the
        user-facing "is it open" answers stay on ``is_running``.
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

        # Counted before the first await: from here until the session is
        # registered, is_live() says this profile is coming up, and nothing
        # may hand its lease away.
        self._starting[profile_id] = self._starting.get(profile_id, 0) + 1
        relay: SocksRelay | None = None
        camoufox = None
        cookie_directory: Path | None = None
        pending_cookies = None
        try:
            try:
                options = _browser_launch_options(launch_options)
                proxy = options.get("proxy") or {}
                server = proxy.get("server", "")
                if server.startswith("socks4://") or (
                    server.startswith("socks5://")
                    and (proxy.get("username") or proxy.get("password"))
                ):
                    relay = SocksRelay(
                        proxy["server"], proxy.get("username"), proxy.get("password")
                    )
                    await relay.start()
                    options["proxy"] = relay.browser_proxy
                if server.startswith(("socks4://", "socks5://")):
                    prefs = dict(options.get("firefox_user_prefs") or {})
                    prefs["network.proxy.socks_remote_dns"] = True
                    options["firefox_user_prefs"] = prefs
                if options.get("persistent_context") and options.get("user_data_dir"):
                    from .cookie_transfer import take_pending_cookies

                    cookie_directory = Path(options["user_data_dir"])
                    pending_cookies = take_pending_cookies(cookie_directory)
                with _linux_ime_startup_preferences(options):
                    camoufox = AsyncCamoufox(**options)
                    browser = await camoufox.start()
                    if pending_cookies is not None:
                        from .cookie_transfer import apply_pending_cookies

                        assert cookie_directory is not None
                        await apply_pending_cookies(browser, pending_cookies, cookie_directory)
            except Exception as exc:  # noqa: BLE001
                raise BrowserLaunchError(f"Failed to launch browser: {exc}") from exc

            process_id = _resolve_process_id(browser) or _resolve_process_id(camoufox)
            session = BrowserSession(profile_id, camoufox, process_id, socks_relay=relay)
            # Expose the ready Playwright context; automation never reaches into
            # AsyncCamoufox internals or passes destination URLs at startup.
            session.context = browser
            session.on_exit = on_exit
            session.on_stopped = self.on_stopped
            self.active_sessions[profile_id] = session
        except (Exception, asyncio.CancelledError):
            # Keep the starting marker until both browser and relay teardown
            # finish. Cancelling the request must not release a live profile.
            failed_session = BrowserSession(
                profile_id, camoufox, _resolve_process_id(camoufox), socks_relay=relay
            )
            try:
                await failed_session.terminate()
            except BrowserTerminationError:
                # Startup may have produced a browser before the SDK failed.
                # Keep the lease fence and an explicit Close path until exit is
                # confirmed; never turn an unconfirmed child into "not running".
                failed_session.on_stopped = self.on_stopped
                self.active_sessions.setdefault(profile_id, failed_session)
                raise
            finally:
                if cookie_directory is not None and pending_cookies is not None:
                    from .cookie_transfer import discard_pending_ack
                    from .profile_operations import run_file_operation

                    await run_file_operation(
                        discard_pending_ack, cookie_directory, pending_cookies["nonce"]
                    )
            raise
        finally:
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

        def finished(task: asyncio.Task[None]) -> None:
            self._exit_tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                # Observe the exception even when no request awaits a natural
                # close event. Failed sessions remain tracked for explicit Close.
                logger.warning("Browser close callback did not complete; session remains tracked")

        def _on_close(*_: Any) -> None:
            task = loop.create_task(self._handle_exit(profile_id))
            self._exit_tasks.add(task)
            task.add_done_callback(finished)

        for event in ("close", "disconnected"):
            try:
                browser.on(event, _on_close)
            except Exception:  # noqa: BLE001 - Browser vs BrowserContext expose different events
                continue

    async def _handle_exit(self, profile_id: str) -> None:
        """Tear down a session exactly once and notify the exit handler."""
        await self._close_session(profile_id, release_lease=True, notify=True)

    async def _close_session(
        self, profile_id: str, *, release_lease: bool, notify: bool = False
    ) -> bool:
        task = self._closing.get(profile_id)
        if release_lease:
            self._release_after_close.add(profile_id)
        if task is None:
            session = self.active_sessions.get(profile_id)
            if session is None:
                self._release_after_close.discard(profile_id)
                return False

            async def finish() -> bool:
                try:
                    await session.terminate()
                    stopped = getattr(session, "on_stopped", self.on_stopped)
                    if stopped is not None:
                        await stopped(profile_id)
                    if profile_id in self._release_after_close:
                        await self._release_lease(profile_id)
                    if self.active_sessions.get(profile_id) is session:
                        self.active_sessions.pop(profile_id)
                    if notify and session.on_exit is not None:
                        try:
                            await session.on_exit(profile_id)
                        except Exception as exc:
                            logger.warning(f"on_exit handler failed for {profile_id}: {exc}")
                    return True
                finally:
                    self._closing.pop(profile_id, None)
                    self._release_after_close.discard(profile_id)

            task = asyncio.create_task(finish())
            self._closing[profile_id] = task
        return await _drain_cleanup(task)

    async def close(self, profile_id: str) -> bool:
        """Close one browser, keeping it tracked until teardown completes."""
        return await self._close_session(profile_id, release_lease=False)

    async def close_and_release(self, profile_id: str) -> bool:
        """Drain browser teardown, then release its lease in the same owned task."""
        return await self._close_session(profile_id, release_lease=True)

    async def _release_lease(self, profile_id: str) -> None:
        """Best-effort lease release. Teardown must not fail on bookkeeping."""
        if self._storage is None or self._holder is None:
            return
        try:
            await self._storage.release_lease(profile_id, self._holder)
        except Exception as exc:  # noqa: BLE001 - a stuck release must not block cleanup
            logger.warning(f"Could not release the lease on {profile_id}: {exc}")

    async def close_all(self) -> int:
        """Close every active browser and return how many were closed."""
        count = 0
        failed = False
        for profile_id in list(self.active_sessions.keys()):
            try:
                if await self.close_and_release(profile_id):
                    count += 1
            except BrowserTerminationError:
                failed = True
        if failed:
            raise BrowserTerminationError(BROWSER_EXITS_UNCONFIRMED)
        return count

    async def _monitor(self, profile_id: str, process_id: int) -> None:
        """Fallback watchdog: clean up if the driver process disappears."""
        while psutil.pid_exists(process_id):
            await asyncio.sleep(10)
        await self._handle_exit(profile_id)
