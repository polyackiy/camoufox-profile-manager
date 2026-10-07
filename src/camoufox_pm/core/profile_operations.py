"""Exclusive recovery operations share the same cross-instance leases as launches."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from .leases import ProfileLocked, make_lease_holder


def profile_data_path(manager, profile) -> Path:
    """Refuse stored paths or symlink directories outside managed profile storage."""
    root = manager.profiles_dir.resolve()
    path = Path(profile.storage_path) if profile.storage_path else root / f"profile_{profile.id}"
    resolved = path.resolve()
    # Older records stored cwd-relative paths such as data/profiles/profile_ID.
    # Opening the same DB from a desktop shortcut changes cwd, but its adjacent
    # canonical profile directory remains authoritative. Never relocate files
    # or infer a different profile's directory from an arbitrary stored path.
    expected = root / f"profile_{profile.id}"
    if (
        not path.is_absolute()
        and path.name == expected.name
        and not resolved.is_relative_to(root)
        and expected.is_dir()
        and not expected.is_symlink()
    ):
        path = expected
        resolved = expected.resolve()
    if resolved == root or not resolved.is_relative_to(root) or path.is_symlink():
        raise ValueError("Profile directory must be inside the managed profiles directory")
    return resolved


async def run_file_operation(function, *args, **kwargs):
    """Drain worker I/O before cancellation releases its protective lease."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@asynccontextmanager
async def profile_operation(manager, profile_id: str, include_deleted: bool = False):
    """Reserve one closed profile across processes, with renewal during long I/O."""
    profile = await manager.storage.get_profile(profile_id, include_deleted=include_deleted)
    if profile is None:
        raise ValueError(f"Profile with ID {profile_id} not found")
    if manager.browser_sessions.is_live(profile_id):
        raise ProfileLocked(profile_id, manager.lease_holder)
    holder = make_lease_holder()
    ttl = 300
    if not await manager.storage.acquire_lease(profile_id, holder, ttl, include_deleted):
        lease = await manager.storage.get_lease(profile_id)
        raise ProfileLocked(profile_id, lease[0] if lease else None)

    async def renew():
        while True:
            await asyncio.sleep(30)
            if not await manager.storage.renew_lease([profile_id], holder, ttl):
                raise ProfileLocked(profile_id)

    heartbeat = asyncio.create_task(renew())
    try:
        # Re-read after acquiring: trash/edit operations might have won first.
        profile = await manager.storage.get_profile(profile_id, include_deleted=include_deleted)
        if profile is None:
            raise ValueError(f"Profile with ID {profile_id} not found")
        yield profile
        if heartbeat.done():
            heartbeat.result()
    finally:
        heartbeat.cancel()
        try:
            # Do not swallow cancellation of the caller during this await.
            await asyncio.gather(heartbeat, return_exceptions=True)
        finally:
            await manager.storage.release_lease(profile_id, holder)
