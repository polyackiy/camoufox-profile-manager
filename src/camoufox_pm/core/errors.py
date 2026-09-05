"""Shared storage-layer exceptions and lease holder ids.

Raised by the backends (SQLite and Postgres) behind the ``StorageManager``
façade, so callers can catch one name regardless of which backend runs.
"""

import os
import socket
import uuid


class ProfileLocked(Exception):
    """Another holder's lease on a profile is still alive.

    ``holder`` is the id currently holding it. This is the two-machine safety
    mechanism speaking: launching the profile now would drive one identity and
    one cookie set from two browsers at once, which is the exact failure the
    lease exists to prevent. An expired lease raises nothing — it simply
    acquires.
    """

    def __init__(self, profile_id: str, holder: str | None = None):
        self.profile_id = profile_id
        self.holder = holder
        super().__init__(
            f"Profile {profile_id} is leased by another holder" + (f" ({holder})" if holder else "")
        )


class StaleWriteError(Exception):
    """A save built on an older row version than the one in the database.

    Raised when a version-checked update matched no rows: someone else saved
    first. The edit is lost loudly rather than silently overwriting theirs.
    """

    def __init__(self, profile_id: str, expected_row_version: int | None = None):
        self.profile_id = profile_id
        self.expected_row_version = expected_row_version
        super().__init__(
            f"Profile {profile_id} was modified elsewhere "
            "(stale row_version); reload it before saving again"
        )


def make_lease_holder() -> str:
    """A lease holder id: ``"<hostname>:<pid>:<uuid4>"``.

    The uuid distinguishes holders that would otherwise collide — two app
    processes on the same host, or a restart that reused the pid. The caller
    mints it once per holder and keeps it, so re-acquisition stays idempotent
    for the holder that owns the lease.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
