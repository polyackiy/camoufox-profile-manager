"""Browser session lifecycle: launch, monitor, and close Camoufox instances.

Extracted from ``profile_manager`` so profile CRUD and browser control have
clear, independently testable responsibilities.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import psutil
from loguru import logger

try:
    from camoufox.async_api import AsyncCamoufox

    CAMOUFOX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without camoufox installed
    AsyncCamoufox = None  # type: ignore[assignment, misc]
    CAMOUFOX_AVAILABLE = False


class BrowserLaunchError(RuntimeError):
    """Raised when a Camoufox browser fails to launch."""


def _resolve_process_id(browser: Any) -> int | None:
    """Best-effort resolution of the browser OS process id.

    Playwright does not expose the process id through its public API, so this
    walks known internal attributes and returns ``None`` if none are available.
    Unlike the previous implementation it never fabricates a placeholder pid.
    """
    candidates = (
        lambda b: b._browser_process.pid,  # noqa: SLF001
        lambda b: b.browser._impl._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b._impl._connection._transport._proc.pid,  # noqa: SLF001
    )
    for getter in candidates:
        try:
            pid = getter(browser)
            if pid:
                return int(pid)
        except Exception:  # noqa: BLE001 - private attributes vary across versions
            continue
    return None


class BrowserSession:
    """A single running Camoufox browser tied to a profile."""

    def __init__(self, profile_id: str, browser: Any, process_id: int | None = None):
        self.profile_id = profile_id
        self.browser = browser  # AsyncCamoufox context manager instance
        self.process_id = process_id
        self.started_at = datetime.now()
        self.monitor_task: asyncio.Task | None = None

    async def terminate(self) -> None:
        """Close the browser and stop its monitor task."""
        logger.info(f"Terminating browser session for profile {self.profile_id}")

        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass

        if self.browser is not None:
            try:
                await self.browser.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Error closing browser for {self.profile_id}: {exc}")

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

    def __init__(self) -> None:
        self.active_sessions: dict[str, BrowserSession] = {}

    def is_running(self, profile_id: str) -> bool:
        """Return whether a browser is currently tracked for the profile."""
        return profile_id in self.active_sessions

    def list_active(self) -> list[dict[str, Any]]:
        """Return summaries of active sessions, pruning dead OS processes."""
        dead: list[str] = []
        for profile_id, session in self.active_sessions.items():
            if session.process_id and not psutil.pid_exists(session.process_id):
                dead.append(profile_id)
        for profile_id in dead:
            del self.active_sessions[profile_id]
        return [session.info() for session in self.active_sessions.values()]

    async def launch(
        self,
        profile_id: str,
        launch_options: dict[str, Any],
        on_exit: Callable[[str], Awaitable[None]] | None = None,
    ) -> BrowserSession:
        """Launch a Camoufox browser and register a monitored session."""
        if not CAMOUFOX_AVAILABLE:
            raise BrowserLaunchError(
                "Camoufox is not installed. Install it with: pip install 'camoufox[geoip]'"
            )
        if profile_id in self.active_sessions:
            return self.active_sessions[profile_id]

        try:
            camoufox = AsyncCamoufox(**launch_options)
            browser = await camoufox.start()
        except Exception as exc:  # noqa: BLE001
            raise BrowserLaunchError(f"Failed to launch browser: {exc}") from exc

        process_id = _resolve_process_id(browser) or _resolve_process_id(camoufox)
        session = BrowserSession(profile_id, camoufox, process_id)
        session.monitor_task = asyncio.create_task(self._monitor(profile_id, process_id, on_exit))
        self.active_sessions[profile_id] = session
        logger.info(f"Browser launched for profile {profile_id} (pid={process_id})")
        return session

    async def close(self, profile_id: str) -> bool:
        """Close a single browser. Returns ``False`` if it was not running."""
        session = self.active_sessions.pop(profile_id, None)
        if session is None:
            return False
        await session.terminate()
        return True

    async def close_all(self) -> int:
        """Close every active browser and return how many were closed."""
        count = 0
        for profile_id in list(self.active_sessions.keys()):
            if await self.close(profile_id):
                count += 1
        return count

    async def _monitor(
        self,
        profile_id: str,
        process_id: int | None,
        on_exit: Callable[[str], Awaitable[None]] | None,
    ) -> None:
        """Watch the browser process and clean up when it exits."""
        try:
            while True:
                await asyncio.sleep(10)
                if process_id and not psutil.pid_exists(process_id):
                    logger.info(f"Browser process {process_id} for {profile_id} ended")
                    break
        except asyncio.CancelledError:
            raise
        finally:
            self.active_sessions.pop(profile_id, None)
            if on_exit is not None:
                try:
                    await on_exit(profile_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"on_exit handler failed for {profile_id}: {exc}")
