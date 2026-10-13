"""Administrator-only account, grant and secret-free activity APIs."""

import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from camoufox_pm.api.access import current_principal, require_admin
from camoufox_pm.api.dependencies import get_storage_manager
from camoufox_pm.api.models.access import CreateUserRequest, GrantRequest, UpdateUserRequest
from camoufox_pm.core.auth import hash_password

router = APIRouter(dependencies=[Depends(require_admin)])


async def _user(username):
    user = await get_storage_manager().get_user_by_username(username)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.get("/access/users")
async def users():
    rows = await get_storage_manager().list_users()
    return {"users": rows, "total": len(rows)}


@router.post("/access/users", status_code=201)
async def create_user(body: CreateUserRequest):
    if not await get_storage_manager().count_users() and body.role != "administrator":
        raise HTTPException(409, "The first account must be an administrator")
    try:
        await get_storage_manager().create_user(
            str(uuid.uuid4()),
            body.username,
            hash_password(body.password),
            body.role,
            current_principal(),
        )
    except ValueError:
        raise HTTPException(409, "Username already exists") from None
    return {"username": body.username, "role": body.role}


@router.patch("/access/users/{username}")
async def update_user(username: str, body: UpdateUserRequest):
    await _user(username)
    try:
        await get_storage_manager().update_user_access(
            username,
            role=body.role,
            password_hash=hash_password(body.password) if body.password else None,
            actor=current_principal(),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    user = await _user(username)
    return {"username": user["username"], "role": user["role"]}


@router.delete("/access/users/{username}")
async def delete_user(username: str):
    await _user(username)
    try:
        await get_storage_manager().delete_user(username, actor=current_principal())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"success": True}


@router.get("/access/users/{username}/profiles")
async def grants(username: str):
    await _user(username)
    rows = await get_storage_manager().list_profile_grants(username)
    return {"grants": rows, "total": len(rows)}


@router.get("/access/users/{username}/profiles/{profile_id}")
async def grant(username: str, profile_id: str):
    rows = (await grants(username))["grants"]
    for row in rows:
        if row["profile_id"] == profile_id:
            return row
    raise HTTPException(404, "Grant not found")


@router.put("/access/users/{username}/profiles/{profile_id}")
async def set_grant(username: str, profile_id: str, body: GrantRequest):
    await _user(username)
    try:
        await get_storage_manager().set_profile_grant(
            username, profile_id, body.can_export, actor=current_principal()
        )
    except sqlite3.IntegrityError:
        raise HTTPException(404, "Profile not found") from None
    return {"profile_id": profile_id, "can_export": body.can_export}


@router.delete("/access/users/{username}/profiles/{profile_id}")
async def delete_grant(username: str, profile_id: str):
    await _user(username)
    await get_storage_manager().delete_profile_grant(
        username, profile_id, actor=current_principal()
    )
    return {"success": True}


@router.get("/access/audit")
async def audit(
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(100, ge=1, le=1000),
    actor: str | None = None,
    action: str | None = None,
    result: str | None = None,
):
    rows = await get_storage_manager().list_audit(
        offset=offset, limit=limit, actor=actor, action=action, result=result
    )
    return {
        "events": rows,
        "total": await get_storage_manager().count_audit(actor=actor, action=action, result=result),
    }


@router.get("/access/audit/export")
async def export_audit(
    actor: str | None = None, action: str | None = None, result: str | None = None
):
    rows = []
    for offset in range(0, 10000, 1000):
        rows.extend(
            await get_storage_manager().list_audit(
                offset=offset, limit=1000, actor=actor, action=action, result=result
            )
        )
    return {"events": rows, "total": len(rows)}
