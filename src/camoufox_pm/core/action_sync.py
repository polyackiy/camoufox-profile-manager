"""Manager-owned action queues and teardown for explicitly prepared browsers.

All entry points are internal. A prepared cohort needs a trusted capture gate,
exact browser sessions, and manager-supplied authorization and playback adapters.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from .action_capture import CaptureRejected

if TYPE_CHECKING:
    from .browser_session import BrowserSession

StopReason = Literal[
    "requested",
    "browser_closed",
    "authority_lost",
    "capture_rejected",
    "overflow",
    "deadline",
    "dispatch_failed",
    "browser_changed",
    "channel_closed",
]


@dataclass(frozen=True, slots=True)
class Click:
    sequence: int
    x: int
    y: int


SendClick = Callable[[], Awaitable[None]]
PrepareClick = Callable[[Click], Awaitable[SendClick]]
Authorize = Callable[[tuple["BrowserSession", ...]], Awaitable[bool]]
Target = tuple["BrowserSession", PrepareClick]


class CaptureInput(Protocol):
    def accept(self, value: dict, proof: str) -> dict: ...

    def stop(self) -> None: ...


class ActionSync:
    """Reserve one cohort within the manager's existing browser leases.

    All calls belong to the browser manager's event loop. Target preflight
    may wait but cannot send input. Its returned callable starts the reviewed
    transport command at invocation, with no intervening asynchronous preflight.
    The returned awaitable settles only when the command can send no more input.
    The authorization adapter checks the whole cohort in one decision. The
    send boundary must confirm that the reviewed tab and geometry are current.
    """

    def __init__(self, is_current: Callable[[BrowserSession], bool]) -> None:
        self._is_current = is_current
        self._run: ActionRun | None = None

    def prepare(
        self,
        capture: CaptureInput,
        leader: BrowserSession,
        targets: tuple[Target, ...],
        authorize: Authorize,
        *,
        dispatch_timeout: float = 5,
        queue_capacity: int = 8,
    ) -> ActionRun:
        if self._run is not None and not self._run.stopped.is_set():
            raise RuntimeError("An action synchronization session already owns the browser cohort")
        sessions = (leader, *(target for target, _ in targets))
        if (
            not 1 <= len(targets) <= 3
            or len({session.profile_id for session in sessions}) != len(sessions)
            or not all(self._is_current(session) for session in sessions)
            or not 1 <= queue_capacity <= 32
            or not math.isfinite(dispatch_timeout)
            or not 0 < dispatch_timeout <= 15
        ):
            raise ValueError("Invalid browser cohort or dispatch limits")
        self._run = ActionRun(
            capture,
            sessions,
            targets,
            self._is_current,
            authorize,
            dispatch_timeout,
            queue_capacity,
            self._release,
        )
        return self._run

    def _release(self, run: ActionRun) -> None:
        if self._run is run:
            self._run = None

    def for_browser(self, session: BrowserSession) -> ActionRun | None:
        run = self._run
        if run is not None and any(member is session for member in run.sessions):
            return run
        return None


class ActionRun:
    """A single generation. A new run requires a fresh reviewed capture gate."""

    def __init__(
        self,
        capture: CaptureInput,
        sessions: tuple[BrowserSession, ...],
        targets: tuple[Target, ...],
        is_current: Callable[[BrowserSession], bool],
        authorize: Authorize,
        dispatch_timeout: float,
        capacity: int,
        release: Callable[[ActionRun], None],
    ) -> None:
        self.sessions = sessions
        self._capture = capture
        self._targets = targets
        self._is_current = is_current
        self._authorize = authorize
        self._timeout = dispatch_timeout
        self._release = release
        self._queues = [asyncio.Queue[tuple[Click, float] | None](capacity) for _ in targets]
        self._workers: set[asyncio.Task[None]] = set()
        self._operations: set[asyncio.Task[None]] = set()
        self._state: Literal["paused", "starting", "running", "draining", "stopped"] = "paused"
        self.reason: StopReason | None = None
        self.uncertain = False
        self.draining = asyncio.Event()
        self.stopped = asyncio.Event()

    def _current(self) -> bool:
        return all(self._is_current(session) for session in self.sessions)

    @property
    def running(self) -> bool:
        return self._state == "running"

    async def start(self) -> bool:
        if self._state != "paused":
            return False
        self._state = "starting"
        deadline = asyncio.get_running_loop().time() + self._timeout
        operation = self._own(self._check_start(), name="action-sync-admission")
        try:
            done, _ = await asyncio.wait((operation,), timeout=self._timeout)
        except asyncio.CancelledError:
            self.stop_accepting()
            raise
        if not done or asyncio.get_running_loop().time() >= deadline:
            self.stop_accepting("deadline")
            return False
        if self._state != "starting":
            return False
        if not self._current():
            self.stop_accepting("browser_changed")
            return False
        self._state = "running"
        for target, queue in zip(self._targets, self._queues, strict=True):
            worker = asyncio.create_task(self._work(target, queue), name="action-sync-follower")
            self._workers.add(worker)
            worker.add_done_callback(self._worker_done)
        return True

    async def _check_start(self) -> None:
        try:
            if self._state != "starting":
                return
            if not await self._authorize(self.sessions):
                self.stop_accepting("authority_lost")
                return
        except Exception:
            self.stop_accepting("authority_lost")

    def submit(self, value: dict, proof: str) -> bool:
        if self._state != "running":
            return False
        if not self._current():
            self.stop_accepting("browser_changed")
            return False
        try:
            accepted = self._capture.accept(value, proof)
        except CaptureRejected:
            self.stop_accepting("capture_rejected")
            return False
        # Preserve sequencing for wheel capture. Playback needs its own
        # unit-aware adapter and is limited to clicks in this lifecycle block.
        if accepted["kind"] != "click":
            return False
        if any(queue.full() for queue in self._queues):
            self.stop_accepting("overflow")
            return False
        item = Click(accepted["sequence"], accepted["x"], accepted["y"])
        deadline = asyncio.get_running_loop().time() + self._timeout
        for queue in self._queues:
            queue.put_nowait((item, deadline))
        return True

    async def _apply(self, target: Target, item: Click, deadline: float) -> None:
        if self._state != "running":
            return
        _, prepare = target
        send = await prepare(item)
        if self._state != "running":
            return
        if not await self._authorize(self.sessions):
            self.stop_accepting("authority_lost")
            return
        if self._state != "running":
            return
        if not self._current():
            self.stop_accepting("browser_changed")
            return
        if asyncio.get_running_loop().time() >= deadline:
            self.stop_accepting("deadline")
            return
        # From here the adapter may have dispatched a command. Its return is
        # the only completion acknowledgement, even when a waiter times out.
        await send()

    async def _work(self, target: Target, queue: asyncio.Queue[tuple[Click, float] | None]) -> None:
        while (waiting := await queue.get()) is not None:
            item, deadline = waiting
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.stop_accepting("deadline")
                return
            operation = self._own(self._apply(target, item, deadline), name="action-sync-send")
            done, _ = await asyncio.wait((operation,), timeout=remaining)
            if not done:
                self.uncertain = True
                self.stop_accepting("deadline")
            try:
                await asyncio.shield(operation)
            except Exception:
                # The done callback stops the cohort and records uncertainty.
                return

    def _own(self, operation: Coroutine[None, None, None], *, name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(operation, name=name)
        self._operations.add(task)
        task.add_done_callback(self._operation_done)
        return task

    def _operation_done(self, operation: asyncio.Task[None]) -> None:
        self._operations.discard(operation)
        if operation.cancelled() or operation.exception() is not None:
            self.uncertain = True
            self.stop_accepting("dispatch_failed")
        self._finish_if_drained()

    def _worker_done(self, worker: asyncio.Task[None]) -> None:
        self._workers.discard(worker)
        if worker.cancelled() or worker.exception() is not None:
            self.uncertain = True
            self.stop_accepting("dispatch_failed")
        self._finish_if_drained()

    def _finish_if_drained(self) -> None:
        if self._state == "draining" and not self._workers and not self._operations:
            self._state = "stopped"
            self._release(self)
            self.stopped.set()

    def stop_accepting(self, reason: StopReason = "requested") -> None:
        """Invalidate input and discard waiting actions before yielding control."""
        if self._state in ("draining", "stopped"):
            return
        self.reason = reason
        self._state = "draining"
        self._capture.stop()
        self.draining.set()
        for queue in self._queues:
            while not queue.empty():
                queue.get_nowait()
            if self._workers:
                queue.put_nowait(None)
        self._finish_if_drained()

    async def stop(self, *, timeout: float) -> bool:
        """Wait for owned dispatches. Timeout or caller cancellation keeps ownership."""
        self.stop_accepting()
        if self.stopped.is_set():
            return True
        try:
            await asyncio.wait_for(self.stopped.wait(), timeout)
        except asyncio.TimeoutError:
            return self.stopped.is_set()
        return True
