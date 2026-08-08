"""Profile storage cleanup and diagnostics.

Finds profile directories on disk that are missing from the database (orphans)
and database profiles that lack a directory, and can reconcile the two.
"""

import argparse
import asyncio
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.models import Profile


class ProfileCleanupManager:
    """Reconcile profile directories on disk with the database."""

    def __init__(self, data_dir: str = "data", db_path: str | None = None):
        """``db_path`` must be the database the application itself uses.

        Deriving it from ``data_dir`` assumes the file is called ``profiles.db``.
        Any other configured name means opening an empty database beside the real
        one — and then every directory on disk has no profile behind it and is
        reported, and removed, as an orphan.
        """
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "profiles"
        self.storage = StorageManager(db_path or str(self.data_dir / "profiles.db"))

    async def initialize(self) -> None:
        await self.storage.initialize()
        logger.info("ProfileCleanupManager initialized")

    def get_profile_directories_on_disk(self) -> list[Path]:
        """Return all profile directories on disk."""
        if not self.profiles_dir.exists():
            return []
        return [
            item
            for item in self.profiles_dir.iterdir()
            if item.is_dir() and item.name.startswith("profile_")
        ]

    async def get_profiles_in_database(self) -> list[Profile]:
        """Return all profiles stored in the database."""
        return await self.storage.list_profiles()

    def extract_profile_id_from_path(self, profile_path: Path) -> str:
        """Extract the profile ID from a directory path (profile_xyz -> xyz)."""
        return profile_path.name.replace("profile_", "")

    async def find_orphaned_profile_directories(self) -> list[dict[str, Any]]:
        """Find directories on disk with no matching database profile."""
        disk_dirs = self.get_profile_directories_on_disk()
        db_profile_ids = {p.id for p in await self.get_profiles_in_database()}

        orphaned: list[dict[str, Any]] = []
        for profile_dir in disk_dirs:
            profile_id = self.extract_profile_id_from_path(profile_dir)
            if profile_id not in db_profile_ids:
                size = self.get_directory_size(profile_dir)
                orphaned.append(
                    {
                        "path": profile_dir,
                        "profile_id": profile_id,
                        "size_mb": size / (1024 * 1024),
                        "modified": profile_dir.stat().st_mtime,
                        "files_count": len(list(profile_dir.rglob("*"))),
                    }
                )
        logger.info(f"Found {len(orphaned)} orphaned directories")
        return orphaned

    async def find_missing_profile_directories(self) -> list[dict[str, Any]]:
        """Find database profiles that have no directory on disk."""
        disk_dirs = self.get_profile_directories_on_disk()
        disk_profile_ids = {self.extract_profile_id_from_path(d) for d in disk_dirs}
        missing = [
            {"profile": profile, "expected_path": self.profiles_dir / f"profile_{profile.id}"}
            for profile in await self.get_profiles_in_database()
            if profile.id not in disk_profile_ids
        ]
        logger.info(f"Found {len(missing)} profiles without directories")
        return missing

    def get_directory_size(self, path: Path) -> int:
        """Return the total size of a directory in bytes."""
        total_size = 0
        try:
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
        except (OSError, PermissionError):
            pass
        return total_size

    async def cleanup_orphaned_directories(self, orphaned: list[dict], confirm: bool = True) -> int:
        """Delete orphaned directories, optionally asking for confirmation."""
        if not orphaned:
            logger.info("No orphaned directories to clean up")
            return 0

        total_size = sum(item["size_mb"] for item in orphaned)
        logger.warning(f"Found {len(orphaned)} orphaned directories ({total_size:.1f} MB)")

        if confirm:
            answer = input(f"Delete {len(orphaned)} orphaned directories? (yes/no): ").lower()
            if answer not in ("yes", "y"):
                logger.info("Cleanup cancelled")
                return 0

        deleted_count = 0
        for item in orphaned:
            try:
                shutil.rmtree(item["path"])
                deleted_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to delete {}: {}".format(item["path"].name, exc))
        logger.info(f"Cleanup complete: removed {deleted_count} directories")
        return deleted_count

    async def create_missing_directories(self, missing: list[dict]) -> int:
        """Create directories for profiles that are missing one."""
        if not missing:
            return 0
        created_count = 0
        for item in missing:
            try:
                item["expected_path"].mkdir(parents=True, exist_ok=True)
                created_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to create {}: {}".format(item["expected_path"].name, exc))
        logger.info(f"Created {created_count} directories")
        return created_count

    async def full_diagnostic(self) -> dict[str, Any]:
        """Run a full diagnostic of profile storage health."""
        disk_dirs = self.get_profile_directories_on_disk()
        db_profiles = await self.get_profiles_in_database()
        orphaned = await self.find_orphaned_profile_directories()
        missing = await self.find_missing_profile_directories()

        total_disk_size = sum(self.get_directory_size(d) for d in disk_dirs) / (1024 * 1024)
        orphaned_size = sum(item["size_mb"] for item in orphaned)

        return {
            "total_profiles_in_db": len(db_profiles),
            "total_directories_on_disk": len(disk_dirs),
            "total_disk_size_mb": total_disk_size,
            "orphaned_directories": len(orphaned),
            "orphaned_size_mb": orphaned_size,
            "missing_directories": len(missing),
            "healthy_profiles": len(db_profiles) - len(missing),
            "issues_found": len(orphaned) + len(missing),
        }

    async def auto_cleanup(self, dry_run: bool = False) -> dict[str, int]:
        """Remove orphaned directories and create missing ones."""
        orphaned = await self.find_orphaned_profile_directories()
        missing = await self.find_missing_profile_directories()
        results = {"orphaned_removed": 0, "directories_created": 0}

        if orphaned and not dry_run:
            results["orphaned_removed"] = await self.cleanup_orphaned_directories(
                orphaned, confirm=False
            )
        if missing and not dry_run:
            results["directories_created"] = await self.create_missing_directories(missing)
        return results

    async def close(self) -> None:
        await self.storage.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Camoufox profile cleanup utility")
    parser.add_argument("--action", choices=["diagnostic", "cleanup", "auto"], default="diagnostic")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without applying them"
    )
    parser.add_argument("--data-dir", default="data", help="Path to the data directory")
    parser.add_argument(
        "--db-path", default=None, help="Path to the database (default: <data-dir>/profiles.db)"
    )
    args = parser.parse_args()

    manager = ProfileCleanupManager(args.data_dir, args.db_path)
    await manager.initialize()
    try:
        if args.action == "diagnostic":
            logger.info(await manager.full_diagnostic())
        elif args.action == "cleanup":
            orphaned = await manager.find_orphaned_profile_directories()
            await manager.cleanup_orphaned_directories(orphaned)
        elif args.action == "auto":
            logger.info(await manager.auto_cleanup(dry_run=args.dry_run))
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
