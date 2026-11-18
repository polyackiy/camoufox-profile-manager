"""Cookie and profile lease checks for manager-owned action synchronization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from camoufox_pm.api.principal import Principal

    from .browser_session import BrowserSession
    from .database import StorageManager


@dataclass(frozen=True, slots=True)
class ActionSyncAuthority:
    """A server-side login binding and, once prepared, its exact browser cohort."""

    _storage: StorageManager
    _holder: str
    _username: str
    _user_id: str
    _session_hash: str = field(repr=False)
    _sessions: tuple[BrowserSession, ...] = ()
    _profile_ids: tuple[str, ...] = ()

    @classmethod
    async def authenticate(
        cls,
        storage: StorageManager | None,
        holder: str | None,
        principal: Principal | None,
        session_hash: str | None,
    ) -> ActionSyncAuthority | None:
        """Resolve the administrator from the manager's authenticated request evidence."""
        if (
            storage is None
            or not holder
            or principal is None
            or principal.kind != "session"
            or principal.role != "administrator"
            or not principal.username
            or session_hash is None
            or not re.fullmatch(r"[0-9a-f]{64}", session_hash)
        ):
            return None
        user_id = await storage.action_sync_administrator(
            session_hash, principal.username, holder, ()
        )
        if user_id is None:
            return None
        return cls(storage, holder, principal.username, user_id, session_hash)

    async def _valid_for(self, profile_ids: tuple[str, ...]) -> bool:
        user_id = await self._storage.action_sync_administrator(
            self._session_hash, self._username, self._holder, profile_ids
        )
        return user_id == self._user_id

    async def is_authenticated(self) -> bool:
        """Recheck the original administrator cookie during preparation."""
        return await self._valid_for(())

    async def bind_sessions(
        self, sessions: tuple[BrowserSession, ...]
    ) -> ActionSyncAuthority | None:
        """Pin the reviewed sessions after one joint check of their leases and identity."""
        profile_ids = tuple(session.profile_id for session in sessions)
        if (
            self._sessions
            or not 2 <= len(profile_ids) <= 4
            or len(set(profile_ids)) != len(profile_ids)
            or not await self._valid_for(profile_ids)
        ):
            return None
        return replace(self, _sessions=sessions, _profile_ids=profile_ids)

    async def authorize(self, sessions: tuple[BrowserSession, ...]) -> bool:
        """Recheck cookie, current administrator role and every bound lease before use."""
        if (
            not self._sessions
            or len(sessions) != len(self._sessions)
            or any(
                expected is not actual
                for expected, actual in zip(self._sessions, sessions, strict=True)
            )
            or tuple(session.profile_id for session in sessions) != self._profile_ids
        ):
            return False
        return await self._valid_for(self._profile_ids)
