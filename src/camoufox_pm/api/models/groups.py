"""API models for profile group endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class GroupCreateRequest(BaseModel):
    """Request body for creating a group."""

    name: str = Field(..., min_length=1, max_length=255, description="Group name")
    description: Optional[str] = Field(None, max_length=1000, description="Group description")

    class Config:
        json_schema_extra = {
            "example": {"name": "Social media", "description": "Profiles for social media"}
        }


class GroupUpdateRequest(BaseModel):
    """Request body for updating a group."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)

    class Config:
        json_schema_extra = {
            "example": {"name": "Social media (updated)", "description": "Updated group description"}
        }


class GroupResponse(BaseModel):
    """Group data returned by the API."""

    id: str
    name: str
    description: Optional[str]
    profile_count: int
    created_at: datetime

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.isoformat()}


class GroupListResponse(BaseModel):
    """List of groups."""

    groups: List[GroupResponse]
    total: int
