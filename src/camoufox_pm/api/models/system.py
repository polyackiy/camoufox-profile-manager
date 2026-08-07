"""System-level API models."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Generic API response."""

    success: bool = Field(True, description="Whether the operation succeeded")
    message: str = Field("", description="Message")
    data: T | None = Field(None, description="Response payload")


class ErrorResponse(BaseModel):
    """Error response."""

    success: bool = Field(False)
    error: str = Field(..., description="Error code")
    message: str = Field(..., description="Error description")
    details: dict[str, Any] | None = Field(None, description="Additional details")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": False,
                "error": "PROFILE_NOT_FOUND",
                "message": "Profile not found",
                "details": {"profile_id": "invalid_id"},
            }
        }
    )


class PaginationResponse(BaseModel):
    """Pagination metadata."""

    page: int = Field(1, ge=1, description="Page number")
    per_page: int = Field(10, ge=1, le=100, description="Items per page")
    total: int = Field(0, ge=0, description="Total number of items")
    has_next: bool = Field(False, description="Whether a next page exists")
    has_prev: bool = Field(False, description="Whether a previous page exists")


class SystemStatusResponse(BaseModel):
    """System status."""

    total_profiles: int = Field(..., description="Total profiles")
    active_profiles: int = Field(..., description="Active profiles")
    running_browsers: int = Field(..., description="Running browsers")
    total_groups: int = Field(..., description="Total groups")
    system_load: float = Field(..., description="System load")
    memory_usage: float = Field(..., description="Memory usage in %")
    disk_usage: float = Field(..., description="Disk usage in %")
    uptime_seconds: int = Field(..., description="Uptime in seconds")


class ProfileDiagnosticResponse(BaseModel):
    """Result of a profile storage diagnostic."""

    total_profiles_in_db: int = Field(..., description="Total profiles in the database")
    total_directories_on_disk: int = Field(..., description="Total directories on disk")
    total_disk_size_mb: float = Field(..., description="Total size in MB")
    orphaned_directories: int = Field(..., description="Orphaned directories")
    orphaned_size_mb: float = Field(..., description="Size of orphaned directories in MB")
    missing_directories: int = Field(..., description="Profiles without directories")
    healthy_profiles: int = Field(..., description="Healthy profiles")
    issues_found: int = Field(..., description="Issues found")


class ProfileCleanupResponse(BaseModel):
    """Result of a profile storage cleanup."""

    orphaned_removed: int = Field(..., description="Orphaned directories removed")
    directories_created: int = Field(..., description="Directories created")
    freed_space_mb: float = Field(..., description="Freed space in MB")
    dry_run: bool = Field(..., description="Dry run")
    message: str = Field(..., description="Result message")
