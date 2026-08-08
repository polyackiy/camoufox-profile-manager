"""API dependencies (dependency injection and auth guards)."""

import hmac

from fastapi import Header, HTTPException, Request

from camoufox_pm.config import get_settings
from camoufox_pm.core import auth
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.scheduler import TaskScheduler

_storage_manager: StorageManager | None = None
_profile_manager: ProfileManager | None = None
_scheduler: TaskScheduler | None = None


def set_storage_manager(storage_manager: StorageManager) -> None:
    """Register the shared ``StorageManager`` instance."""
    global _storage_manager
    _storage_manager = storage_manager


def set_profile_manager(profile_manager: ProfileManager) -> None:
    """Register the shared ``ProfileManager`` instance."""
    global _profile_manager
    _profile_manager = profile_manager


def get_storage_manager() -> StorageManager:
    """Return the shared ``StorageManager`` instance."""
    if _storage_manager is None:
        raise HTTPException(status_code=500, detail="StorageManager is not initialized")
    return _storage_manager


def get_profile_manager() -> ProfileManager:
    """Return the shared ``ProfileManager`` instance."""
    if _profile_manager is None:
        raise HTTPException(status_code=500, detail="ProfileManager is not initialized")
    return _profile_manager


def _api_key_matches(x_api_key: str | None) -> bool:
    """Whether the presented key equals the configured one, in constant time."""
    configured = get_settings().api_key
    if not configured or x_api_key is None:
        return False
    # Constant-time comparison to avoid leaking the key via response timing.
    return hmac.compare_digest(x_api_key, configured)


def set_scheduler(scheduler: TaskScheduler) -> None:
    """Register the shared ``TaskScheduler`` instance."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> TaskScheduler:
    """Return the shared ``TaskScheduler`` instance."""
    if _scheduler is None:
        raise HTTPException(status_code=500, detail="TaskScheduler is not initialized")
    return _scheduler


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce the API key when ``CPM_API_KEY`` is set; a no-op otherwise."""
    if not get_settings().api_key:
        return
    if not _api_key_matches(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def require_auth(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """The guard on every API route: a login session or the API key.

    Three configurations, all decided per request so a user created by the CLI
    while the server runs takes effect immediately:

    - no users, no ``CPM_API_KEY`` — open, exactly the pre-auth loopback default;
    - ``CPM_API_KEY`` set — a matching ``X-API-Key`` always passes, so machine
      clients are unaffected by user accounts existing or not;
    - any user account exists — everything else needs a valid session cookie.
    """
    if _api_key_matches(x_api_key):
        return
    storage = get_storage_manager()
    if await storage.count_users() == 0:
        if not get_settings().api_key:
            return
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token and await auth.validate_session(storage, token) is not None:
        return
    raise HTTPException(
        status_code=401, detail="Authentication required: log in or send a valid API key"
    )
