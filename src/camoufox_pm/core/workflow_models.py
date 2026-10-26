"""Bounded, declarative browser workflows. No executable scripts or launch args."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Schedule, ScheduleAction


def validate_name(value: str) -> str:
    if not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("Use a nonempty name without control characters")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowStep(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    action: Literal["launch", "navigate", "wait", "close"]
    url: str | None = Field(None, max_length=2048)
    seconds: int | None = Field(None, ge=1, le=60, strict=True)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return validate_name(value)

    @model_validator(mode="after")
    def parameters(self):
        if self.action == "navigate":
            try:
                parsed = urlsplit(self.url or "")
                valid = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and not any(c.isspace() or ord(c) < 32 for c in self.url or "")
                    and "\\" not in (self.url or "")
                )
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Use an HTTP or HTTPS URL without embedded credentials")
        elif self.url is not None:
            raise ValueError("Only navigation steps accept a URL")
        if (self.action == "wait") != (self.seconds is not None):
            raise ValueError("Only wait steps require seconds")
        return self


class WorkflowTarget(StrictModel):
    profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    row_version: int = Field(ge=0, strict=True)


class WorkflowTiming(StrictModel):
    kind: Literal["interval", "daily"]
    interval_minutes: int | None = Field(None, ge=1, le=40320, strict=True)
    at_time: str | None = None
    days: list[int] | None = Field(None, max_length=7)

    @model_validator(mode="after")
    def expression(self):
        Schedule(profile_id="workflow", action=ScheduleAction.LAUNCH, **self.model_dump())
        return self


class WorkflowDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    steps: list[WorkflowStep] = Field(min_length=2, max_length=12)
    concurrency: int = Field(1, ge=1, le=4, strict=True)
    timing: WorkflowTiming | None = None
    schedule_enabled: bool = Field(default=False, strict=True)
    scheduled_targets: list[WorkflowTarget] = Field(default_factory=list, max_length=100)

    _name = field_validator("name")(validate_name)

    @model_validator(mode="after")
    def structure(self):
        actions = [step.action for step in self.steps]
        if (
            actions[0] != "launch"
            or actions[-1] != "close"
            or any(action in {"launch", "close"} for action in actions[1:-1])
        ):
            raise ValueError(
                "Start with launch, end with close; use navigation or wait between them"
            )
        if self.schedule_enabled and (not self.timing or not self.scheduled_targets):
            raise ValueError("An enabled workflow schedule needs timing and explicit targets")
        ids = [target.profile_id for target in self.scheduled_targets]
        if len(ids) != len(set(ids)):
            raise ValueError("Select each profile only once")
        return self


class WorkflowUpdate(WorkflowDefinition):
    revision: int = Field(ge=1, strict=True)


class WorkflowStart(StrictModel):
    request_id: str
    revision: int = Field(ge=1, strict=True)
    targets: list[WorkflowTarget] = Field(min_length=1, max_length=100)
    retry_of: str | None = None
    replay_confirmed: bool = Field(default=False, strict=True)

    @field_validator("request_id", "retry_of")
    @classmethod
    def identity(cls, value):
        if value is not None and (str(UUID(value)) != value or UUID(value).int == 0):
            raise ValueError("Use a canonical nonzero UUID")
        return value

    @model_validator(mode="after")
    def unique_targets(self):
        if len({target.profile_id for target in self.targets}) != len(self.targets):
            raise ValueError("Select each profile only once")
        if self.retry_of and not self.replay_confirmed:
            raise ValueError("Confirm that retry repeats incomplete profiles from the first step")
        return self


class WorkflowResponse(BaseModel):
    id: str
    revision: int
    definition: WorkflowDefinition
    next_run_at: str | None


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowResponse]


class WorkflowStepReceipt(BaseModel):
    name: str
    action: str
    state: str


class WorkflowTargetReceipt(BaseModel):
    profile_id: str
    row_version: int
    state: str
    message: str | None
    steps: list[WorkflowStepReceipt]


class WorkflowRunResponse(BaseModel):
    """Allowlisted history shape: no internal requests, owners or parameters."""

    id: str
    workflow_id: str
    revision: int
    state: str
    cancel_requested: bool
    started_at: str
    finished_at: str | None
    targets: list[WorkflowTargetReceipt]


class WorkflowRunListResponse(BaseModel):
    runs: list[WorkflowRunResponse]


def planned_after(definition: WorkflowDefinition, now: datetime) -> str | None:
    from .scheduler import next_run_after

    if not definition.schedule_enabled or not definition.timing:
        return None
    schedule = Schedule(
        profile_id="workflow", action=ScheduleAction.LAUNCH, **definition.timing.model_dump()
    )
    return next_run_after(schedule, now).isoformat()
