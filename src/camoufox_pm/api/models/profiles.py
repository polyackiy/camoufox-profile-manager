"""API models for profile endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from camoufox_pm.core.models import ProfileStatus


class ProfileCreateRequest(BaseModel):
    """Request body for creating a profile."""

    name: str = Field(..., min_length=1, max_length=255, description="Profile name")
    group: Optional[str] = Field(None, description="Profile group ID")
    browser_settings: Optional[Dict[str, Any]] = Field(
        None, description="Browser settings (os, screen, languages, etc.)"
    )
    proxy_config: Optional[Dict[str, Any]] = Field(None, description="Proxy configuration")
    notes: Optional[str] = Field(None, max_length=1000, description="Notes")
    generate_fingerprint: bool = Field(True, description="Generate a fingerprint")

    class Config:
        json_schema_extra = {
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


class ProfileUpdateRequest(BaseModel):
    """Request body for updating a profile."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    group: Optional[str] = Field(None)
    status: Optional[ProfileStatus] = Field(None)
    browser_settings: Optional[Dict[str, Any]] = Field(None)
    proxy_config: Optional[Dict[str, Any]] = Field(None)
    notes: Optional[str] = Field(None, max_length=1000)

    # Individual browser settings
    browser_os: Optional[str] = Field(None, description="Operating system (windows, macos, linux)")
    browser_screen: Optional[str] = Field(None, description="Screen resolution (1920x1080)")
    browser_user_agent: Optional[str] = Field(None, description="User-Agent string")
    browser_languages: Optional[List[str]] = Field(None, description="Browser languages")
    browser_timezone: Optional[str] = Field(None, description="Timezone")
    browser_locale: Optional[str] = Field(None, description="Locale (en_US, ru_RU)")
    browser_webrtc_mode: Optional[str] = Field(
        None, description="WebRTC mode (forward, replace, real, none)"
    )
    browser_canvas_noise: Optional[bool] = Field(None, description="Canvas noise")
    browser_webgl_noise: Optional[bool] = Field(None, description="WebGL noise")
    browser_audio_noise: Optional[bool] = Field(None, description="Audio noise")
    browser_hardware_concurrency: Optional[int] = Field(
        None, ge=1, le=32, description="CPU cores"
    )
    browser_device_memory: Optional[int] = Field(
        None, ge=1, le=128, description="Device memory (GB)"
    )
    browser_max_touch_points: Optional[int] = Field(
        None, ge=0, le=10, description="Max touch points"
    )
    browser_window_width: Optional[int] = Field(
        None, ge=800, le=3840, description="Browser window width"
    )
    browser_window_height: Optional[int] = Field(
        None, ge=600, le=2160, description="Browser window height"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "status": "inactive",
                "notes": "Updated notes",
                "browser_os": "windows",
                "browser_screen": "1920x1080",
                "browser_webrtc_mode": "replace",
                "browser_canvas_noise": True,
                "browser_webgl_noise": True,
            }
        }


class ProfileResponse(BaseModel):
    """Profile data returned by the API."""

    id: str
    name: str
    group: Optional[str]
    status: ProfileStatus
    browser_settings: Dict[str, Any]
    proxy_config: Optional[Dict[str, Any]]
    storage_path: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_used: Optional[datetime]

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.isoformat()}


class ProfileListResponse(BaseModel):
    """Paginated list of profiles."""

    profiles: List[ProfileResponse]
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
    last_session: Optional[datetime]
    success_rate: float
    actions: List[Dict[str, Any]]

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ProfileCloneRequest(BaseModel):
    """Request body for cloning a profile."""

    new_name: str = Field(..., min_length=1, max_length=255)
    regenerate_fingerprint: bool = Field(True, description="Generate a new fingerprint")

    class Config:
        json_schema_extra = {
            "example": {"new_name": "Facebook Profile 1 (copy)", "regenerate_fingerprint": True}
        }


class ProfileLaunchRequest(BaseModel):
    """Request body for launching a browser with a profile."""

    headless: bool = Field(False, description="Launch in headless mode")
    window_size: Optional[str] = Field(None, description="Window size (1920x1080)")
    additional_options: Optional[Dict[str, Any]] = Field(
        None, description="Additional Camoufox options"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "headless": False,
                "window_size": "1920x1080",
                "additional_options": {"geoip": True, "humanize": True},
            }
        }


class ProfileLaunchResponse(BaseModel):
    """Response returned when a browser is launched."""

    profile_id: str
    browser_session_id: str
    status: str
    message: str
    camoufox_options: Dict[str, Any]
