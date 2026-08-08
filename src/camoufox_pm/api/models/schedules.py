"""API models for schedule endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from camoufox_pm.core.models import (
    Schedule,
    ScheduleAction,
    ScheduleKind,
    ScheduleRun,
    ScheduleRunOutcome,
)


class ScheduleCreateRequest(BaseModel):
    """Request body for creating a schedule.

    The cross-field rules (an interval schedule needs ``interval_minutes``, a
    daily one ``at_time``) are enforced here so they fail as request
    validation — the same 422 shape as every other bad field.
    """

    profile_id: str = Field(..., min_length=1)
    action: ScheduleAction
    kind: ScheduleKind
    interval_minutes: int | None = Field(
        None, ge=1, le=40320, description="Every N minutes (interval schedules)"
    )
    at_time: str | None = Field(None, description="HH:MM, 24-hour, on the server's clock")
    days: list[int] | None = Field(
        None, description="Weekdays a daily schedule fires, 0=Monday … 6=Sunday; empty = every day"
    )
    run_minutes: int | None = Field(
        None, ge=1, le=1440, description="Launch only: close the browser after this many minutes"
    )
    enabled: bool = True

    @model_validator(mode="after")
    def _expression_is_complete(self) -> "ScheduleCreateRequest":
        # Validate through the core model so the rules live in one place.
        Schedule(profile_id=self.profile_id or "x", **self.model_dump(exclude={"profile_id"}))
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "profile_id": "a1b2c3d4",
                "action": "launch",
                "kind": "daily",
                "at_time": "09:00",
                "days": [0, 1, 2, 3, 4],
                "run_minutes": 15,
            }
        }
    )


class ScheduleUpdateRequest(BaseModel):
    """Request body for updating a schedule. Omitted fields are left alone."""

    action: ScheduleAction | None = None
    kind: ScheduleKind | None = None
    interval_minutes: int | None = Field(None, ge=1, le=40320)
    at_time: str | None = None
    days: list[int] | None = None
    run_minutes: int | None = Field(None, ge=1, le=1440)
    enabled: bool | None = None

    model_config = ConfigDict(
        json_schema_extra={"example": {"kind": "interval", "interval_minutes": 120}}
    )


class ScheduleRunResponse(BaseModel):
    """One recorded firing of a schedule."""

    id: int | None
    schedule_id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: ScheduleRunOutcome
    message: str | None

    @classmethod
    def from_run(cls, run: ScheduleRun) -> "ScheduleRunResponse":
        return cls(**run.model_dump())


class ScheduleResponse(BaseModel):
    """Schedule data returned by the API."""

    id: str
    profile_id: str
    profile_name: str | None = Field(
        None, description="Resolved for display; null if the profile is gone"
    )
    action: ScheduleAction
    kind: ScheduleKind
    interval_minutes: int | None
    at_time: str | None
    days: list[int] | None
    run_minutes: int | None
    enabled: bool
    next_run_at: datetime | None = Field(
        None, description="Next planned firing, on the server's clock"
    )
    last_run: ScheduleRunResponse | None = Field(None, description="Most recent recorded firing")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_schedule(
        cls,
        schedule: Schedule,
        profile_name: str | None = None,
        last_run: ScheduleRun | None = None,
    ) -> "ScheduleResponse":
        return cls(
            profile_name=profile_name,
            last_run=ScheduleRunResponse.from_run(last_run) if last_run else None,
            **schedule.model_dump(),
        )


class ScheduleListResponse(BaseModel):
    """List of schedules."""

    schedules: list[ScheduleResponse]
    total: int


class ScheduleRunListResponse(BaseModel):
    """Run history of one schedule, newest first."""

    runs: list[ScheduleRunResponse]
    total: int
