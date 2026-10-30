"""First-run browser download and application update preparation."""

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator

from camoufox_pm.api.access import require_admin
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.core.browser_install import BrowserStatus, browser_installer
from camoufox_pm.core.updates import UpdateStatus, check_update

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/system/browser", response_model=ApiResponse[BrowserStatus])
async def browser_status():
    return ApiResponse(data=browser_installer.status())


class BrowserInstallRequest(BaseModel):
    github_token: SecretStr | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("github_token")
    @classmethod
    def valid_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            token = value.get_secret_value()
            if not token.isascii() or any(
                not char.isprintable() or char.isspace() for char in token
            ):
                raise ValueError("Use a GitHub token without spaces or control characters.")
        return value


@router.post("/system/browser/install", response_model=ApiResponse[BrowserStatus])
async def install_browser(request: BrowserInstallRequest | None = Body(default=None)):
    if request is not None and request.github_token is not None:
        status = browser_installer.start(github_token=request.github_token)
    else:
        status = browser_installer.start()
    return ApiResponse(success=True, message="Browser setup started.", data=status)


@router.get("/system/updates", response_model=ApiResponse[UpdateStatus])
async def updates(channel: Literal["stable", "preview"] = "stable"):
    return ApiResponse(
        success=True,
        message="Update check completed.",
        data=await check_update(channel) if channel != "stable" else await check_update(),
    )


class UpdatePreparation(BaseModel):
    release_url: str
    backup_count: int
    message: str


@router.post("/system/updates/prepare", response_model=ApiResponse[UpdatePreparation])
async def prepare_update(channel: Literal["stable", "preview"] = "stable"):
    from camoufox_pm.api.routes.recovery import get_backup_manager

    update = await check_update(channel) if channel != "stable" else await check_update()
    if update.error:
        raise HTTPException(503, update.error)
    if not update.available:
        raise HTTPException(409, "You are already on the latest available release.")
    result = await get_backup_manager().create_backups(reason="before_update")
    if result["skipped"]:
        failures = "; ".join(
            f"{item['profile_id']}: {item['reason']}" for item in result["skipped"]
        )
        raise HTTPException(
            409, f"Backup incomplete. Resolve these problems before updating: {failures}"
        )
    return ApiResponse(
        success=True,
        message="Update prepared.",
        data=UpdatePreparation(
            release_url=update.release_url,
            backup_count=len(result["backups"]),
            message="Profiles backed up. Close the application before installing the update.",
        ),
    )
