"""Closed, nonpersistent operation evidence without exception text or private data.

Only fixed codes and a bounded line in an allowlisted loaded module may leave
the process. Never inspect traceback locals, exception arguments/causes, source
paths or arbitrary class names for export. Sync dependencies stay optional.
"""

from __future__ import annotations

import asyncio
import errno
import sqlite3
import sys
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

DiagnosticArea = Literal["sync", "backup"]
DiagnosticStage = Literal[
    "authorization",
    "runtime_setup",
    "service_setup",
    "execute",
    "final_authorization",
    "profile_lookup",
    "backup_profile",
]
DiagnosticCategory = Literal[
    "profile_busy",
    "missing_profile",
    "permission_denied",
    "disk_full",
    "missing_file",
    "resource_busy",
    "filesystem_failed",
    "deadline_exceeded",
    "network_failed",
    "sqlite_programming",
    "sqlite_integrity",
    "sqlite_operational",
    "sqlite_failed",
    "dependency_missing",
    "private_permissions",
    "sync_blocked",
    "vault_failed",
    "relay_conflict",
    "relay_failed",
    "snapshot_invalid",
    "snapshot_activation",
    "profile_not_quiescent",
    "engine_rejected",
    "serialization_failed",
    "invalid_state",
    "internal",
]
DiagnosticSite = Literal[
    "api.sync",
    "core.backups",
    "core.database",
    "core.profile_archive",
    "core.profile_operations",
    "core.sync_admission",
    "core.sync_guard",
    "core.update_permissions",
    "sync.relay",
    "sync.runtime",
    "sync.apply",
    "sync.snapshots",
    "sync.transfer",
    "sync.control",
    "sync.vault",
    "sync.client",
    "sync.engine",
    "sync.quiescence",
    "sync.service",
    "stdlib.zipfile",
    "stdlib.json",
    "stdlib.pathlib",
    "pydantic.validation",
]


class OperationDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(strict=True, ge=1, le=1)
    area: DiagnosticArea
    stage: DiagnosticStage
    category: DiagnosticCategory
    site: DiagnosticSite | None = None
    line: int | None = Field(default=None, strict=True, ge=1, le=10000)

    @model_validator(mode="after")
    def line_requires_site(self) -> OperationDiagnostic:
        if self.line is not None and self.site is None:
            raise ValueError("Diagnostic line requires an allowlisted site")
        return self


_SITES: dict[str, DiagnosticSite] = {
    "camoufox_pm.api.routes.sync": "api.sync",
    **{
        "camoufox_pm." + name: name
        for name in (
            "core.backups",
            "core.database",
            "core.profile_archive",
            "core.profile_operations",
            "core.sync_admission",
            "core.sync_guard",
            "core.update_permissions",
            "sync.relay",
            "sync.runtime",
            "sync.apply",
            "sync.snapshots",
            "sync.transfer",
            "sync.control",
            "sync.vault",
            "sync.client",
            "sync.engine",
            "sync.quiescence",
            "sync.service",
        )
    },
    "zipfile": "stdlib.zipfile",
    "json.encoder": "stdlib.json",
    "pathlib": "stdlib.pathlib",
    "pathlib._local": "stdlib.pathlib",
    "pydantic.main": "pydantic.validation",
}
_CATEGORIES: dict[tuple[str, str], DiagnosticCategory] = {
    ("camoufox_pm.core.leases", "ProfileLocked"): "profile_busy",
    ("camoufox_pm.core.update_permissions", "PermissionProtectionError"): "private_permissions",
    ("camoufox_pm.core.sync_guard", "SyncBlocked"): "sync_blocked",
    ("camoufox_pm.sync.vault", "VaultError"): "vault_failed",
    ("camoufox_pm.sync.client", "RelayConflict"): "relay_conflict",
    ("camoufox_pm.sync.client", "RelayError"): "relay_failed",
    ("camoufox_pm.sync.snapshots", "SnapshotError"): "snapshot_invalid",
    ("camoufox_pm.sync.apply", "ApplyError"): "snapshot_activation",
    ("camoufox_pm.sync.quiescence", "QuiescenceError"): "profile_not_quiescent",
    ("camoufox_pm.sync.engine", "EngineError"): "engine_rejected",
    ("pydantic_core._pydantic_core", "PydanticSerializationError"): "serialization_failed",
}


def _category(error: BaseException) -> DiagnosticCategory:
    kind = type(error)
    known = _CATEGORIES.get((kind.__module__, kind.__name__))
    if known is not None:
        return known
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return "deadline_exceeded"
    if isinstance(error, httpx.HTTPError):
        return "network_failed"
    if isinstance(error, sqlite3.ProgrammingError):
        return "sqlite_programming"
    if isinstance(error, sqlite3.IntegrityError):
        return "sqlite_integrity"
    if isinstance(error, sqlite3.OperationalError):
        return "sqlite_operational"
    if isinstance(error, sqlite3.Error):
        return "sqlite_failed"
    if isinstance(error, OSError):
        number = error.errno
        if type(number) is int:
            if number in {errno.EACCES, errno.EPERM}:
                return "permission_denied"
            if number == errno.ENOSPC:
                return "disk_full"
            if number == errno.ENOENT:
                return "missing_file"
            if number in {errno.EBUSY, errno.EAGAIN, getattr(errno, "ETXTBSY", None)}:
                return "resource_busy"
        return "filesystem_failed"
    if isinstance(error, ImportError):
        return "dependency_missing"
    if isinstance(error, ValueError):
        return "invalid_state"
    return "internal"


_FORWARDERS = {
    ("camoufox_pm.sync.control", "ControlError"),
    ("camoufox_pm.sync.control", "ControlConflict"),
    ("camoufox_pm.core.sync_guard", "SyncBlocked"),
}


def _forwarded_diagnostic(error: BaseException) -> OperationDiagnostic | None:
    kind = type(error)
    identity = (kind.__module__, kind.__name__)
    if identity not in _FORWARDERS:
        return None
    module = sys.modules.get(kind.__module__)
    if module is None or vars(module).get(kind.__name__) is not kind:
        return None
    value = vars(error).get("_cpm_operation_diagnostic")
    if type(value) is not OperationDiagnostic:
        return None
    try:
        # Revalidate even an internal model: model_construct must not bypass the
        # closed contract. Never serialize validation errors or their inputs.
        return OperationDiagnostic.model_validate(dict(vars(value)))
    except (ValueError, TypeError):
        return None


def operation_diagnostic(
    error: BaseException, *, area: DiagnosticArea, stage: DiagnosticStage
) -> OperationDiagnostic:
    forwarded = _forwarded_diagnostic(error)
    if forwarded is not None:
        return OperationDiagnostic(
            version=1,
            area=area,
            stage=stage,
            category=forwarded.category,
            site=forwarded.site,
            line=forwarded.line,
        )
    site = line = None
    trace = error.__traceback__
    for _ in range(64):
        if trace is None:
            break
        name = trace.tb_frame.f_globals.get("__name__")
        if type(name) is str and name in _SITES:
            module = sys.modules.get(name)
            # A user module cannot claim a shipped module name in its globals.
            if module is not None and vars(module) is trace.tb_frame.f_globals:
                site = _SITES[name]
                number = trace.tb_lineno
                line = number if type(number) is int and 1 <= number <= 10000 else None
        trace = trace.tb_next
    return OperationDiagnostic(
        version=1, area=area, stage=stage, category=_category(error), site=site, line=line
    )


def retain_operation_diagnostic(target: BaseException, source: BaseException) -> None:
    """Capture only closed evidence at a trusted sanitizing wrapper boundary.

    No exception cause is traversed or retained here. The outer operation owns
    the eventual area/stage; only loaded allowlisted wrapper classes may forward.
    """
    target.__dict__["_cpm_operation_diagnostic"] = operation_diagnostic(
        source, area="sync", stage="execute"
    )


def validated_diagnostic(value: object, *, area: DiagnosticArea) -> dict | None:
    """CI exports only this closed contract, never arbitrary server metadata."""
    if type(value) is not dict:
        return None
    try:
        result = OperationDiagnostic.model_validate(value)
    except (ValueError, TypeError):
        return None
    if result.area != area:
        return None
    return result.model_dump(mode="json", exclude_none=True)
