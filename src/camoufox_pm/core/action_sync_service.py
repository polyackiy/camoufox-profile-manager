"""Explicit manager entry point for a reviewed, local action capture session."""

from __future__ import annotations

import asyncio
import os
import secrets
import shlex
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import psutil

from .action_capture import CaptureRejected
from .action_capture_channel import CaptureBinding, CaptureChannel
from .action_capture_launch import native_registration, prepare_capture_addon
from .action_sync import ActionRun
from .action_sync_authority import ActionSyncAuthority
from .action_sync_tabs import ReviewedTab
from .browser_session import BrowserSession, _drain_cleanup
from .maintenance import MaintenanceGate
from .profile_operations import profile_data_path
from .sync_admission import profile_admission
from .update_permissions import create_private_directory, reject_reparse_paths

if TYPE_CHECKING:
    from playwright.async_api import Page

    from camoufox_pm.api.principal import Principal

    from .profile_manager import ProfileManager


class ActionSyncUnavailable(RuntimeError):
    """Fixed diagnostic for an unavailable prepared session."""

    def __init__(self) -> None:
        super().__init__("Could not prepare action synchronization for these browsers")


def _reviewed_origin(origin: str) -> str:
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme in ("http", "https")
            # Restrict the synthetic playback adapter to controlled fixtures.
            and host in ("localhost", "127.0.0.1")
        ):
            port_suffix = (
                ""
                if port is None or (parsed.scheme, port) in {("http", 80), ("https", 443)}
                else f":{port}"
            )
            canonical = f"{parsed.scheme}://{host}{port_suffix}"
            if origin == canonical:
                return canonical
    except ValueError:
        pass
    raise ActionSyncUnavailable()


def _console_host(root: Path) -> tuple[Path, Path]:
    """Use the known console helper from this installation, or the source interpreter."""
    # Keep a source venv's executable spelling. Resolving its symlink before
    # exec would lose the venv and its installed camoufox_pm package under -I.
    interpreter = Path(sys.executable).absolute()
    if getattr(sys, "frozen", False):
        suffix = ".exe" if sys.platform == "win32" else ""
        executable = interpreter.resolve(strict=True).with_name(f"cpm-capture-host{suffix}")
        reject_reparse_paths(executable)
        if not executable.is_file():
            raise ActionSyncUnavailable()
        return executable, executable
    if sys.platform == "win32":
        raise ActionSyncUnavailable()
    # A framework's launcher can exec a different binary after startup.
    # Bind the native peer to the real interpreter image of this manager.
    image = Path(psutil.Process().exe())
    launcher = root / "native-host"
    launcher.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(str(interpreter))
        + ' -I -m camoufox_pm.core.action_capture_host "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    return launcher, image


class CaptureLaunch:
    """Privileged options used only at the browser launch call site."""

    def __init__(
        self,
        authority: ActionSyncAuthority,
        maintenance: MaintenanceGate,
        channel: CaptureChannel,
        executable: Path,
        addon_id: str,
        addon: Path,
        host_image: Path,
    ) -> None:
        self._authority = authority
        self._maintenance = maintenance
        self.channel = channel
        self._executable = executable
        self._addon_id = addon_id
        self._addon = addon
        self._host_image = host_image

    async def options(self, normal: dict[str, Any]) -> dict[str, Any]:
        from camoufox.utils import launch_options

        # Playwright wire logging can print the whole launch request including
        # the native environment. Fail before handing out a key in that mode.
        debug = os.environ.get("DEBUG", "")
        if "DEBUGP" in os.environ or "pw:" in debug or "*" in debug:
            raise ActionSyncUnavailable()
        if not await self._authority.is_authenticated() or self._maintenance.blocked:
            raise ActionSyncUnavailable()
        public = {
            **normal,
            "addons": [*(normal.get("addons") or []), str(self._addon)],
            "executable_path": str(self._executable),
        }
        if public.pop("persistent_context", False) is not True:
            raise ActionSyncUnavailable()
        if public.get("headless") not in (True, False):
            raise ActionSyncUnavailable()
        # The SDK probes the rendering host and caches its input environment.
        # Build the public options first and give the launch capability solely
        # to the final persistent Playwright context via SDK from_options.
        public.setdefault("pin_cpu_cores", False)
        prepared = await _drain_cleanup(
            asyncio.create_task(asyncio.to_thread(launch_options, **public))
        )
        if not await self._authority.is_authenticated() or self._maintenance.blocked:
            raise ActionSyncUnavailable()
        env = dict(prepared.get("env") or os.environ)
        env["CPM_ACTION_CAPTURE_PROBE"] = self.channel.environment(
            self._addon_id, host_executable=self._host_image
        )
        return {"from_options": {**prepared, "env": env}, "persistent_context": True}


class PreparedActionLeader:
    """One explicitly launched leader and one native tab proposed for review."""

    def __init__(
        self,
        manager: ProfileManager,
        leader: BrowserSession,
        authority: ActionSyncAuthority,
        maintenance: MaintenanceGate,
        channel: CaptureChannel,
        binding: CaptureBinding,
        resources: AsyncExitStack,
    ) -> None:
        self.leader = leader
        self.binding = binding
        self._manager = manager
        self._authority = authority
        self._maintenance = maintenance
        self._channel = channel
        self._resources = resources
        self._owner = asyncio.current_task()
        self._run: ActionRun | None = None
        self._reader: asyncio.Task[None] | None = None
        self._watcher: asyncio.Task[None] | None = None

    async def prepare(
        self,
        confirmed: CaptureBinding,
        followers: tuple[tuple[BrowserSession, Page], ...],
    ) -> ActionRun:
        """Use a confirmed native tab and explicit follower page handles."""
        sessions = (self.leader, *(session for session, _ in followers))
        if (
            self._owner is not asyncio.current_task()
            or self._run is not None
            or self._maintenance.blocked
            or confirmed != self.binding
            or not 1 <= len(followers) <= 3
            or len({session.profile_id for session in sessions}) != len(sessions)
        ):
            raise ActionSyncUnavailable()
        bound = await self._authority.bind_sessions(sessions)
        if bound is None:
            raise ActionSyncUnavailable()
        async with AsyncExitStack() as preparation:
            for profile_id in sorted(session.profile_id for session in sessions[1:]):
                await preparation.enter_async_context(profile_admission(self._manager, profile_id))
            reviewed = []
            for session, page in followers:
                tab = await ReviewedTab.review(
                    session,
                    page,
                    origin=confirmed.origin,
                    width=confirmed.width,
                    height=confirmed.height,
                )
                preparation.push_async_callback(tab.close)
                reviewed.append(tab)

            async def authorize(current: tuple[BrowserSession, ...]) -> bool:
                return (
                    await bound.authorize(current)
                    and not self._maintenance.blocked
                    and self._channel.is_open()
                    and all(tab.is_current() for tab in reviewed)
                )

            if not await authorize(sessions):
                raise ActionSyncUnavailable()
            self._run = self._manager.browser_sessions.action_sync.prepare(
                self._channel,
                self.leader,
                tuple(
                    (session, tab.prepare_click)
                    for (session, _), tab in zip(followers, reviewed, strict=True)
                ),
                authorize,
            )
            run = self._run
            self._channel.on_stop(lambda: run.stop_accepting("channel_closed"))
            self._resources.push_async_callback(preparation.pop_all().aclose)
            self._watcher = asyncio.create_task(self._watch(self._run, authorize))
            return self._run

    async def start(self) -> bool:
        run = self._run
        if self._owner is not asyncio.current_task() or run is None:
            return False
        if not await run.start():
            return False
        try:
            await self._channel.arm(self.binding)
        except asyncio.CancelledError:
            run.stop_accepting()
            raise
        except (CaptureRejected, EOFError, OSError):
            run.stop_accepting("channel_closed")
            return False
        if not run.running:
            return False
        self._reader = asyncio.create_task(self._feed(run))
        return True

    async def _feed(self, run: ActionRun) -> None:
        try:
            while run.running:
                await self._channel.read_event(run.submit)
        except (CaptureRejected, EOFError, OSError):
            pass
        finally:
            run.stop_accepting("channel_closed")

    @staticmethod
    async def _watch(run: ActionRun, authorize) -> None:
        try:
            while not run.draining.is_set():
                try:
                    await asyncio.wait_for(run.draining.wait(), 1)
                except asyncio.TimeoutError:
                    if not await authorize(run.sessions):
                        run.stop_accepting("authority_lost")
        except Exception:
            run.stop_accepting("authority_lost")

    async def close(self) -> None:
        """Stop intake immediately and keep all admission until playback settles."""
        self._channel.stop()
        if self._run is not None:
            self._run.stop_accepting()

        async def finish() -> None:
            pending = tuple(task for task in (self._reader, self._watcher) if task is not None)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if self._run is not None:
                await self._run.stopped.wait()
            await self._channel.close()

        await _drain_cleanup(asyncio.create_task(finish()))


@asynccontextmanager
async def prepare_action_leader(
    manager: ProfileManager,
    principal: Principal | None,
    session_hash: str | None,
    *,
    profile_id: str,
    origin: str,
    headless: bool = False,
) -> AsyncIterator[PreparedActionLeader]:
    """Explicit reviewed launch. A caller owns this context until Stop drains.

    Normal launches and public HTTP routes do not call this entry point. The
    eventual controller keeps preparation and Start in one owned service task.
    """
    from camoufox.multiversion import get_active_path
    from camoufox.pkgman import launch_path

    from .browser_install import ensure_browser_ready

    authority = await ActionSyncAuthority.authenticate(
        manager.storage, manager.lease_holder, principal, session_hash
    )
    if authority is None:
        raise ActionSyncUnavailable()
    origin = _reviewed_origin(origin)
    maintenance = MaintenanceGate(
        Path(manager.storage.db.db_path).resolve().parent / "update-control" / "maintenance.sqlite3"
    )
    async with AsyncExitStack() as resources:
        resources.enter_context(maintenance.operation())
        await resources.enter_async_context(profile_admission(manager, profile_id))
        profile = await manager.get_profile(profile_id)
        if profile is None or manager.browser_sessions.is_live(profile_id):
            raise ActionSyncUnavailable()
        ensure_browser_ready()
        active = get_active_path()
        if active is None:
            raise ActionSyncUnavailable()
        executable = Path(launch_path(active)).resolve(strict=True)
        temporary = resources.enter_context(tempfile.TemporaryDirectory(prefix="cpm-capture-"))
        root = Path(temporary).resolve() / "run"
        create_private_directory(root)
        launcher, host_image = _console_host(root)
        channel = CaptureChannel(profile_data_path(manager, profile), executable)
        resources.push_async_callback(channel.close)
        host = "cpm.capture." + secrets.token_hex(12)
        addon_id = f"capture-{secrets.token_hex(12)}@camoufox-pm.local"
        resources.enter_context(native_registration(root, host, addon_id, launcher))
        addon = prepare_capture_addon(
            root, origin=origin, generation=channel.generation, host=host, addon_id=addon_id
        )
        launch = CaptureLaunch(
            authority, maintenance, channel, executable, addon_id, addon, host_image
        )
        prepared = None
        try:
            receipt = await manager._launch_browser(
                profile_id, headless=headless, capture_launch=launch
            )
            leader = manager.browser_sessions.active_sessions.get(profile_id)
            if (
                receipt["status"] != "launched"
                or leader is None
                or leader.action_capture is not channel
                or leader.process_id is None
            ):
                raise ActionSyncUnavailable()
            try:
                binding = await channel.await_ready(psutil.Process(leader.process_id))
            except (CaptureRejected, EOFError, OSError, psutil.Error):
                raise ActionSyncUnavailable() from None
            if (
                binding.origin != origin
                or not await authority.is_authenticated()
                or maintenance.blocked
            ):
                raise ActionSyncUnavailable()
            prepared = PreparedActionLeader(
                manager, leader, authority, maintenance, channel, binding, resources
            )
            yield prepared
        finally:
            if prepared is not None:
                await prepared.close()
            else:
                channel.stop()
                current = manager.browser_sessions.active_sessions.get(profile_id)
                if current is not None and current.action_capture is channel:
                    await manager.browser_sessions.close_and_release(profile_id)
