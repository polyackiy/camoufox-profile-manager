"""API models for profile endpoints."""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from camoufox_pm.core.models import Profile, ProfileStatus
from camoufox_pm.core.proxy_check import Level, ProxyCheckResult


class ProfileCreateRequest(BaseModel):
    """Request body for creating a profile."""

    name: str = Field(..., min_length=1, max_length=255, description="Profile name")
    group: str | None = Field(None, description="Profile group ID")
    browser_settings: dict[str, Any] | None = Field(
        None, description="Browser settings (os, screen, languages, etc.)"
    )
    proxy_config: dict[str, Any] | None = Field(None, description="Proxy configuration")
    notes: str | None = Field(None, max_length=1000, description="Notes")
    generate_fingerprint: bool = Field(True, description="Generate a fingerprint")
    fingerprint_preset: str | None = Field(
        None,
        description=(
            "Pin the profile to a captured real device, using an id from "
            "GET /api/v1/fingerprints/presets. Omit to generate a fingerprint instead."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Facebook Profile 1",
                "group": "social_media_group_id",
                "browser_settings": {
                    "os": "windows",
                    "screen": "1920x1080",
                    "languages": ["en-US", "en"],
                },
                "proxy_config": {
                    "type": "http",
                    "server": "proxy.example.com:8080",
                    "username": "user",
                    "password": "pass",
                },
                "notes": "Profile for Facebook",
                "generate_fingerprint": True,
            }
        }
    )


class ProfileUpdateRequest(BaseModel):
    """Request body for updating a profile."""

    name: str | None = Field(None, min_length=1, max_length=255)
    group: str | None = Field(None)
    status: ProfileStatus | None = Field(None)
    browser_settings: dict[str, Any] | None = Field(None)
    proxy_config: dict[str, Any] | None = Field(None)
    notes: str | None = Field(None, max_length=1000)

    # The legacy flattened form of browser_settings. Kept for 0.1.x clients;
    # removed in 1.0. Send the same keys inside browser_settings instead.
    browser_os: str | None = Field(
        None, description="Operating system (windows, macos, linux)", deprecated=True
    )
    browser_screen: str | None = Field(
        None, description="Screen resolution (1920x1080)", deprecated=True
    )
    browser_user_agent: str | None = Field(None, description="User-Agent string", deprecated=True)
    browser_languages: list[str] | None = Field(
        None, description="Browser languages", deprecated=True
    )
    browser_timezone: str | None = Field(None, description="Timezone", deprecated=True)
    browser_locale: str | None = Field(None, description="Locale (en_US, ru_RU)", deprecated=True)
    browser_webrtc_mode: str | None = Field(
        None, description="WebRTC mode (forward, replace, real, none)", deprecated=True
    )
    browser_canvas_noise: bool | None = Field(None, description="Canvas noise", deprecated=True)
    browser_stable_canvas: bool | None = Field(
        None,
        description=(
            "Keep the canvas reproducible across launches. Costs cross-site "
            "unlinkability: the canvas becomes identical on every site."
        ),
        deprecated=True,
    )
    browser_webgl_noise: bool | None = Field(None, description="WebGL noise", deprecated=True)
    browser_audio_noise: bool | None = Field(None, description="Audio noise", deprecated=True)
    browser_hardware_concurrency: int | None = Field(
        None, ge=1, le=32, description="CPU cores", deprecated=True
    )
    browser_device_memory: int | None = Field(
        None, ge=1, le=128, description="Device memory (GB)", deprecated=True
    )
    browser_max_touch_points: int | None = Field(
        None, ge=0, le=10, description="Max touch points", deprecated=True
    )
    browser_window_width: int | None = Field(
        None, ge=800, le=3840, description="Browser window width", deprecated=True
    )
    browser_window_height: int | None = Field(
        None, ge=600, le=2160, description="Browser window height", deprecated=True
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "inactive",
                "notes": "Updated notes",
                "browser_settings": {
                    "os": "windows",
                    "screen": "1920x1080",
                    "webrtc_mode": "replace",
                    "canvas_noise": True,
                    "webgl_noise": True,
                },
            }
        }
    )


class ProfileResponse(BaseModel):
    """Profile data returned by the API."""

    id: str
    name: str
    group: str | None
    status: ProfileStatus
    browser_settings: dict[str, Any]
    proxy_config: dict[str, Any] | None
    storage_path: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    last_used: datetime | None
    # A short description of the pinned machine, not the stored config itself:
    # that is ~40 properties and tens of kilobytes, which nothing here needs.
    fingerprint: dict[str, Any] | None = Field(
        None,
        description=(
            "Summary of the pinned machine, null until the first launch. Its values "
            "come from Camoufox and may gain keys as Camoufox changes; not frozen "
            "by the API contract."
        ),
    )

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_profile(cls, profile: "Profile") -> "ProfileResponse":
        """Build the API view of a profile."""
        from camoufox_pm.core import fingerprint_store

        return cls(
            id=profile.id,
            name=profile.name,
            group=profile.group,
            status=profile.status,
            browser_settings=(
                profile.browser_settings.model_dump() if profile.browser_settings else {}
            ),
            proxy_config=profile.proxy.model_dump() if profile.proxy else None,
            storage_path=profile.storage_path,
            notes=profile.notes,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            last_used=profile.last_used,
            fingerprint=fingerprint_store.summarize(profile.fingerprint),
        )


class ProfileListResponse(BaseModel):
    """Paginated list of profiles."""

    profiles: list[ProfileResponse]
    total: int
    page: int
    per_page: int
    has_next: bool
    has_prev: bool


class ProfileStatsResponse(BaseModel):
    """Usage statistics for a profile."""

    profile_id: str
    total_sessions: int
    total_duration_minutes: int
    last_session: datetime | None
    success_rate: float
    actions: list[dict[str, Any]] = Field(
        ..., description="Recent usage log entries; shape not covered by the API contract"
    )


class ProfileCloneRequest(BaseModel):
    """Request body for cloning a profile."""

    new_name: str = Field(..., min_length=1, max_length=255)
    regenerate_fingerprint: bool = Field(True, description="Generate a new fingerprint")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"new_name": "Facebook Profile 1 (copy)", "regenerate_fingerprint": True}
        }
    )


class ProfileLaunchRequest(BaseModel):
    """Request body for launching a browser with a profile."""

    headless: bool = Field(False, description="Launch in headless mode")
    window_size: str | None = Field(None, description="Window size (1920x1080)")
    additional_options: dict[str, Any] | None = Field(
        None, description="Additional Camoufox options"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "headless": False,
                "window_size": "1920x1080",
                "additional_options": {"geoip": True, "humanize": True},
            }
        }
    )


class ProfileLaunchResponse(BaseModel):
    """Response returned when a browser is launched."""

    profile_id: str
    browser_session_id: str = Field(
        ...,
        description=(
            "A random value that never identified anything; no endpoint accepts it. "
            "Use profile_id to address the running browser."
        ),
        deprecated=True,
    )
    status: str = Field(..., description="launched or already_running")
    message: str
    process_id: int | None = Field(None, description="OS process id of the browser, if known")
    camoufox_options: dict[str, Any] = Field(
        ...,
        description="Launch internals passed to Camoufox; shape not covered by the API contract",
    )


class BrowserCloseResponse(BaseModel):
    """Response returned when a browser is asked to close."""

    status: str = Field(..., description="closed or not_running")
    profile_id: str
    message: str


class ActiveBrowserInfo(BaseModel):
    """One running browser."""

    profile_id: str
    process_id: int | None
    started_at: datetime


class ActiveBrowsersResponse(BaseModel):
    """The browsers currently running."""

    active_browsers: list[ActiveBrowserInfo]
    count: int


class BrowsersCloseAllResponse(BaseModel):
    """Response returned when every browser is asked to close."""

    status: str
    closed_count: int
    errors: list[str]
    message: str


class ProxyCheckRequest(BaseModel):
    """Request body for checking a proxy that is not saved yet."""

    proxy_config: dict[str, Any] | None = Field(
        None, description="Proxy to check. Omit to check the direct connection."
    )
    browser_settings: dict[str, Any] | None = Field(
        None, description="Settings to compare against the exit address"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "proxy_config": {"type": "http", "server": "proxy.example.com:8080"},
                "browser_settings": {"timezone": "Europe/Berlin"},
            }
        }
    )


class ProxyLocationResponse(BaseModel):
    """Where a proxy actually comes out."""

    ip: str
    country: str | None = None
    timezone: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ProxyFindingResponse(BaseModel):
    """One thing worth telling the user about this proxy and this profile."""

    level: Level = Field(..., description="error, warning or info")
    field: str = Field(..., description="The setting the finding is about")
    message: str


class ProxyCheckResponse(BaseModel):
    """The result of reaching the internet through a proxy and comparing it."""

    reachable: bool
    error: str | None = None
    latency_ms: int | None = None
    location: ProxyLocationResponse | None = None
    findings: list[ProxyFindingResponse] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: ProxyCheckResult) -> "ProxyCheckResponse":
        return cls(
            reachable=result.reachable,
            error=result.error,
            latency_ms=result.latency_ms,
            location=(
                ProxyLocationResponse(**asdict(result.location)) if result.location else None
            ),
            findings=[ProxyFindingResponse(**asdict(f)) for f in result.findings],
        )
