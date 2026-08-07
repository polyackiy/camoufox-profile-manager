"""API models for profile group endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GroupCreateRequest(BaseModel):
    """Request body for creating a group."""

    name: str = Field(..., min_length=1, max_length=255, description="Group name")
    description: str | None = Field(None, max_length=1000, description="Group description")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "Social media", "description": "Profiles for social media"}
        }
    )


class GroupUpdateRequest(BaseModel):
    """Request body for updating a group."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Social media (updated)",
                "description": "Updated group description",
            }
        }
    )


class GroupResponse(BaseModel):
    """Group data returned by the API."""

    id: str
    name: str
    description: str | None
    profile_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GroupListResponse(BaseModel):
    """List of groups."""

    groups: list[GroupResponse]
    total: int
