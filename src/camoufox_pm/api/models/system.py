"""System-level API models."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Envelope for action endpoints that do not return a resource."""

    success: bool = Field(True, description="Whether the operation succeeded")
    message: str = Field("", description="Message")
    data: T | None = Field(None, description="Response payload")


class ErrorDetail(BaseModel):
    """The machine-readable part of every error response."""

    code: str = Field(..., description="Stable error code (snake_case)")
    message: str = Field(..., description="Human-readable description")
    details: list[Any] | None = Field(
        None, description="Structured specifics; set for validation errors"
    )


class ErrorResponse(BaseModel):
    """The one shape every non-2xx response uses."""

    error: ErrorDetail
    detail: str = Field(
        ...,
        description="Mirror of error.message for FastAPI-style clients",
        deprecated=True,
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": "not_found",
                    "message": "Profile with ID x1 not found",
                    "details": None,
                },
                "detail": "Profile with ID x1 not found",
            }
        }
    )


class HealthResponse(BaseModel):
    """Liveness report; unhealthy instances answer with it under a 503."""

    status: str = Field(..., description="healthy or unhealthy")
    api_version: str
    database: str = Field(..., description="connected or disconnected")
    profiles_count: int


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


class SystemInfoData(BaseModel):
    """Payload of the system info endpoint."""

    name: str
    version: str
    description: str
    uptime_seconds: int


class SystemConfigData(BaseModel):
    """Effective configuration; secrets are reported only as set/unset."""

    version: str
    host: str
    port: int
    database_path: str
    api_key_set: bool
    # Whether any user account exists, i.e. whether the API requires a login
    # session (or the API key) rather than being open.
    user_auth_enabled: bool
    encryption_enabled: bool
    cors_origins: list[str]
    camoufox_available: bool
    uptime_seconds: int


class DevicePreset(BaseModel):
    """A fingerprint captured from a real machine, from Camoufox's catalogue."""

    id: str = Field(..., description="Preset id, '<os>:<index>'")
    os: str
    screen: str | None = None
    hardware_concurrency: int | None = None
    gpu: str | None = None
    vendor: str | None = None
    user_agent: str | None = None


class PresetListData(BaseModel):
    """Payload of the preset catalogue endpoint."""

    presets: list[DevicePreset]
    total: int


class ExcelImportData(BaseModel):
    """Payload of the Excel import endpoint."""

    created_count: int
    updated_count: int
    error_count: int
    errors: list[str]


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
