"""Private reusable workflows, explicit target review and durable run receipts."""

from fastapi import APIRouter, HTTPException, Query

from camoufox_pm.api.access import current_principal, refreshed_principal, request_session_hash
from camoufox_pm.api.dependencies import get_scheduler, get_storage_manager
from camoufox_pm.core.workflow_models import (
    WorkflowDefinition,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    WorkflowStart,
    WorkflowUpdate,
)
from camoufox_pm.core.workflow_store import WorkflowConflict
from camoufox_pm.core.workflows import WorkflowDenied, workflow_owner

router = APIRouter()


def engine():
    return get_scheduler().workflows


async def owner():
    actor = await refreshed_principal(current_principal())
    if actor.role == "viewer":
        raise HTTPException(403, "Workflow access denied.")
    try:
        return await workflow_owner(get_storage_manager(), actor, request_session_hash.get())
    except WorkflowDenied:
        raise HTTPException(403, "Workflow access denied.") from None


async def owned(workflow_id):
    actor = await owner()
    value = engine().store.get(workflow_id)
    if not value or value["owner"] != actor:
        raise HTTPException(404, "Workflow not found.")
    return value


def public(value):
    return {key: item for key, item in value.items() if key != "owner"}


@router.get("/workflows", operation_id="list_workflows", response_model=WorkflowListResponse)
async def list_workflows():
    actor = await owner()
    return {
        "workflows": [public(value) for value in engine().store.list() if value["owner"] == actor]
    }


async def validate_targets(definition, actor):
    for target in definition.scheduled_targets:
        if not await engine().authorized(actor, target.profile_id, request_session_hash.get()):
            raise HTTPException(404, "Profile not found")
        profile = await get_storage_manager().get_profile(target.profile_id)
        if not profile or profile.row_version != target.row_version:
            raise HTTPException(409, "A selected profile changed. Reload and review the targets.")


@router.post(
    "/workflows", status_code=201, operation_id="create_workflow", response_model=WorkflowResponse
)
async def create_workflow(request: WorkflowDefinition):
    actor = await owner()
    if len(engine().store.list()) >= 100:
        raise HTTPException(409, "The installation already has 100 workflows.")
    await validate_targets(request, actor)
    try:
        return public(engine().store.save(request, actor))
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.put(
    "/workflows/{workflow_id}", operation_id="update_workflow", response_model=WorkflowResponse
)
async def update_workflow(workflow_id: str, request: WorkflowUpdate):
    value = await owned(workflow_id)
    definition = WorkflowDefinition(**request.model_dump(exclude={"revision"}))
    await validate_targets(definition, value["owner"])
    try:
        return public(
            engine().store.save(definition, value["owner"], (workflow_id, request.revision))
        )
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/workflows/{workflow_id}", operation_id="delete_workflow")
async def delete_workflow(workflow_id: str, revision: int = Query(ge=1)):
    await owned(workflow_id)
    try:
        engine().store.delete(workflow_id, revision)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {"success": True}


@router.post(
    "/workflows/{workflow_id}/runs",
    status_code=202,
    operation_id="start_workflow",
    response_model=WorkflowRunResponse,
)
async def start_workflow(workflow_id: str, request: WorkflowStart):
    value = await owned(workflow_id)
    try:
        return await engine().submit(value, request, request_session_hash.get())
    except WorkflowDenied:
        raise HTTPException(404, "Profile not found") from None
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None


async def visible_run(workflow_id, run_id):
    value = await owned(workflow_id)
    run = engine().store.run(run_id)
    if not run or run["workflow_id"] != workflow_id:
        raise HTTPException(404, "Workflow run not found.")
    # A revoked profile grant cannot be bypassed via a retained run receipt.
    for target in run["targets"]:
        if not await engine().authorized(
            value["owner"], target["profile_id"], request_session_hash.get()
        ):
            raise HTTPException(404, "Workflow run not found.")
    return run


@router.get(
    "/workflows/{workflow_id}/runs",
    operation_id="list_workflow_runs",
    response_model=WorkflowRunListResponse,
)
async def list_runs(workflow_id: str):
    value = await owned(workflow_id)
    visible = []
    for run in engine().store.runs(workflow_id):
        if all(
            [
                await engine().authorized(
                    value["owner"], target["profile_id"], request_session_hash.get()
                )
                for target in run["targets"]
            ]
        ):
            visible.append(run)
    return {"runs": visible}


@router.get(
    "/workflows/{workflow_id}/runs/{run_id}",
    operation_id="get_workflow_run",
    response_model=WorkflowRunResponse,
)
async def get_run(workflow_id: str, run_id: str):
    return await visible_run(workflow_id, run_id)


@router.post("/workflows/{workflow_id}/runs/{run_id}/cancel", operation_id="cancel_workflow_run")
async def cancel_run(workflow_id: str, run_id: str):
    # Cancellation remains available to the creator after losing a profile
    # grant. It can only stop pending work and drain that run's own sessions.
    await owned(workflow_id)
    run = engine().store.run(run_id)
    if not run or run["workflow_id"] != workflow_id:
        raise HTTPException(404, "Workflow run not found.")
    try:
        engine().cancel(run_id)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {"success": True}
