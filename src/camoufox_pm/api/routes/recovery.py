"""Explicit recovery actions: retained trash and safe, additive backup restore."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.profiles import ProfileResponse
from camoufox_pm.api.models.recovery import BackupListData, BackupRunData, TrashListData
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.core.backups import ProfileBackupManager

router = APIRouter()
_backup_manager: ProfileBackupManager | None = None


def set_backup_manager(manager: ProfileBackupManager | None) -> None:
    global _backup_manager
    _backup_manager = manager


def get_backup_manager() -> ProfileBackupManager:
    if _backup_manager is None:
        raise HTTPException(503, "Backup service is not initialized")
    return _backup_manager


class CreateBackupsRequest(BaseModel):
    profile_ids: list[str] | None = Field(None, max_length=1000)
    reason: str = Field("manual", min_length=1, max_length=80)


class RestoreBackupRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)


@router.get("/trash/profiles", response_model=ApiResponse[TrashListData], operation_id="list_trash")
async def list_trash():
    profiles = await get_profile_manager().list_trashed_profiles()
    return ApiResponse(
        success=True,
        message="Trash",
        data={"profiles": [ProfileResponse.from_profile(profile) for profile in profiles]},
    )


@router.post(
    "/trash/profiles/{profile_id}/restore",
    response_model=ApiResponse[ProfileResponse],
    operation_id="restore_trashed_profile",
)
async def restore_trash(profile_id: str):
    profile = await get_profile_manager().restore_trashed_profile(profile_id)
    if profile is None:
        raise HTTPException(404, "Trashed profile not found")
    return ApiResponse(
        success=True,
        message="Profile restored; schedules remain paused",
        data=ProfileResponse.from_profile(profile),
    )


@router.delete(
    "/trash/profiles/{profile_id}",
    response_model=ApiResponse[None],
    operation_id="permanently_delete_profile",
)
async def permanently_delete(profile_id: str, confirm: bool = Query(False)):
    if not confirm:
        raise HTTPException(400, "Permanent deletion requires confirm=true")
    try:
        deleted = await get_profile_manager().permanently_delete_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Trashed profile not found")
    return ApiResponse(success=True, message="Profile permanently deleted", data=None)


@router.get(
    "/system/backups", response_model=ApiResponse[BackupListData], operation_id="list_backups"
)
async def list_backups():
    return ApiResponse(
        success=True,
        message="Profile backups",
        data={"backups": get_backup_manager().list_backups()},
    )


@router.post(
    "/system/backups", response_model=ApiResponse[BackupRunData], operation_id="create_backups"
)
async def create_backups(request: CreateBackupsRequest):
    result = await get_backup_manager().create_backups(request.profile_ids, request.reason)
    return ApiResponse(success=True, message="Backup run finished", data=result)


@router.post(
    "/system/backups/{backup_id}/restore",
    response_model=ApiResponse[ProfileResponse],
    operation_id="restore_backup",
)
async def restore_backup(backup_id: str, request: RestoreBackupRequest):
    try:
        profile = await get_backup_manager().restore_backup(backup_id, request.name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ApiResponse(
        success=True,
        message="Backup restored as a new profile",
        data=ProfileResponse.from_profile(profile),
    )
