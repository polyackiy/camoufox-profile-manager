"""Closed-profile recovery archives, never a copy/replacement of a live SQLite DB.

Each archive carries a profile record, pinned identity, decrypted proxy credentials
and browser data (without disposable caches). Import re-encrypts credentials with
the current installation key; recovery does not require the old key. Users, login
sessions, schedules and application settings are outside this per-profile scope.
Trash has no automatic expiry. Routine retention is independent of the newest two
before-update checkpoints per profile. Restore always creates a fresh profile.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from . import profile_archive
from .leases import ProfileLocked, make_lease_holder
from .profile_operations import profile_data_path, profile_operation, run_file_operation


class ProfileBackupManager:
    def __init__(self, manager, retention: int = 7, interval_hours: int = 24):
        self.manager = manager
        self.directory = manager.data_dir / "backups"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.retention = max(1, retention)
        self.interval_hours = max(1, interval_hours)

    @asynccontextmanager
    async def _exclusive(self):
        """Serialize archive creation/pruning/restoration across app processes."""
        storage = self.manager.storage
        holder = make_lease_holder()
        if not await storage.acquire_recovery_lock(holder, 300):
            raise ProfileLocked("backups")

        async def renew():
            while True:
                await asyncio.sleep(30)
                if not await storage.acquire_recovery_lock(holder, 300):
                    raise ProfileLocked("backups")

        heartbeat = asyncio.create_task(renew())
        try:
            yield
            if heartbeat.done():
                heartbeat.result()
        finally:
            heartbeat.cancel()
            try:
                # Gather consumes the heartbeat's cancellation while preserving
                # cancellation of this outer operation during its cleanup.
                await asyncio.gather(heartbeat, return_exceptions=True)
            finally:
                await storage.release_recovery_lock(holder)

    def list_backups(self) -> list[dict]:
        result = []
        for metadata in self.directory.glob("*.json"):
            if metadata.is_symlink():
                continue
            try:
                if metadata.stat().st_size > 64 * 1024:
                    continue
                record = json.loads(metadata.read_text(encoding="utf-8"))
                archive = self._archive_path(record["id"])
                if metadata.stem != record["id"] or not archive.is_file() or archive.is_symlink():
                    continue
                datetime.fromisoformat(record["created_at"])
                result.append(
                    {
                        "id": record["id"],
                        "profile_id": record["profile_id"],
                        "profile_name": record["profile_name"],
                        "created_at": record["created_at"],
                        "reason": record["reason"],
                        "size_bytes": archive.stat().st_size,
                    }
                )
            except (ValueError, KeyError, TypeError, OSError):
                logger.warning(f"Unreadable backup metadata: {metadata.name}")
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def _archive_path(self, backup_id: str) -> Path:
        try:
            if uuid.UUID(backup_id).hex != backup_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("Invalid backup ID") from None
        archive = self.directory / f"{backup_id}.zip"
        if archive.is_symlink() or archive.resolve().parent != self.directory.resolve():
            raise ValueError("Invalid backup path")
        return archive

    async def create_backups(
        self,
        profile_ids: list[str] | None = None,
        reason: str = "manual",
        *,
        due_only: bool = False,
    ) -> dict:
        """Return {backups, skipped}; partial batches explicitly name failures.

        before_update includes Trash. No archive is published until its complete
        zip and metadata have been written; failures preserve older checkpoints.
        """
        async with self._exclusive():
            profiles = await self.manager.storage.list_profiles({"include_deleted": True})
            if profile_ids is not None:
                requested = set(profile_ids)
                profiles = [profile for profile in profiles if profile.id in requested]
                missing = requested - {profile.id for profile in profiles}
            else:
                missing = set()
            latest = self.list_backups()
            cutoff = datetime.now() - timedelta(hours=self.interval_hours)
            result = {
                "backups": [],
                "skipped": [
                    {"profile_id": item, "reason": "Profile not found"} for item in sorted(missing)
                ],
            }
            for profile in profiles:
                if due_only and any(
                    item["profile_id"] == profile.id
                    and datetime.fromisoformat(item["created_at"]) > cutoff
                    for item in latest
                ):
                    continue
                try:
                    backup = await self._create_one(profile.id, reason)
                    result["backups"].append(backup)
                except (ProfileLocked, ValueError, OSError) as exc:
                    result["skipped"].append({"profile_id": profile.id, "reason": str(exc)})
            return result

    async def create_due_backups(self) -> dict:
        return await self.create_backups(reason="automatic", due_only=True)

    async def _create_one(self, profile_id: str, reason: str) -> dict:
        backup_id = uuid.uuid4().hex
        destination = self._archive_path(backup_id)
        temporary = destination.with_suffix(".partial")
        metadata = destination.with_suffix(".json")
        temporary_metadata = metadata.with_suffix(".pending")
        try:
            async with profile_operation(self.manager, profile_id, include_deleted=True) as profile:
                if (
                    profile.proxy
                    and profile.proxy.password
                    and profile.proxy.password.startswith("enc:")
                ):
                    raise ValueError(
                        "Stored proxy credentials could not be decrypted. Restore the original "
                        "secret.key or CPM_SECRET_KEY before creating a recoverable backup."
                    )
                data = profile_data_path(self.manager, profile)
                if not data.is_dir():
                    raise ValueError("Profile data directory is missing; backup was not created")
                await run_file_operation(
                    profile_archive.export_profile, profile, data, temporary, strict=True
                )
                temporary.chmod(0o600)
                record = {
                    "id": backup_id,
                    "profile_id": profile.id,
                    "profile_name": profile.name,
                    "created_at": datetime.now().isoformat(),
                    "reason": reason,
                    "size_bytes": temporary.stat().st_size,
                }
                temporary_metadata.write_text(json.dumps(record), encoding="utf-8")
                temporary_metadata.chmod(0o600)
                temporary.replace(destination)
                temporary_metadata.replace(metadata)
            self._prune(profile_id)
            return record
        except BaseException:
            temporary.unlink(missing_ok=True)
            temporary_metadata.unlink(missing_ok=True)
            # A zip without metadata is an unpublished, incomplete operation.
            if not metadata.exists():
                destination.unlink(missing_ok=True)
            raise

    def _prune(self, profile_id: str):
        items = [item for item in self.list_backups() if item["profile_id"] == profile_id]
        routine = [item for item in items if item["reason"] != "before_update"]
        checkpoints = [item for item in items if item["reason"] == "before_update"]
        for item in routine[self.retention :] + checkpoints[2:]:
            try:
                self._archive_path(item["id"]).unlink(missing_ok=True)
                (self.directory / f"{item['id']}.json").unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(f"Could not prune backup {item['id']}: {exc}")

    async def restore_backup(self, backup_id: str, name: str | None = None):
        async with self._exclusive():
            archive = self._archive_path(backup_id)
            if not any(item["id"] == backup_id for item in self.list_backups()):
                raise FileNotFoundError("Backup not found")
            # Copy under the shared recovery lock, then import into fresh storage.
            # import_profile resets id, path, deletion marker, checks and group.
            return await self.manager.import_profile(archive, name=name)
