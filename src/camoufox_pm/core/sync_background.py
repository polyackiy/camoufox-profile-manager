"""Resume closed-profile transfers using native keys while the manager is running."""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger


async def drain_sync_queue(manager, maintenance, *, runtime_factory=None) -> int:
    """One bounded pass; encrypted-file vaults wait for an explicit user password."""
    directory = Path(manager.storage.db.db_path).absolute().parent / "sync-control"
    if not (directory / "control.sqlite3").exists():
        return 0
    from camoufox_pm.sync.runtime import SyncRuntime

    runtime = (runtime_factory or SyncRuntime)(manager, maintenance)
    installation = runtime.service.control.get("installation")
    if installation is None:
        return 0
    owner = installation.payload
    if owner.get("vault_kind") != "native" or owner.get("phase") not in {
        "ready",
        "freshness_unknown",
    }:
        return 0
    completed = 0
    pending = [
        record
        for record in runtime.service.control.list("runtime/dirty/")
        if record.payload.get("phase") != "complete"
    ]
    # Persist attempt order so completed or repeatedly failing entries cannot
    # monopolize every bounded pass, including after a manager restart.
    pending.sort(key=lambda record: (record.payload.get("last_attempt", 0), record.key))
    attempt = max((record.payload.get("last_attempt", 0) for record in pending), default=0) + 1
    for queued in pending[:8]:
        try:
            runtime.service.control.update(
                queued.key,
                {**queued.payload, "last_attempt": attempt},
                queued.revision,
            )
        except Exception:
            continue
        local_id = queued.key.removeprefix("runtime/dirty/")
        binding = runtime.service.control.get("profile/" + local_id)
        if binding is None or not binding.payload.get("handoff"):
            continue
        if manager.browser_sessions.is_live(local_id):
            continue
        try:
            await runtime.handoff(owner["local_user_id"], local_id)
            completed += 1
        except Exception:
            # No paths, proxy data, password or network error bodies in logs.
            logger.warning("A queued synchronized profile transfer needs retry or unlock")
    return completed


async def sync_queue_worker(manager, maintenance) -> None:
    while True:
        try:
            await drain_sync_queue(manager, maintenance)
        except Exception:
            logger.warning("Synchronization queue remains pending")
        try:
            await asyncio.wait_for(manager.sync_queue_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        manager.sync_queue_event.clear()
