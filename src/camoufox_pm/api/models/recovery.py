"""Public recovery response shapes for OpenAPI clients."""

from datetime import datetime

from pydantic import BaseModel, Field

from .profiles import ProfileResponse


class BackupInfo(BaseModel):
    id: str
    profile_id: str
    profile_name: str
    created_at: datetime
    reason: str
    size_bytes: int = Field(ge=0)


class BackupSkip(BaseModel):
    profile_id: str
    reason: str


class BackupListData(BaseModel):
    backups: list[BackupInfo]


class BackupRunData(BackupListData):
    skipped: list[BackupSkip]


class TrashListData(BaseModel):
    profiles: list[ProfileResponse]
