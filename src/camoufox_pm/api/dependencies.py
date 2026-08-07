"""API dependencies (dependency injection and auth guard)."""

import hmac

from fastapi import Header, HTTPException

from camoufox_pm.config import get_settings
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager

_storage_manager: StorageManager | None = None
_profile_manager: ProfileManager | None = None


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


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce the API key when ``CPM_API_KEY`` is set; a no-op otherwise."""
    configured = get_settings().api_key
    if not configured:
        return
    # Constant-time comparison to avoid leaking the key via response timing.
    if x_api_key is None or not hmac.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
