"""Persist closed-browser upload intent before relinquishing the local lease."""

from __future__ import annotations

import time

from .sync_guard import _inspect, _validate


async def queue_closed_profile(manager, profile_id: str) -> bool:
    profile = await manager.storage.get_profile(profile_id)
    if profile is None:
        return False
    binding = _inspect(manager, profile)
    if binding is None:
        return False
    service, user_id = binding
    from camoufox_pm.sync.control import ControlMutation

    record = service.control.get("profile/" + profile_id)
    if record is None:
        raise ValueError("Synchronized profile binding disappeared")
    value = record.payload
    key = "runtime/dirty/" + profile_id
    previous = service.control.get(key)
    if value.get("phase") == "handoff" and value.get("handoff") is True and previous:
        return True
    # No relay request, private key or password is needed to block another launch.
    # Demotion does not cancel the obligation to retain a closed profile's work.
    _validate(service, user_id, manager, profile)
    value.update(phase="handoff", handoff=True, needs_snapshot=True)
    service.control.batch(
        (
            ControlMutation(record.key, value, record.revision),
            ControlMutation(
                key,
                {"reason": "browser_closed", "created_at": int(time.time())},
                previous.revision if previous else None,
            ),
        )
    )
    return True
