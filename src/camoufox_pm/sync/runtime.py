"""Closed-profile synchronization coordinated with local admission and durable state."""

from __future__ import annotations

import asyncio
import inspect
import platform
from contextlib import ExitStack
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from uuid import UUID, uuid4

from camoufox_pm.core.profile_operations import (
    profile_data_path,
    profile_operation,
    run_file_operation,
)
from camoufox_pm.core.sync_guard import mark_synchronized
from camoufox_pm.core.update_permissions import create_private_directory, verify_private_path

from .apply import apply_snapshot, recover_apply
from .client import RelayClient
from .control import ControlMutation
from .engine import Binding, EngineError, SyncEngine
from .protocol import SnapshotManifest
from .quiescence import browser_profile_lock
from .service import SyncService
from .snapshots import Compatibility, build_snapshot
from .transfer import SnapshotTransfer

_authorization: ContextVar = ContextVar("sync_runtime_authorization", default=None)


def authorized(function):
    @wraps(function)
    async def operation(self, user_id, *args, authorize=None, **kwargs):
        token = _authorization.set(authorize)
        try:
            return await function(self, user_id, *args, **kwargs)
        finally:
            _authorization.reset(token)

    return operation


def _public(state):
    value = state.model_dump(mode="json")
    if state.pending:
        value["pending"] = {"kind": state.pending.kind, "operation_id": state.pending.operation_id}
    return value


def installed_compatibility() -> Compatibility:
    from camoufox.pkgman import installed_verstr

    operating_system = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(
        platform.system()
    )
    architecture = {
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }.get(platform.machine().lower())
    if operating_system is None or architecture is None:
        raise EngineError("This platform has no tested snapshot compatibility")
    return Compatibility.model_validate(
        {"browser_build": installed_verstr(), "os": operating_system, "architecture": architecture}
    )


class SyncRuntime:
    """One explicit operation uses short-lived keys; locked vaults require user unlock.

    The manager owns API admission. Independent storage checks here prevent background
    work continuing after the enrolled administrator was removed or demoted.
    """

    def __init__(
        self,
        manager,
        maintenance,
        *,
        client_factory=RelayClient,
        compatibility=installed_compatibility,
    ):
        self.manager, self.maintenance = manager, maintenance
        self.service = SyncService(
            Path(manager.storage.db.db_path).absolute().parent / "sync-control"
        )
        self.client_factory, self.compatibility = client_factory, compatibility
        self.cache = self.service.control.directory / "transfers"

    async def _authorize(self, user_id: str, *, allow_revoked: bool = False):
        callback = _authorization.get()
        if callback is not None:
            result = callback()
            if inspect.isawaitable(result):
                await result
        users = await self.manager.storage.list_users()
        if not any(
            user.get("id") == user_id and user.get("role") == "administrator" for user in users
        ):
            raise EngineError("The enrolled administrator is no longer available")
        if not allow_revoked:
            self.service._read(user_id)
        else:
            from .identity import verify_membership
            from .service import Enrollment

            record = self.service.control.get("installation")
            if record is None:
                raise EngineError("Owner binding is unavailable")
            owner = Enrollment.model_validate(record.payload)
            verify_membership(owner.root, owner.membership, trusted_digest=owner.membership_digest)
            if owner.local_user_id != user_id or owner.phase == "abandoned":
                raise EngineError("Owner binding is unavailable")

    async def _session(self, user_id: str, password: str | None):
        await self._authorize(user_id)
        keys = await run_file_operation(self.service.device_keys, user_id, password=password)
        await self._authorize(user_id)
        _, owner = self.service._read(user_id)
        client = self.client_factory(owner.root, owner.device_id, keys)
        loop = asyncio.get_running_loop()
        callback = _authorization.get()

        async def live_authorize():
            token = _authorization.set(callback)
            try:
                await self._authorize(user_id)
            finally:
                _authorization.reset(token)

        def transport_authorize():
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is loop:
                raise EngineError("Relay mutation must run in a worker thread")
            asyncio.run_coroutine_threadsafe(live_authorize(), loop).result()

        client.authorize = transport_authorize
        try:
            return (
                SyncEngine(self.service, user_id, client, authorize=transport_authorize),
                keys,
                client,
            )
        except BaseException:
            client.close()
            raise

    def _directory(self):
        if not self.cache.exists():
            try:
                create_private_directory(self.cache)
            except FileExistsError:
                pass
        verify_private_path(self.cache)
        directory = self.cache / str(uuid4())
        create_private_directory(directory)
        return directory

    def _job_directory(self, local_id):
        key = "runtime/work/" + local_id
        previous = self.service.control.get(key)
        if previous is not None:
            identifier = previous.payload.get("directory")
            if type(identifier) is not str or str(UUID(identifier)) != identifier:
                raise EngineError("Invalid interrupted transfer directory")
            old = self.cache / identifier / "snapshot.zip"
            verify_private_path(old.parent)
            self._cleanup(old.parent)
        directory = self._directory()
        payload = {"directory": directory.name}
        if previous:
            self.service.control.update(key, payload, previous.revision)
        else:
            self.service.control.create(key, payload)
        return directory

    @staticmethod
    def _cleanup(directory):
        if directory is not None:
            verify_private_path(directory)
            (directory / "snapshot.zip").unlink(missing_ok=True)
            (directory / "restored.zip").unlink(missing_ok=True)
            preflight = directory / "preflight"
            if preflight.exists():
                import shutil

                verify_private_path(preflight)
                shutil.rmtree(preflight)

    def _capture(self, profile, directory, compatibility, *, lifecycle=None):
        if lifecycle is None and self.service.control.get("lifecycle/" + profile.id) is not None:
            lifecycle = "active"
        data = profile_data_path(self.manager, profile)
        with browser_profile_lock(data):
            return build_snapshot(
                profile, data, directory / "snapshot.zip", compatibility, lifecycle=lifecycle
            )

    def _require_lifecycle_idle(self, local_id, *, resume=None):
        record = self.service.control.get("lifecycle/" + local_id)
        if (
            record
            and record.payload.get("phase") == "pending"
            and record.payload.get("operation") != resume
        ):
            raise EngineError("Resume the pending lifecycle operation first")

    def _lifecycle_write(self, local_id, state, revision_id, *, phase="complete", **details):
        key = "lifecycle/" + local_id
        previous = self.service.control.get(key)
        payload = {"state": state, "revision_id": revision_id, "phase": phase, **details}
        if previous:
            self.service.control.update(key, payload, previous.revision)
        else:
            self.service.control.create(key, payload)

    async def _set_local_trash(self, user_id, profile, trashed):
        with ExitStack() as locks:
            await run_file_operation(
                locks.enter_context, browser_profile_lock(profile_data_path(self.manager, profile))
            )
            await self._authorize(user_id)
            return await self.manager.storage.set_profile_trashed(profile.id, trashed)

    async def _publish_lifecycle_record(self, engine, local_id, payload):
        path = Path(payload["ciphertext_path"])
        if (
            path.name != "ciphertext"
            or path.parent.parent != self.cache.absolute()
            or path.parent.name != str(UUID(path.parent.name))
        ):
            raise EngineError("Invalid lifecycle ciphertext location")
        verify_private_path(path.parent)
        manifest = SnapshotManifest.model_validate(payload["manifest"])
        return await run_file_operation(
            engine.publish, local_id, manifest, payload["signature"], path
        )

    async def _lifecycle_snapshot(self, engine, keys, local_id, directory):
        from .snapshots import validate_snapshot

        selected = await run_file_operation(engine.reconcile, local_id)
        if selected.pending:
            raise EngineError("Resolve the pending transfer before changing lifecycle")
        transfer = SnapshotTransfer(engine, keys)
        revision = await run_file_operation(
            transfer.download,
            local_id,
            directory / "ciphertext",
            directory / "snapshot.zip",
            require_checkout=False,
        )
        compatibility = await run_file_operation(self.compatibility)
        metadata = await run_file_operation(
            validate_snapshot, directory / "snapshot.zip", directory / "preflight", compatibility
        )
        checked = await run_file_operation(engine.reconcile, local_id)
        if checked.checkpoint != selected.checkpoint or revision != selected.checkpoint.revision_id:
            raise EngineError("Profile changed while authenticating lifecycle; retry")
        return checked, metadata, compatibility

    @authorized
    async def trash(self, user_id, local_id, *, acknowledge_deletion, password=None):
        if acknowledge_deletion is not True:
            raise EngineError("Confirm recoverable deletion on synchronized devices")
        engine, keys, client = await self._session(user_id, password)
        directory = None
        try:
            with self.maintenance.operation():
                async with profile_operation(
                    self.manager, local_id, include_deleted=True, sync_operation=True
                ) as profile:
                    await self._authorize(user_id)
                    job = self.service.control.get("lifecycle/" + local_id)
                    state = await run_file_operation(engine.reconcile, local_id)
                    if (
                        job
                        and job.payload.get("phase") == "complete"
                        and job.payload.get("state") == "trashed"
                    ):
                        if job.payload["revision_id"] != state.checkpoint.revision_id:
                            raise EngineError(
                                "Remote profile changed; authenticate its lifecycle first"
                            )
                        await self._set_local_trash(user_id, profile, True)
                        return _public(state)
                    if (
                        job
                        and job.payload.get("phase") == "pending"
                        and job.payload.get("operation") != "trash"
                    ):
                        raise EngineError("Another lifecycle operation requires recovery")
                    resuming = job is not None and job.payload.get("operation") == "trash"
                    target = job.payload.get("revision_id") if resuming else None
                    if not resuming:
                        await self._authorize(user_id)
                        binding_record, binding = engine._read(local_id)
                        if not engine.may_launch(local_id):
                            raise EngineError("Deletion requires confirmed local checkout")
                        # The lifecycle decision and handoff fence become durable together.
                        # A crash cannot leave an unidentifiable handoff between them.
                        self.service.control.batch(
                            (
                                ControlMutation(
                                    binding_record.key,
                                    binding.model_copy(
                                        update={
                                            "phase": "handoff",
                                            "needs_snapshot": True,
                                            "handoff": True,
                                        }
                                    ).model_dump(mode="json"),
                                    binding_record.revision,
                                ),
                                ControlMutation(
                                    "lifecycle/" + local_id,
                                    {
                                        "state": "trashed",
                                        "revision_id": None,
                                        "phase": "pending",
                                        "operation": "trash",
                                    },
                                    job.revision if job else None,
                                ),
                            )
                        )
                    if state.pending:
                        if not resuming or state.pending.kind not in {"commit", "release"}:
                            raise EngineError("Another transfer is pending")
                        restarted = await run_file_operation(
                            engine.restart_expired_upload, local_id
                        )
                        if restarted:
                            target = None
                            self._lifecycle_write(
                                local_id, "trashed", None, phase="pending", operation="trash"
                            )
                        else:
                            await self._retry(engine, user_id, local_id)
                        state = engine.status(local_id)
                    if target is not None and state.checkpoint.revision_id != target:
                        state = await self._publish_lifecycle_record(engine, local_id, job.payload)
                    if target is None:
                        compatibility = await run_file_operation(self.compatibility)
                        directory = self._job_directory(local_id)
                        await run_file_operation(
                            self._capture, profile, directory, compatibility, lifecycle="trashed"
                        )
                        manifest, signature = await run_file_operation(
                            SnapshotTransfer(engine, keys).encrypt,
                            local_id,
                            directory / "snapshot.zip",
                            directory / "ciphertext",
                        )
                        await self._authorize(user_id)
                        target = manifest.revision_id
                        self._lifecycle_write(
                            local_id,
                            "trashed",
                            target,
                            phase="pending",
                            operation="trash",
                            manifest=manifest.model_dump(mode="json"),
                            signature=signature,
                            ciphertext_path=str((directory / "ciphertext").absolute()),
                        )
                        state = await run_file_operation(
                            engine.publish, local_id, manifest, signature, directory / "ciphertext"
                        )
                    if state.checkpoint.holder_device_id == engine.device_id:
                        state = await run_file_operation(engine.release, local_id)
                    if (
                        state.checkpoint.revision_id != target
                        or state.checkpoint.holder_device_id is not None
                    ):
                        raise EngineError("Deletion handoff is not confirmed")
                    await self._authorize(user_id)
                    await self._set_local_trash(user_id, profile, True)
                    self._lifecycle_write(local_id, "trashed", target)
                    return _public(state)
        finally:
            self._cleanup(directory)
            client.close()

    @authorized
    async def apply_trash(self, user_id, local_id, *, acknowledge_deletion, password=None):
        if acknowledge_deletion is not True:
            raise EngineError("Confirm keeping local changes in recoverable Trash")
        engine, keys, client = await self._session(user_id, password)
        directory = None
        try:
            with self.maintenance.operation():
                async with profile_operation(
                    self.manager, local_id, include_deleted=True, sync_operation=True
                ) as profile:
                    directory = self._job_directory(local_id)
                    state, metadata, _ = await self._lifecycle_snapshot(
                        engine, keys, local_id, directory
                    )
                    if (
                        metadata.lifecycle != "trashed"
                        or state.checkpoint.holder_device_id is not None
                    ):
                        raise EngineError("No authenticated, released deletion is available")
                    await run_file_operation(self._assert_quiescent, profile)
                    await self._authorize(user_id)
                    self._lifecycle_write(
                        local_id,
                        "trashed",
                        state.checkpoint.revision_id,
                        phase="pending",
                        operation="apply-trash",
                    )
                    await self._set_local_trash(user_id, profile, True)
                    self._lifecycle_write(local_id, "trashed", state.checkpoint.revision_id)
                    return _public(state)
        finally:
            self._cleanup(directory)
            client.close()

    def _assert_quiescent(self, profile):
        with browser_profile_lock(profile_data_path(self.manager, profile)):
            pass

    async def _restore_publish(self, engine, keys, user_id, profile, source_revision):
        directory = self._job_directory(profile.id)
        try:
            compatibility = await run_file_operation(self.compatibility)
            await run_file_operation(
                self._capture, profile, directory, compatibility, lifecycle="active"
            )
            manifest, signature = await run_file_operation(
                SnapshotTransfer(engine, keys).encrypt,
                profile.id,
                directory / "snapshot.zip",
                directory / "ciphertext",
            )
            await self._authorize(user_id)
            self._lifecycle_write(
                profile.id,
                "trashed",
                source_revision,
                phase="pending",
                operation="restore",
                publication=manifest.revision_id,
                publication_row_version=profile.row_version,
                publication_storage_path=profile.storage_path,
                manifest=manifest.model_dump(mode="json"),
                signature=signature,
                ciphertext_path=str((directory / "ciphertext").absolute()),
            )
            await run_file_operation(
                engine.publish, profile.id, manifest, signature, directory / "ciphertext"
            )
            await self._authorize(user_id)
            engine.record_applied(profile.id, manifest.revision_id)
            self._lifecycle_write(profile.id, "active", manifest.revision_id)
            return _public(engine.status(profile.id))
        finally:
            self._cleanup(directory)

    @authorized
    async def restore(self, user_id, local_id, *, acknowledge_restore, password=None):
        if acknowledge_restore is not True:
            raise EngineError(
                "Confirm restoration of the authenticated deleted snapshot; local data is backed up"
            )
        engine, keys, client = await self._session(user_id, password)
        directory = None
        try:
            journal = self.service.control.get("apply/" + local_id)
            lifecycle = self.service.control.get("lifecycle/" + local_id)
            if (
                journal
                and journal.payload.get("phase") not in {"complete", "aborted"}
                and lifecycle
                and lifecycle.payload.get("operation") == "restore"
            ):
                await recover_apply(
                    self.manager,
                    local_id,
                    self.service.control,
                    maintenance=self.maintenance,
                    authorize=lambda: self._authorize(user_id),
                )
            with self.maintenance.operation():
                async with profile_operation(
                    self.manager, local_id, include_deleted=True, sync_operation=True
                ) as profile:
                    job = self.service.control.get("lifecycle/" + local_id)
                    state = await run_file_operation(engine.reconcile, local_id)
                    resuming = job is not None and job.payload.get("operation") == "restore"
                    if job and job.payload.get("phase") == "pending" and not resuming:
                        raise EngineError("Finish the pending lifecycle operation first")
                    if resuming and job.payload.get("publication"):
                        target = job.payload["publication"]
                        if profile.row_version != job.payload.get(
                            "publication_row_version"
                        ) or profile.storage_path != job.payload.get("publication_storage_path"):
                            raise EngineError(
                                "Local restored data changed; preserve and recover it first"
                            )
                        if state.pending:
                            restarted = await run_file_operation(
                                engine.restart_expired_upload, local_id
                            )
                            if restarted:
                                return await self._restore_publish(
                                    engine, keys, user_id, profile, job.payload["revision_id"]
                                )
                            await self._retry(engine, user_id, local_id)
                            state = engine.status(local_id)
                        if state.checkpoint.revision_id != target:
                            state = await self._publish_lifecycle_record(
                                engine, local_id, job.payload
                            )
                        if state.checkpoint.holder_device_id != engine.device_id:
                            raise EngineError("Restoration publication is uncertain")
                        await self._authorize(user_id)
                        if (
                            state.installed_revision != target
                            or state.installed_generation != state.checkpoint.checkout_generation
                        ):
                            engine.record_applied(local_id, target)
                        await self._set_local_trash(user_id, profile, False)
                        self._lifecycle_write(local_id, "active", target)
                        return _public(engine.status(local_id))
                    if resuming and state.pending is not None and state.pending.kind == "claim":
                        state = await run_file_operation(engine.claim, local_id)
                    directory = self._job_directory(local_id)
                    state, metadata, compatibility = await self._lifecycle_snapshot(
                        engine, keys, local_id, directory
                    )
                    if metadata.lifecycle != "trashed":
                        raise EngineError("The latest authenticated revision is not deleted")
                    if state.checkpoint.holder_device_id not in {None, engine.device_id}:
                        raise EngineError("Restoration is checked out on another device")
                    await self._authorize(user_id)
                    revision = state.checkpoint.revision_id
                    self._lifecycle_write(
                        local_id, "trashed", revision, phase="pending", operation="restore"
                    )
                    # Rebuild only already authenticated staging with active format2 metadata.
                    values = profile.model_dump(mode="json")
                    values.update(metadata.profile.apply_fields())
                    restored = type(profile).model_validate(values)
                    active_archive = directory / "restored.zip"
                    await run_file_operation(
                        build_snapshot,
                        restored,
                        directory / "preflight" / "data",
                        active_archive,
                        compatibility,
                        lifecycle="active",
                    )
                    if state.checkpoint.holder_device_id is None:
                        state = await run_file_operation(
                            engine.claim, local_id, expected_checkpoint=state.checkpoint
                        )
                    if profile.deleted_at:
                        await self._set_local_trash(user_id, profile, False)
                        profile = await self.manager.storage.get_profile(local_id)
                    expected_version = profile.row_version
                await apply_snapshot(
                    self.manager,
                    local_id,
                    active_archive,
                    compatibility,
                    self.service.control,
                    revision,
                    expected_version,
                    maintenance=self.maintenance,
                    authorize=lambda: self._authorize(user_id),
                )
                await self._record_activation(engine, user_id, local_id, revision)
                async with profile_operation(
                    self.manager, local_id, sync_operation=True
                ) as profile:
                    return await self._restore_publish(engine, keys, user_id, profile, revision)
        finally:
            if directory is not None:
                (directory / "restored.zip").unlink(missing_ok=True)
            self._cleanup(directory)
            client.close()

    @authorized
    async def enable(self, user_id: str, local_id: str, *, password: str | None = None):
        engine, keys, client = await self._session(user_id, password)
        directory = None
        try:
            with self.maintenance.operation():
                async with profile_operation(
                    self.manager, local_id, sync_operation=True
                ) as profile:
                    await self._authorize(user_id)
                    job_key = "runtime/initial/" + local_id
                    job = self.service.control.get(job_key)
                    existing = self.service.control.get("profile/" + local_id)
                    if existing and job is None:
                        raise EngineError("Profile is already synchronized")
                    if job is None:
                        self.service.control.create(job_key, {"phase": "initializing"})
                    elif job.payload.get("phase") not in {"initializing", "complete"}:
                        raise EngineError("Invalid initialization state")
                    compatibility = await run_file_operation(self.compatibility)
                    directory = self._job_directory(local_id)
                    await run_file_operation(self._capture, profile, directory, compatibility)
                    await self._authorize(user_id)
                    remote_id = (
                        engine.status(local_id).checkpoint.profile_id
                        if existing
                        else engine.enable(local_id)
                    )
                    mark_synchronized(self.manager, profile, engine.root.owner_id, remote_id)
                    state = engine.status(local_id)
                    if state.pending is not None:
                        await run_file_operation(engine.restart_expired_upload, local_id)
                        if engine.status(local_id).pending is not None:
                            await self._retry(engine, user_id, local_id)
                        state = engine.status(local_id)
                    if state.checkpoint.revision_id is not None:
                        if not engine.may_launch(local_id):
                            job = self.service.control.get(job_key)
                            if (
                                job.payload.get("revision") != state.checkpoint.revision_id
                                or job.payload.get("row_version") != profile.row_version
                            ):
                                raise EngineError("Initial publication needs recovery")
                            engine.record_applied(local_id, state.checkpoint.revision_id)
                    else:
                        if state.checkpoint.holder_device_id is None:
                            state = await run_file_operation(engine.claim, local_id)
                        if state.checkpoint.holder_device_id != engine.device_id:
                            raise EngineError("Initial profile is held on another device")
                        if state.checkpoint.revision_id is not None:
                            raise EngineError("New profile unexpectedly has remote history")
                        if not engine.may_launch(local_id):
                            engine.record_applied(local_id, None)
                        transfer = SnapshotTransfer(engine, keys)
                        manifest, signature = await run_file_operation(
                            transfer.encrypt,
                            local_id,
                            directory / "snapshot.zip",
                            directory / "ciphertext",
                        )
                        await self._authorize(user_id)
                        job = self.service.control.get(job_key)
                        self.service.control.update(
                            job_key,
                            {
                                "phase": "initializing",
                                "revision": manifest.revision_id,
                                "row_version": profile.row_version,
                            },
                            job.revision,
                        )
                        await run_file_operation(
                            engine.publish, local_id, manifest, signature, directory / "ciphertext"
                        )
                        await self._authorize(user_id)
                        engine.record_applied(local_id, manifest.revision_id)
                    job = self.service.control.get(job_key)
                    self.service.control.update(job_key, {"phase": "complete"}, job.revision)
                    return _public(engine.status(local_id))
        finally:
            try:
                self._cleanup(directory)
            finally:
                client.close()

    @authorized
    async def handoff(self, user_id: str, local_id: str, *, password: str | None = None):
        self._require_lifecycle_idle(local_id)
        engine, keys, client = await self._session(user_id, password)
        directory = None
        try:
            with self.maintenance.operation():
                async with profile_operation(
                    self.manager, local_id, sync_operation=True
                ) as profile:
                    state = engine.status(local_id)
                    if state.pending is not None:
                        return await self._retry(engine, user_id, local_id)
                    await self._authorize(user_id)
                    if state.handoff and not state.needs_snapshot:
                        state = await run_file_operation(engine.reconcile, local_id)
                        if state.phase == "released":
                            return self._finish_release(local_id, state)
                        await self._authorize(user_id)
                        return self._finish_release(
                            local_id, await run_file_operation(engine.release, local_id)
                        )
                    if not state.handoff:
                        engine.begin_handoff(local_id)
                    compatibility = await run_file_operation(self.compatibility)
                    directory = self._job_directory(local_id)
                    await run_file_operation(self._capture, profile, directory, compatibility)
                    manifest, signature = await run_file_operation(
                        SnapshotTransfer(engine, keys).encrypt,
                        local_id,
                        directory / "snapshot.zip",
                        directory / "ciphertext",
                    )
                    await self._authorize(user_id)
                    await run_file_operation(
                        engine.publish, local_id, manifest, signature, directory / "ciphertext"
                    )
                    await self._authorize(user_id)
                    return self._finish_release(
                        local_id, await run_file_operation(engine.release, local_id)
                    )
        finally:
            try:
                self._cleanup(directory)
            finally:
                client.close()

    def _finish_release(self, local_id, state):
        if state.phase == "released" and state.checkpoint.holder_device_id is None:
            queued = self.service.control.get("runtime/dirty/" + local_id)
            if queued is not None:
                self.service.control.update(
                    queued.key, {**queued.payload, "phase": "complete"}, queued.revision
                )
        return _public(state)

    async def _retry(self, engine, user_id: str, local_id: str):
        state = engine.status(local_id)
        pending = state.pending
        if pending is None:
            await self._authorize(user_id)
            state = await run_file_operation(engine.reconcile, local_id)
            if state.handoff and not state.needs_snapshot:
                await self._authorize(user_id)
                state = await run_file_operation(engine.release, local_id)
            return self._finish_release(local_id, state)
        await self._authorize(user_id)
        if pending.kind == "commit":
            path = Path(pending.ciphertext_path or "")
            # Never open arbitrary paths obtained from persisted operation metadata.
            if (
                path.name != "ciphertext"
                or path.parent.parent != self.cache.absolute()
                or path.parent.name != str(UUID(path.parent.name))
            ):
                raise EngineError("Pending encrypted snapshot path is invalid")
            verify_private_path(path.parent)
            manifest = SnapshotManifest.model_validate(pending.body["manifest"])
            state = await run_file_operation(
                engine.publish, local_id, manifest, pending.body["signature"], path
            )
            if state.handoff:
                await self._authorize(user_id)
                state = await run_file_operation(engine.release, local_id)
            else:
                state = engine.record_applied(local_id, manifest.revision_id)
        elif pending.kind == "release":
            state = await run_file_operation(engine.release, local_id)
        elif pending.kind == "claim":
            state = await run_file_operation(engine.claim, local_id)
        else:
            raise EngineError("Owner recovery is required for this pending operation")
        return self._finish_release(local_id, state)

    @authorized
    async def acquire(self, user_id: str, local_id: str, *, password: str | None = None):
        return await self._acquire(user_id, local_id, password=password)

    @authorized
    async def takeover(
        self,
        user_id: str,
        local_id: str,
        *,
        acknowledge_lost_device: bool,
        password: str | None = None,
    ):
        if acknowledge_lost_device is not True:
            raise EngineError(
                "Confirm that the lost browser may still run and existing website sessions cannot be recalled"
            )
        return await self._acquire(user_id, local_id, password=password, lost_device=True)

    async def _acquire(
        self, user_id: str, local_id: str, *, password: str | None = None, lost_device: bool = False
    ):
        self._require_lifecycle_idle(local_id, resume="acquire")
        engine, keys, client = await self._session(user_id, password)
        directory = None
        try:
            with self.maintenance.operation():
                async with profile_operation(
                    self.manager, local_id, include_deleted=True, sync_operation=True
                ) as profile:
                    from .snapshots import validate_snapshot

                    if profile.extensions:
                        raise EngineError(
                            "This profile has unsupported local extensions; preserve a local copy before recovery"
                        )
                    state = await run_file_operation(engine.reconcile, local_id)
                    authority = None
                    if lost_device:
                        _, owner = self.service._read(user_id)
                        if not owner.authority or owner.phase != "ready":
                            raise EngineError(
                                "Only the verified owner authority can recover a lost device"
                            )
                        if state.pending is not None and state.pending.kind != "takeover":
                            raise EngineError(
                                "Preserve or resolve the existing transfer before lost-device recovery"
                            )
                        if state.checkpoint.holder_device_id != engine.device_id and (
                            state.checkpoint.holder_device_id is None
                            or state.checkpoint.holder_device_id
                            in {device.device_id for device in owner.membership.membership.devices}
                        ):
                            raise EngineError(
                                "Revoke the lost holder in Trusted devices before recovery"
                            )
                        authority, _ = await run_file_operation(self.service._keys, owner, password)
                        await self._authorize(user_id)
                    elif state.pending or state.checkpoint.holder_device_id not in {
                        None,
                        engine.device_id,
                    }:
                        raise EngineError("Profile is held elsewhere or a transfer is pending")
                    if engine.may_launch(local_id):
                        return _public(state)
                    selected = state.checkpoint
                    transfer = SnapshotTransfer(engine, keys)
                    manifest, _, _, _ = await run_file_operation(
                        transfer.selected, local_id, require_checkout=False
                    )
                    if not any(
                        envelope.kind == "device" and envelope.recipient_id == engine.device_id
                        for envelope in manifest.recipients
                    ):
                        raise EngineError(
                            "Ask the source device to publish a new snapshot after pairing"
                        )
                    compatibility = await run_file_operation(self.compatibility)
                    directory = self._job_directory(local_id)
                    revision = await run_file_operation(
                        transfer.download,
                        local_id,
                        directory / "ciphertext",
                        directory / "snapshot.zip",
                        require_checkout=False,
                    )
                    metadata = await run_file_operation(
                        validate_snapshot,
                        directory / "snapshot.zip",
                        directory / "preflight",
                        compatibility,
                    )
                    if metadata.lifecycle == "trashed":
                        observed = await run_file_operation(engine.reconcile, local_id)
                        if revision != selected.revision_id or observed.checkpoint != selected:
                            raise EngineError("Remote deletion changed during preflight; retry")
                        await self._authorize(user_id)
                        self._lifecycle_write(local_id, "trashed", revision)
                        raise EngineError(
                            "Authenticated deletion: choose Apply deletion or Restore"
                        )
                    if revision != selected.revision_id:
                        raise EngineError("Remote snapshot changed during preflight; retry")
                    if lost_device and (
                        selected.holder_device_id != engine.device_id or state.pending is not None
                    ):
                        await self._authorize(user_id)
                        state = await run_file_operation(
                            engine.takeover,
                            local_id,
                            authority,
                            warning_ack=True,
                            expected_checkpoint=selected,
                        )
                    elif selected.holder_device_id is None:
                        await self._authorize(user_id)
                        state = await run_file_operation(
                            engine.claim, local_id, expected_checkpoint=selected
                        )
                    if state.checkpoint.revision_id != revision:
                        raise EngineError("Remote snapshot changed before activation")
                    if profile.deleted_at:
                        await self._authorize(user_id)
                        self._lifecycle_write(
                            local_id, "active", revision, phase="pending", operation="acquire"
                        )
                        await self._set_local_trash(user_id, profile, False)
                        profile = await self.manager.storage.get_profile(local_id)
                    expected_version = profile.row_version
                await self._authorize(user_id)
                compatibility = await run_file_operation(self.compatibility)
                await apply_snapshot(
                    self.manager,
                    local_id,
                    directory / "snapshot.zip",
                    compatibility,
                    self.service.control,
                    revision,
                    expected_version,
                    maintenance=self.maintenance,
                    authorize=lambda: self._authorize(user_id),
                )
                await self._authorize(user_id)
                result = await self._record_activation(engine, user_id, local_id, revision)
                await self._authorize(user_id)
                self._lifecycle_write(local_id, "active", revision)
                return result
        finally:
            try:
                self._cleanup(directory)
            finally:
                client.close()

    @authorized
    async def recover(self, user_id: str, local_id: str, *, password: str | None = None):
        engine, _, client = await self._session(user_id, password)
        directory = None
        try:
            result = await recover_apply(
                self.manager,
                local_id,
                self.service.control,
                maintenance=self.maintenance,
                authorize=lambda: self._authorize(user_id),
            )
            await self._authorize(user_id)
            state = await run_file_operation(engine.reconcile, local_id)
            if (
                result is None
                or result.phase != "complete"
                or state.checkpoint.revision_id != result.revision_id
            ):
                raise EngineError("Recovered snapshot is no longer the selected revision")
            result_state = await self._record_activation(
                engine, user_id, local_id, result.revision_id
            )
            lifecycle = self.service.control.get("lifecycle/" + local_id)
            if lifecycle and lifecycle.payload.get("operation") == "acquire":
                await self._authorize(user_id)
                self._lifecycle_write(local_id, "active", result.revision_id)
            return result_state
        finally:
            try:
                self._cleanup(directory)
            finally:
                client.close()

    async def _record_activation(self, engine, user_id, local_id, revision):
        async with profile_operation(self.manager, local_id, sync_operation=True) as profile:
            await self._authorize(user_id)
            journal = self.service.control.get("apply/" + local_id)
            if (
                journal is None
                or journal.payload.get("phase") != "complete"
                or journal.payload.get("revision_id") != revision
            ):
                raise EngineError("Activation journal is not complete")
            operation = journal.payload.get("operation_id")
            if type(operation) is not str or str(UUID(operation)) != operation:
                raise EngineError("Invalid activation identity")
            expected = self.manager.profiles_dir.resolve() / ("sync-" + operation) / "data"
            if (
                profile_data_path(self.manager, profile) != expected
                or profile.row_version != journal.payload.get("expected_row_version", -2) + 1
            ):
                raise EngineError("Local profile differs from activated snapshot")
            state = await run_file_operation(engine.reconcile, local_id)
            await self._authorize(user_id)
            if state.checkpoint.revision_id != revision:
                raise EngineError("Remote profile changed during activation")
            return _public(engine.record_applied(local_id, revision))

    @authorized
    async def retry(self, user_id: str, local_id: str, *, password: str | None = None):
        lifecycle = self.service.control.get("lifecycle/" + local_id)
        if lifecycle is not None and lifecycle.payload.get("phase") == "pending":
            action = lifecycle.payload.get("operation")
            if action in {"trash", "apply-trash", "restore"}:
                method = getattr(self, action.replace("-", "_"))
                acknowledgement = {
                    "acknowledge_restore" if action == "restore" else "acknowledge_deletion": True
                }
                return await method(
                    user_id,
                    local_id,
                    password=password,
                    authorize=_authorization.get(),
                    **acknowledgement,
                )
        if (
            lifecycle is not None
            and lifecycle.payload.get("phase") == "pending"
            and lifecycle.payload.get("operation") == "acquire"
        ):
            return await self.acquire(
                user_id, local_id, password=password, authorize=_authorization.get()
            )
        initial = self.service.control.get("runtime/initial/" + local_id)
        if initial is not None and initial.payload.get("phase") == "initializing":
            return await self.enable(
                user_id, local_id, password=password, authorize=_authorization.get()
            )
        engine, _, client = await self._session(user_id, password)
        try:
            state = engine.status(local_id)
            if state.pending is None and state.handoff and state.needs_snapshot:
                return await self.handoff(
                    user_id, local_id, password=password, authorize=_authorization.get()
                )
            with self.maintenance.operation():
                async with profile_operation(self.manager, local_id, sync_operation=True):
                    restarted = await run_file_operation(engine.restart_expired_upload, local_id)
                    if not restarted:
                        return await self._retry(engine, user_id, local_id)
            return await self.handoff(
                user_id, local_id, password=password, authorize=_authorization.get()
            )
        finally:
            client.close()

    def _paired(self, user_id):
        from .identity import PairingApproval, PairingRequest, pairing_fingerprint, verify_pairing

        _, owner = self.service._read(user_id)
        found = {}
        for record in self.service.control.list("received/"):
            value = record.payload
            if value.get("phase") != "consumed" or value.get("local_user_id") != user_id:
                continue
            request = PairingRequest.model_validate(value["request"])
            approval = PairingApproval.model_validate(value["approval"])
            if request.device.device_id != owner.device_id:
                continue
            checkpoints = verify_pairing(
                owner.root,
                request,
                approval,
                approval.membership,
                now=request.issued_at,
                confirmed_fingerprint=pairing_fingerprint(request),
            )
            for checkpoint in checkpoints:
                value = {
                    "owner_id": owner.root.owner_id,
                    "membership_epoch": approval.membership.membership.epoch,
                    **checkpoint.model_dump(mode="json"),
                }
                if checkpoint.profile_id in found and found[checkpoint.profile_id] != value:
                    raise EngineError("Conflicting paired checkpoints")
                found[checkpoint.profile_id] = value
        from .profile_invitation import trusted_profile_checkpoints

        for identifier, checkpoint in trusted_profile_checkpoints(self.service, user_id).items():
            if identifier in found and found[identifier] != checkpoint:
                raise EngineError("Conflicting trusted profile checkpoints")
            found[identifier] = checkpoint
        return found

    @authorized
    async def retention_list(self, user_id, local_id, *, password=None):
        from .retention_workflow import listing

        return await listing(self, user_id, local_id, password=password)

    @authorized
    async def retention_schedule(
        self, user_id, local_id, *, revisions, acknowledge_cleanup, password=None
    ):
        from .retention_workflow import schedule

        return await schedule(
            self,
            user_id,
            local_id,
            revisions=revisions,
            acknowledge_cleanup=acknowledge_cleanup,
            password=password,
        )

    @authorized
    async def retention_cancel(self, user_id, local_id, *, decision_id, password=None):
        from .retention_workflow import cancel

        return await cancel(self, user_id, local_id, decision_id=decision_id, password=password)

    @authorized
    async def list_profiles(self, user_id: str):
        await self._authorize(user_id)
        profiles = []
        for record in self.service.control.list("profile/"):
            state = Binding.model_validate(record.payload)
            profile = await self.manager.storage.get_profile(state.local_id, include_deleted=True)
            lifecycle = self.service.control.get("lifecycle/" + state.local_id)
            value = lifecycle.payload if lifecycle else {}
            profiles.append(
                {
                    **_public(state),
                    "name": profile.name if profile else "Unavailable local profile",
                    "local_trashed": bool(profile and profile.deleted_at),
                    "lifecycle": {
                        "state": value.get("state", "unknown"),
                        "revision_id": value.get("revision_id"),
                        "operation": value.get("operation"),
                        "pending": value.get("phase") == "pending",
                    },
                }
            )
        available = [
            {"profile_id": identifier, "checkpoint": checkpoint}
            for identifier, checkpoint in self._paired(user_id).items()
            if self.service.control.get("mapping/" + identifier) is None
        ]
        await self._authorize(user_id)
        return {"profiles": profiles, "available": available}

    @authorized
    async def bind_paired(
        self,
        user_id: str,
        remote_id: str,
        *,
        name: str = "Synchronized profile",
        password: str | None = None,
    ):
        from camoufox_pm.core.models import Profile

        if str(UUID(remote_id)) != remote_id or not UUID(remote_id).int:
            raise EngineError("Invalid remote profile")
        if type(name) is not str or not 1 <= len(name.strip()) <= 200:
            raise EngineError("Profile name must contain 1 to 200 characters")
        engine, _, client = await self._session(user_id, password)
        try:
            with self.maintenance.operation():
                await self._authorize(user_id)
                if remote_id not in self._paired(user_id):
                    raise EngineError("Profile has no independently paired checkpoint")
                key = "runtime/import/" + remote_id
                job = self.service.control.get(key)
                if job is None:
                    profile = Profile(name=name.strip())
                    job = self.service.control.create(
                        key, {"local_id": profile.id, "phase": "preparing"}
                    )
                else:
                    profile = Profile(id=job.payload["local_id"], name=name.strip())
                    if job.payload.get("phase") not in {"preparing", "complete"}:
                        raise EngineError("Invalid profile import state")
                mapping = self.service.control.get("mapping/" + remote_id)
                if mapping is None:
                    engine.bind_paired(profile.id, remote_id)
                elif mapping.payload != {"local_id": profile.id}:
                    raise EngineError("Remote profile already has a different local mapping")
                existing = await self.manager.storage.get_profile(profile.id, include_deleted=True)
                if existing is None:
                    directory = profile_data_path(self.manager, profile)
                    if not directory.exists():
                        create_private_directory(directory)
                    mark_synchronized(self.manager, profile, engine.root.owner_id, remote_id)
                    await self._authorize(user_id)
                    await self.manager.storage.insert_profile_if_absent(profile)
                else:
                    mark_synchronized(self.manager, existing, engine.root.owner_id, remote_id)
                await self._authorize(user_id)
                job = self.service.control.get(key)
                self.service.control.update(
                    key, {"local_id": profile.id, "phase": "complete"}, job.revision
                )
                return _public(engine.status(profile.id))
        finally:
            client.close()

    @authorized
    async def preserve_local_copy(
        self, user_id: str, local_id: str, *, acknowledge_fork: bool, name: str | None = None
    ):
        """Explicitly fork local bytes; never claim that they are the latest remote head.

        The caller must explain that the original browser identity may still be
        active on another device. This action retains the fingerprint and cookies.
        """
        import shutil

        from camoufox_pm.core.models import Profile

        from .snapshots import validate_snapshot

        if acknowledge_fork is not True:
            raise EngineError("Confirm that this copy may duplicate a session active elsewhere")
        if name is not None and (type(name) is not str or not 1 <= len(name.strip()) <= 160):
            raise EngineError("Recovered copy name must contain 1 to 160 characters")
        await self._authorize(user_id, allow_revoked=True)
        directory = target = None
        published = False
        target_owned = False
        try:
            with self.maintenance.operation():
                async with profile_operation(
                    self.manager, local_id, include_deleted=True, sync_operation=True
                ) as source:
                    record = self.service.control.get("profile/" + local_id)
                    if record is None:
                        raise EngineError("Source profile has no synchronization binding")
                    state = Binding.model_validate(record.payload)
                    owner = self.service.control.get("installation")
                    if (
                        state.local_id != local_id
                        or state.checkpoint.owner_id != owner.payload["root"]["owner_id"]
                    ):
                        raise EngineError("Source profile owner does not match")
                    compatibility = await run_file_operation(self.compatibility)
                    directory = self._job_directory(local_id)
                    await run_file_operation(self._capture, source, directory, compatibility)
                    target = self.manager.profiles_dir.resolve() / ("recovered-" + str(uuid4()))
                    metadata = await run_file_operation(
                        validate_snapshot, directory / "snapshot.zip", target, compatibility
                    )
                    target_owned = True
                    values = metadata.profile.apply_fields()
                    values["name"] = "Recovered copy — " + (
                        name.strip() if name else source.name[:160]
                    )
                    profile = Profile(**values, storage_path=str(target / "data"))
                    await self._authorize(user_id, allow_revoked=True)
                    if not await self.manager.storage.insert_profile_if_absent(profile):
                        raise EngineError("A new profile identity could not be reserved")
                    published = True
                    return {
                        "local_id": profile.id,
                        "name": profile.name,
                        "synchronized": False,
                        "source_local_id": local_id,
                    }
        finally:
            self._cleanup(directory)
            if target_owned and target is not None and not published:
                await run_file_operation(shutil.rmtree, target)
