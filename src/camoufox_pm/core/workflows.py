"""Durable receipts, bounded execution and explicit replay for managed browsers."""

import asyncio
import sys
from collections import deque
from contextlib import nullcontext
from datetime import datetime
from uuid import uuid4

from loguru import logger

from camoufox_pm.api.principal import Principal
from camoufox_pm.config import get_settings

from .access import AccessControl
from .browser_session import BrowserLaunchError
from .leases import ProfileLocked
from .sync_admission import profile_admission
from .sync_guard import guard_profile
from .workflow_models import WorkflowDefinition, WorkflowStart, planned_after
from .workflow_store import WorkflowConflict, WorkflowStore

if sys.version_info >= (3, 11):
    from asyncio import timeout as launch_deadline
else:
    # The upstream timeout context was backported for Python 3.10. Unlike
    # wait_for on 3.10/3.11 it does not move work into a second task, which would
    # correctly fail the task-bound profile admission check.
    from async_timeout import timeout as launch_deadline


class WorkflowDenied(ValueError):
    pass


def safe_failure_code(error):
    """Fixed troubleshooting categories; never export exception messages/classes."""
    if isinstance(error, asyncio.TimeoutError):
        return "timeout"
    if isinstance(error, BrowserLaunchError):
        return "browser_startup"
    if isinstance(error, ConnectionError):
        return "proxy_connection"
    if isinstance(error, ValueError):
        return "configuration"
    if isinstance(error, OSError):
        return "filesystem"
    if isinstance(error, KeyError):
        return "session_unavailable"
    if isinstance(error, WorkflowConflict):
        return "operation_conflict"
    return "unexpected"


async def workflow_owner(storage, actor, session_hash=None):
    user = await storage.get_user_by_username(actor.username) if actor.kind == "session" else None
    if actor.kind == "session":
        if not user or not session_hash:
            raise WorkflowDenied("Workflow access denied.")
        # Resolve identity before rechecking its immutable session binding: an
        # in-flight cookie must not inherit a newly recreated username between
        # middleware authentication and this lookup (including across processes).
        session = await storage.get_session(session_hash)
        if (
            not session
            or session["user_id"] != user["id"]
            or datetime.fromisoformat(session["expires_at"]) <= datetime.now()
        ):
            raise WorkflowDenied("Workflow access denied.")
    return {"kind": actor.kind, "username": actor.username, "user_id": user["id"] if user else None}


class WorkflowEngine:
    def __init__(self, storage, profiles):
        self.storage = storage
        self.profiles = profiles
        self.store = WorkflowStore(storage)
        self.maintenance_gate = None
        self.jobs: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancellations: dict[str, asyncio.Event] = {}
        self.slots = asyncio.Semaphore(4)
        self._service: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self.available = False
        self.launch_timeout = 120.0

    def admission(self):
        return self.maintenance_gate.operation() if self.maintenance_gate else nullcontext()

    async def start(self):
        if self._stop.is_set():
            raise WorkflowConflict("Workflow execution is unavailable in this manager.")
        if self._service is not None and self._service.done():
            self._service = None
            self._ready = asyncio.Event()
        if self._service is None:
            self._service = asyncio.create_task(self._serve(), name="workflow-owner")
        await self._ready.wait()
        if not self.available:
            raise WorkflowConflict("Workflow execution is unavailable in this manager.")

    async def _serve(self):
        try:
            # One executor per installation, held in its own task so both the
            # filesystem lock and ContextVar token are released in that context.
            async with profile_admission(self.profiles, "_workflow_engine"):
                with self.admission():
                    self.store.interrupted()
                    now = datetime.now()
                    for value in self.store.list():
                        if (
                            value["next_run_at"]
                            and datetime.fromisoformat(value["next_run_at"]) <= now
                        ):
                            self._missed(value, now)
                self.available = True
                self._ready.set()
                await self._stop.wait()
        except Exception:
            # Another manager's live executor must never be reconciled away.
            self.available = False
            self._ready.set()
            logger.info(
                "Workflow executor unavailable; another manager or recovery may own admission"
            )
        finally:
            self.available = False

    async def stop(self):
        self.available = False
        for event in self.cancellations.values():
            event.set()
        if self.tasks:
            # Accepted browser actions drain before the owning service releases
            # its installation lock. Never cancel a launch/teardown halfway.
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        self._stop.set()
        if self._service:
            await self._service

    async def authorized(self, owner, profile_id, session_hash=None):
        kind = owner["kind"]
        if kind == "session":
            user = await self.storage.get_user_by_username(owner["username"])
            if not user or user["id"] != owner["user_id"]:
                return False
            if session_hash:
                session = await self.storage.get_session(session_hash)
                if (
                    not session
                    or session["username"] != owner["username"]
                    or datetime.fromisoformat(session["expires_at"]) <= datetime.now()
                ):
                    return False
        elif kind == "anonymous_local":
            if await self.storage.count_users() or get_settings().api_key:
                return False
        elif kind == "legacy_api_key":
            if not get_settings().api_key:
                return False
        else:
            return False
        return await AccessControl(self.storage).can_profile(
            Principal(kind, owner["username"]), profile_id, "launch"
        )

    async def submit(self, value, request: WorkflowStart, session_hash=None):
        await self.start()
        if value["revision"] != request.revision:
            raise WorkflowConflict("Workflow changed. Reload before saving or running.")
        existing = self.store.run(request.request_id)
        if existing:
            if (
                existing["workflow_id"] != value["id"]
                or existing["request"] != request.model_dump()
            ):
                raise WorkflowConflict("This request ID belongs to a different run.")
            # Idempotent recovery returns a receipt, so apply the same current
            # grant/session checks as history reads. Do not recheck row versions:
            # a later profile edit cannot turn recovery into a repeated launch.
            for target in existing["targets"]:
                if not await self.authorized(value["owner"], target["profile_id"], session_hash):
                    raise WorkflowDenied("Workflow access denied.")
            return existing
        if self.store.seen(request.request_id):
            raise WorkflowConflict(
                "Run history expired. Review the targets and use a new request ID."
            )
        if len(self.tasks) >= 4:
            raise WorkflowConflict("Four workflow runs are already active. Wait or cancel a run.")
        if request.retry_of:
            prior = self.store.run(request.retry_of)
            if (
                not prior
                or prior["workflow_id"] != value["id"]
                or prior["revision"] != value["revision"]
                or prior["state"] in {"queued", "running"}
            ):
                raise WorkflowConflict("Reload the completed run before retrying.")
            eligible = {
                row["profile_id"] for row in prior["targets"] if row["state"] != "completed"
            }
            if not {row.profile_id for row in request.targets} <= eligible:
                raise WorkflowConflict("Retry can include only incomplete profiles from this run.")
        for target in request.targets:
            if not await self.authorized(value["owner"], target.profile_id, session_hash):
                raise WorkflowDenied("Workflow access denied.")
            profile = await self.storage.get_profile(target.profile_id)
            if not profile or profile.row_version != target.row_version:
                raise WorkflowConflict("A selected profile changed. Reload and review the targets.")
        definition = WorkflowDefinition(**value["definition"])
        job = {
            "id": request.request_id,
            "workflow_id": value["id"],
            "revision": value["revision"],
            "request": request.model_dump(),
            "state": "queued",
            "cancel_requested": False,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "targets": [
                {
                    **target.model_dump(),
                    "state": "queued",
                    "message": None,
                    "steps": [
                        {"name": step.name, "action": step.action, "state": "queued"}
                        for step in definition.steps
                    ],
                }
                for target in request.targets
            ],
        }
        # Receipt commits before dispatch. Repeated HTTP submissions cannot
        # repeat browser side effects, including after a response is lost.
        self.store.insert_run(job)
        self.jobs[job["id"]] = job
        event = self.cancellations[job["id"]] = asyncio.Event()
        task = asyncio.create_task(
            self._run(job, definition, value["owner"], event, session_hash), name="workflow-run"
        )
        self.tasks[job["id"]] = task
        return self.store.run(job["id"])

    def cancel(self, run_id):
        job = self.jobs.get(run_id)
        if job:
            self.cancellations[run_id].set()
            job["cancel_requested"] = True
            self.store.save_run(job)
        return self.store.run(run_id)

    async def _run(self, job, definition, owner, cancel, session_hash):
        try:
            with self.admission():
                job["state"] = "running"
                self.store.save_run(job)
                waiting = deque(job["targets"])

                async def worker():
                    while waiting:
                        async with self.slots:
                            # Another worker may drain the queue while this one
                            # waits behind another run for a global slot.
                            if not waiting:
                                return
                            row = waiting.popleft()
                            if cancel.is_set():
                                row["state"] = "cancelled"
                            else:
                                await self._target(
                                    job, row, definition, owner, cancel, session_hash
                                )
                            self.store.save_run(job)

                results = await asyncio.gather(
                    *(worker() for _ in range(definition.concurrency)), return_exceptions=True
                )
                if any(isinstance(result, BaseException) for result in results):
                    for row in job["targets"]:
                        if row["state"] in {"queued", "running"}:
                            row.update(
                                state="failed",
                                message="Step failed. Check the profile, proxy and destination before retrying.",
                            )
                for row in job["targets"]:
                    for step in row["steps"]:
                        if step["state"] == "queued":
                            step["state"] = (
                                "cancelled" if row["state"] == "cancelled" else "skipped"
                            )
                states = {row["state"] for row in job["targets"]}
                job["state"] = (
                    "completed"
                    if states == {"completed"}
                    else "cancelled"
                    if states == {"cancelled"}
                    else "partial"
                    if "completed" in states
                    else "failed"
                )
                job["finished_at"] = datetime.now().isoformat()
                self.store.save_run(job)
                await self.storage.append_audit(
                    Principal(owner["kind"], owner["username"]),
                    "workflow_execute",
                    job["workflow_id"],
                    "success"
                    if job["state"] == "completed"
                    else "partial"
                    if job["state"] == "partial"
                    else "failure",
                )
        except Exception:
            # A gate which closed before this task entered owns the DB; leave
            # the durable queued receipt for startup reconciliation, not a write
            # through an active recovery transaction.
            logger.warning("Workflow run stopped unexpectedly; reconciling its sanitized receipt")
            try:
                with self.admission():
                    stored = self.store.run(job["id"])
                    if stored and stored["state"] in {"queued", "running"}:
                        job["state"] = "interrupted"
                        job["finished_at"] = datetime.now().isoformat()
                        for row in job["targets"]:
                            if row["state"] in {"queued", "running"}:
                                row["state"] = "interrupted"
                            for step in row["steps"]:
                                if step["state"] in {"queued", "running"}:
                                    step["state"] = "interrupted"
                        self.store.save_run(job)
            except Exception:
                # An update owner or unavailable DB stays fenced. The next
                # owning startup reconciles this receipt without replay.
                pass
        finally:
            self.tasks.pop(job["id"], None)
            self.jobs.pop(job["id"], None)
            self.cancellations.pop(job["id"], None)

    async def _target(self, job, row, definition, owner, cancel, session_hash):
        profile_id = row["profile_id"]
        owned = None
        page = None
        current = None
        try:
            async with profile_admission(self.profiles, profile_id):
                profile = await self.storage.get_profile(profile_id)
                if not profile or profile.row_version != row["row_version"]:
                    row.update(
                        state="failed",
                        message="A selected profile changed. Reload and review the targets.",
                    )
                    return
                if self.profiles.browser_sessions.is_live(profile_id):
                    row.update(
                        state="skipped",
                        message="Profile is already open or busy. Close it before retrying.",
                    )
                    return
                row["state"] = "running"
                for index, step in enumerate(definition.steps):
                    if cancel.is_set():
                        row["state"] = "cancelled"
                        break
                    await guard_profile(self.profiles, profile_id)
                    if not await self.authorized(owner, profile_id, session_hash):
                        raise WorkflowDenied()
                    current = row["steps"][index]
                    current["state"] = "running"
                    self.store.save_run(job)
                    if step.action == "launch":
                        async with launch_deadline(self.launch_timeout):
                            # Give desktop Firefox an explicit neutral tab. No
                            # destination/authentication challenge belongs in
                            # startup, before Playwright's handlers are ready.
                            await self.profiles.launch_browser(
                                profile_id, args=["-new-tab", "about:blank"]
                            )
                        owned = self.profiles.browser_sessions.active_sessions[profile_id]
                    else:
                        # Never navigate/close a replacement or manually reopened
                        # browser. The run owns the exact new session it launched.
                        if (
                            self.profiles.browser_sessions.active_sessions.get(profile_id)
                            is not owned
                            or owned is None
                        ):
                            raise WorkflowConflict()
                        if step.action == "navigate":
                            if page is None:
                                page = await asyncio.wait_for(owned.context.new_page(), 15)
                            assert page is not None
                            if not await self.authorized(owner, profile_id, session_hash):
                                raise WorkflowDenied()
                            response = await asyncio.wait_for(
                                page.goto(step.url, wait_until="domcontentloaded", timeout=45000),
                                50,
                            )
                            if response is not None and response.status >= 400:
                                raise WorkflowConflict()
                        elif step.action == "wait":
                            try:
                                await asyncio.wait_for(cancel.wait(), step.seconds)
                            except asyncio.TimeoutError:
                                pass
                            else:
                                current["state"] = "cancelled"
                                row["state"] = "cancelled"
                                break
                        else:
                            await self.profiles.close_browser(profile_id)
                            owned = None
                    current["state"] = "completed"
                    self.store.save_run(job)
                else:
                    row["state"] = "completed"
        except WorkflowDenied:
            row.update(
                state="denied", message="Workflow access was revoked. Remaining steps were stopped."
            )
        except ProfileLocked:
            row.update(
                state="skipped",
                message="Profile is already open or busy. Close it before retrying.",
            )
        except Exception as error:
            # Browser exceptions often contain URLs and proxy credentials. No
            # exception text, response bodies, page content or parameters in logs.
            row.update(
                state="failed",
                error_code=safe_failure_code(error),
                message="Step failed. Check the profile, proxy and destination before retrying.",
            )
        finally:
            if current and current["state"] == "running":
                current["state"] = "failed"
            if (
                owned is not None
                and self.profiles.browser_sessions.active_sessions.get(profile_id) is owned
            ):
                try:
                    await self.profiles.close_browser(profile_id)
                except Exception:
                    row.update(
                        state="failed",
                        message="Browser cleanup failed. Stop this profile before retrying.",
                    )

    def _missed(self, value, now):
        self.store.advance(value, planned_after(WorkflowDefinition(**value["definition"]), now))
        job = {
            "id": str(uuid4()),
            "workflow_id": value["id"],
            "revision": value["revision"],
            "state": "missed",
            "cancel_requested": False,
            "started_at": now.isoformat(),
            "finished_at": now.isoformat(),
            "targets": [],
            "request": None,
        }
        self.store.insert_run(job)
        self.store.save_run(job)

    async def tick(self, now=None):
        if not self.available:
            return
        now = now or datetime.now()
        for value in self.store.list():
            if not value["next_run_at"] or datetime.fromisoformat(value["next_run_at"]) > now:
                continue
            definition = WorkflowDefinition(**value["definition"])
            self.store.advance(value, planned_after(definition, now))
            try:
                await self.submit(
                    value,
                    WorkflowStart(
                        request_id=str(uuid4()),
                        revision=value["revision"],
                        targets=definition.scheduled_targets,
                    ),
                )
            except (WorkflowConflict, WorkflowDenied):
                # No backlog and no auto-retry under stale authority or target
                # versions. Keep the next occurrence, record a missed one.
                self._missed(value, now)
