"""Named proxy templates. Assignment copies a snapshot into closed profiles."""

from fastapi import APIRouter, HTTPException, Query

from camoufox_pm.api.access import (
    require_admin,
)
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.proxies import (
    ProxyAssignData,
    ProxyAssignRequest,
    ProxyCreateRequest,
    ProxyImportData,
    ProxyImportRequest,
    ProxyListData,
    ProxyResponse,
    ProxyUpdateRequest,
)
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.core.proxy_import import parse_proxy_import
from camoufox_pm.core.proxy_inventory import (
    ProxyCredentialsUnavailable,
    ProxyInventory,
    ProxyNotFound,
)

router = APIRouter()


def inventory() -> ProxyInventory:
    return ProxyInventory(get_profile_manager())


async def _perform(operation):
    try:
        return await operation
    except ProxyNotFound as exc:
        raise HTTPException(404, "Proxy not found") from exc
    except ProxyCredentialsUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        # Model internals may contain credential input. Never reflect it.
        raise HTTPException(422, "Invalid proxy request") from exc


@router.get("/proxies", response_model=ApiResponse[ProxyListData], operation_id="list_proxies")
async def list_proxies():
    await require_admin()
    return ApiResponse(
        success=True, data={"proxies": await inventory().list()}, message="Proxy library"
    )


@router.post(
    "/proxies",
    response_model=ApiResponse[ProxyResponse],
    status_code=201,
    operation_id="create_proxy",
)
async def create_proxy(request: ProxyCreateRequest):
    await require_admin()
    proxy = await _perform(inventory().create(request.model_dump()))
    return ApiResponse(success=True, data=proxy, message="Proxy saved")


@router.post(
    "/proxies/import", response_model=ApiResponse[ProxyImportData], operation_id="import_proxies"
)
async def import_proxies(request: ProxyImportRequest):
    await require_admin()
    try:
        configs = parse_proxy_import(request.text)
    except ValueError as exc:
        # The parser's errors contain safe line numbers, never raw input.
        raise HTTPException(422, str(exc)) from exc
    result = await _perform(inventory().import_configs(configs))
    return ApiResponse(success=True, data=result, message="Proxy import finished")


@router.patch(
    "/proxies/{proxy_id}", response_model=ApiResponse[ProxyResponse], operation_id="update_proxy"
)
async def update_proxy(proxy_id: str, request: ProxyUpdateRequest):
    await require_admin()
    changes = request.model_dump(exclude_unset=True, exclude={"row_version"})
    proxy = await _perform(inventory().update(proxy_id, changes, request.row_version))
    return ApiResponse(
        success=True, data=proxy, message="Proxy updated; assigned profiles keep their snapshot"
    )


@router.delete("/proxies/{proxy_id}", response_model=ApiResponse[None], operation_id="delete_proxy")
async def delete_proxy(proxy_id: str, row_version: int = Query(..., ge=0)):
    await require_admin()
    await _perform(inventory().delete(proxy_id, row_version))
    return ApiResponse(
        success=True, data=None, message="Proxy deleted; assigned profiles keep their snapshot"
    )


@router.post(
    "/proxies/{proxy_id}/check",
    response_model=ApiResponse[ProxyResponse],
    operation_id="check_inventory_proxy",
)
async def check_proxy(proxy_id: str):
    await require_admin()
    return ApiResponse(
        success=True, data=await _perform(inventory().check(proxy_id)), message="Proxy checked"
    )


@router.post(
    "/proxies/{proxy_id}/assign",
    response_model=ApiResponse[ProxyAssignData],
    operation_id="assign_inventory_proxy",
)
async def assign_proxy(proxy_id: str, request: ProxyAssignRequest):
    await require_admin()
    result = await _perform(
        inventory().assign(
            proxy_id,
            [profile.model_dump() for profile in request.profiles],
            request.proxy_row_version,
        )
    )
    return ApiResponse(
        success=True, data=result, message="Assignment finished; profile proxy settings are copies"
    )
