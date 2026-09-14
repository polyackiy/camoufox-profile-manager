"""Profile leases: the fleet-wide answer to "is this profile already running?".

``BrowserSessionManager.active_sessions`` only knows about browsers this
process started. A second instance — the web UI and a CLI launch on one
machine, or two machines against a shared database — sees an empty dict and
happily opens the same profile again. Two browsers on one identity means one
cookie jar written from two places and the same account live from two IPs,
which is exactly the correlation an antidetect profile exists to avoid.

A lease is one row-level claim on ``profiles``: ``locked_by`` says who holds
it and ``lock_expires`` says until when. Taking one is a single conditional
``UPDATE`` (never a read followed by a write), so the database decides the
winner rather than the application.
"""

import os
import socket
import uuid
from datetime import datetime


class ProfileLocked(Exception):
    """Another holder's lease on a profile is still alive.

    ``holder`` is the id currently holding it. An expired lease raises
    nothing — it is simply taken over, which is how a machine that died
    without releasing anything stops blocking the rest of the fleet.
    """

    def __init__(self, profile_id: str, holder: str | None = None):
        self.profile_id = profile_id
        self.holder = holder
        super().__init__(
            f"Profile {profile_id} is leased by another holder" + (f" ({holder})" if holder else "")
        )


def make_lease_holder() -> str:
    """Mint a holder id: ``"<hostname>:<pid>:<uuid4>"``.

    The uuid separates holders the first two fields cannot: two app processes
    on one host, or a restart that reused the pid. Minted once per process and
    kept, so re-acquiring a lease we already hold stays idempotent.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


def lease_expired(lock_expires: str | None) -> bool:
    """Whether a stored lease expiry has already passed.

    ``acquire_lease`` writes these with ``datetime('now', ...)``, which SQLite
    produces in UTC — unlike ``created_at`` and ``last_used`` in this schema,
    which are Python ``isoformat()`` values in local time. The two conventions
    coexist, so anything reading ``lock_expires`` has to treat it as UTC.

    An unparseable value counts as *not* expired: refusing to take over a lease
    we cannot read is the safe direction, since the alternative is launching a
    profile that may be live on another machine.
    """
    if not lock_expires:
        return False
    text = str(lock_expires)
    for candidate in (text.replace(" ", "T", 1) + "+00:00", text):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
        return parsed < now
    return False
