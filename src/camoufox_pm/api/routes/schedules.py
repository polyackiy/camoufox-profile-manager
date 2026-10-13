"""API routes for schedules: recurring launches and browser-version refreshes."""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from camoufox_pm.api.access import (
    current_principal,
    is_admin,
    require_profile_access,
    visible_profile_ids,
)
from camoufox_pm.api.dependencies import get_scheduler, get_storage_manager
from camoufox_pm.api.models.schedules import (
    ScheduleCreateRequest,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleRunListResponse,
    ScheduleRunResponse,
    ScheduleUpdateRequest,
)
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.core.models import Schedule
from camoufox_pm.core.scheduler import next_run_after

router = APIRouter()


async def _authorize_schedule(schedule: Schedule | None, permission: str = "view") -> None:
    if schedule is None:
        raise HTTPException(404, "Schedule not found")
    try:
        await require_profile_access(schedule.profile_id, permission)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "Schedule not found") from None
        raise


async def _actor_fields():
    actor = current_principal()
    user = (
        await get_storage_manager().get_user_by_username(actor.username)
        if actor.kind == "session"
        else None
    )
    return {
        "actor_kind": actor.kind,
        "actor_username": actor.username,
        "actor_user_id": user["id"] if user else None,
    }


# Editing any of these changes when the schedule fires, so next_run_at is
# recomputed; a rename-style edit leaves the planned time alone.
_TIMING_FIELDS = {"kind", "interval_minutes", "at_time", "days", "enabled"}


async def _respond(schedule: Schedule) -> ScheduleResponse:
    """Build the response shape: the schedule plus display context."""
    storage = get_storage_manager()
    profile = await storage.get_profile(schedule.profile_id)
    runs = await storage.list_schedule_runs(schedule.id, limit=1)
    response = ScheduleResponse.from_schedule(
        schedule,
        profile_name=profile.name if profile else None,
        last_run=runs[0] if runs else None,
    )
    if not is_admin() and response.last_run:
        response.last_run.message = None
    return response


@router.get(
    "/schedules",
    response_model=ScheduleListResponse,
    operation_id="list_schedules",
    summary="List schedules.",
    description="List all schedules. They are few, so the list is not paginated.",
)
async def list_schedules():
    """List all schedules with their profile names and last outcomes."""
    try:
        schedules = await get_storage_manager().list_schedules()
        visible = await visible_profile_ids()
        responses = [
            await _respond(schedule)
            for schedule in schedules
            if visible is None or schedule.profile_id in visible
        ]
        return ScheduleListResponse(schedules=responses, total=len(responses))
    except Exception as exc:
        logger.error(f"Failed to list schedules: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/schedules",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_schedule",
    summary="Create a schedule.",
    description=(
        "Create a recurring task against a profile: launch its browser, or move its "
        "pinned fingerprint onto the installed browser version. Daily times are read "
        "on the server's clock. Regenerating hardware is deliberately not schedulable."
    ),
)
async def create_schedule(request: ScheduleCreateRequest):
    """Create a schedule and plan its first run."""
    await require_profile_access(request.profile_id, "edit")
    try:
        storage = get_storage_manager()
        profile = await storage.get_profile(request.profile_id)
        if not profile:
            raise HTTPException(
                status_code=400, detail=f"Profile with ID {request.profile_id} not found"
            )

        schedule = Schedule(**request.model_dump(), **(await _actor_fields()))
        if schedule.enabled:
            schedule.next_run_at = next_run_after(schedule, get_scheduler().clock())
        await storage.save_schedule(schedule)
        logger.info(f"Created schedule {schedule.id} ({schedule.action}) for {profile.name}")
        return await _respond(schedule)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Failed to create schedule: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/schedules/{schedule_id}",
    response_model=ScheduleResponse,
    operation_id="get_schedule",
    summary="Get a schedule.",
    description="Get one schedule with its profile name and last outcome.",
)
async def get_schedule(schedule_id: str):
    """Get a schedule by ID."""
    schedule = await get_storage_manager().get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await _authorize_schedule(schedule)
    return await _respond(schedule)


@router.put(
    "/schedules/{schedule_id}",
    response_model=ScheduleResponse,
    operation_id="update_schedule",
    summary="Update a schedule.",
    description=(
        "Update a schedule. Omitted fields are left alone. Changing the timing or "
        "re-enabling recomputes the next run from now."
    ),
)
async def update_schedule(schedule_id: str, request: ScheduleUpdateRequest):
    """Update a schedule."""
    try:
        storage = get_storage_manager()
        schedule = await storage.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        await _authorize_schedule(schedule, "edit")
        sent = request.model_dump(exclude_unset=True)
        # Rebuild through the model so the cross-field rules hold for the
        # merged result — switching kind without supplying the matching field
        # must fail here, not at fire time.
        now = get_scheduler().clock()
        merged = {**schedule.model_dump(), **sent, "updated_at": now}
        merged.update(await _actor_fields())
        updated = Schedule(**merged)
        if _TIMING_FIELDS & sent.keys():
            updated.next_run_at = next_run_after(updated, now) if updated.enabled else None
        await storage.save_schedule(updated)
        logger.info(f"Updated schedule {schedule_id}")
        return await _respond(updated)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Failed to update schedule {schedule_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete(
    "/schedules/{schedule_id}",
    response_model=ApiResponse[None],
    operation_id="delete_schedule",
    summary="Delete a schedule.",
    description="Delete a schedule and its run history.",
)
async def delete_schedule(schedule_id: str):
    """Delete a schedule."""
    schedule = await get_storage_manager().get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(404, "Schedule not found")
    await _authorize_schedule(schedule, "edit")
    deleted = await get_storage_manager().delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    logger.info(f"Deleted schedule {schedule_id}")
    return ApiResponse(success=True, message=f"Schedule {schedule_id} deleted", data=None)


@router.post(
    "/schedules/{schedule_id}/run",
    response_model=ScheduleRunResponse,
    operation_id="run_schedule",
    summary="Run a schedule now.",
    description=(
        "Execute the schedule's task immediately and record the outcome. The next "
        "planned run is left alone. Works on a disabled schedule."
    ),
)
async def run_schedule(schedule_id: str):
    """Run a schedule immediately."""
    schedule = await get_storage_manager().get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await _authorize_schedule(schedule, "launch" if schedule.action == "launch" else "edit")
    try:
        run = await get_scheduler().run_now(schedule)
        return ScheduleRunResponse.from_run(run).model_copy(
            update={"message": run.message if is_admin() else None}
        )
    except Exception as exc:
        # execute() records failures as run outcomes; reaching here means the
        # recording itself broke.
        logger.error(f"Failed to run schedule {schedule_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/schedules/{schedule_id}/runs",
    response_model=ScheduleRunListResponse,
    operation_id="list_schedule_runs",
    summary="Get a schedule's run history.",
    description="The newest recorded firings, newest first. History is capped at 20 runs.",
)
async def list_schedule_runs(schedule_id: str):
    """Get the run history of a schedule."""
    storage = get_storage_manager()
    schedule = await storage.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await _authorize_schedule(schedule)
    runs = await storage.list_schedule_runs(schedule_id)
    return ScheduleRunListResponse(
        runs=[
            ScheduleRunResponse.from_run(run).model_copy(
                update={"message": run.message if is_admin() else None}
            )
            for run in runs
        ],
        total=len(runs),
    )
