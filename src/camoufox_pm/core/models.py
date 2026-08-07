"""Data models for the Camoufox profile management system."""

import secrets
import string
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Characters used for short IDs, excluding visually confusing ones (0, o, 1, l, i).
_ID_ALPHABET = "".join(c for c in (string.ascii_lowercase + string.digits) if c not in "0o1li")


def generate_short_id(length: int = 8) -> str:
    """Generate a short, readable ID.

    Every character is random, drawn from an alphabet without visually confusing
    characters (0/o, 1/l/i), which gives 31**8 ≈ 8.5e11 possible IDs at the
    default length.

    This used to derive most of the ID from a microsecond timestamp and leave
    only two random characters. IDs minted in the same microsecond — which is
    what a bulk Excel import does — then had just 961 possible values, and
    because profiles are stored with INSERT OR REPLACE keyed on the ID, a
    collision silently overwrote an existing profile.
    """
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))


def generate_profile_id() -> str:
    """Generate a short ID for a profile."""
    return generate_short_id(8)


def generate_group_id() -> str:
    """Generate a short ID for a group."""
    return generate_short_id(8)


class ProfileStatus(str, Enum):
    """Profile status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"
    MAINTENANCE = "maintenance"


class ProxyType(str, Enum):
    """Proxy protocol."""

    HTTP = "http"
    HTTPS = "https"
    SOCKS4 = "socks4"
    SOCKS5 = "socks5"


class WebRTCMode(str, Enum):
    """WebRTC IP spoofing mode."""

    FORWARD = "forward"  # Forward the real IP
    REPLACE = "replace"  # Replace with the proxy IP
    REAL = "real"  # Use the real IP
    NONE = "none"  # Disable WebRTC


class ProxyConfig(BaseModel):
    """Proxy configuration."""

    type: ProxyType
    server: str
    username: str | None = None
    password: str | None = None
    country: str | None = None

    def to_camoufox_format(self) -> dict[str, str]:
        """Convert to the proxy dict Camoufox/Playwright expects."""
        config = {"server": f"{self.type.value}://{self.server}"}
        if self.username:
            config["username"] = self.username
        if self.password:
            config["password"] = self.password
        return config


class BrowserSettings(BaseModel):
    """Browser fingerprint and behaviour settings.

    High-level fields (os, screen, languages, fonts) are handed to Camoufox,
    which generates a consistent fingerprint. Only explicit overrides that
    Camoufox cannot derive on its own are emitted via :meth:`to_camoufox_config`.
    """

    # Base settings
    os: str = "windows"  # windows, linux, macos
    screen: str = "1920x1080"
    user_agent: str | None = None
    languages: list[str] = ["en-US", "en"]
    timezone: str | None = None
    locale: str | None = None

    # Browser window size
    window_width: int | None = 1280
    window_height: int | None = 720

    # Geolocation
    geolocation: dict[str, float] | None = None

    # WebRTC
    webrtc_mode: WebRTCMode = WebRTCMode.REPLACE
    webrtc_public_ip: str | None = None
    webrtc_local_ips: list[str] | None = None

    # Device characteristics
    hardware_concurrency: int | None = None
    device_memory: int | None = None
    max_touch_points: int = 0

    # Fingerprint noise preferences. Camoufox applies canvas/WebGL/audio spoofing
    # itself; these flags are stored intent and are not forwarded as config keys.
    canvas_noise: bool = True
    webgl_noise: bool = True
    audio_noise: bool = True

    # Fonts
    fonts: list[str] | None = None

    def to_camoufox_config(self) -> dict[str, Any]:
        """Build Camoufox ``config`` property overrides.

        Only values Camoufox does not derive itself are included; the browser's
        own generator owns user-agent, WebGL, canvas and audio to keep the
        fingerprint internally consistent.
        """
        config: dict[str, Any] = {}
        if self.geolocation:
            config["geolocation:latitude"] = self.geolocation["lat"]
            config["geolocation:longitude"] = self.geolocation["lon"]
            if "accuracy" in self.geolocation:
                config["geolocation:accuracy"] = self.geolocation["accuracy"]
        if self.timezone:
            config["timezone"] = self.timezone
        if self.hardware_concurrency:
            config["navigator.hardwareConcurrency"] = self.hardware_concurrency
        if self.max_touch_points:
            config["navigator.maxTouchPoints"] = self.max_touch_points
        # Camoufox only exposes the public ICE address (webrtc:ipv4/ipv6); there is
        # no local-IP config key, so webrtc_local_ips is intentionally not emitted.
        if self.webrtc_public_ip:
            config["webrtc:ipv4"] = self.webrtc_public_ip
        return config


class Profile(BaseModel):
    """Browser profile."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=generate_profile_id)
    name: str
    group: str | None = None
    status: ProfileStatus = ProfileStatus.ACTIVE
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_used: datetime | None = None

    browser_settings: BrowserSettings = Field(default_factory=BrowserSettings)
    proxy: ProxyConfig | None = None
    extensions: list[str] = Field(default_factory=list)
    storage_path: str | None = None
    notes: str | None = None

    def get_storage_path(self, base_path: str = "data/profiles") -> str:
        """Return (and lazily assign) the on-disk path for this profile's data."""
        if not self.storage_path:
            self.storage_path = f"{base_path}/profile_{self.id}"
        return self.storage_path

    def to_camoufox_launch_options(self) -> dict[str, Any]:
        """Build the keyword arguments passed to ``AsyncCamoufox``.

        Passes high-level constraints and lets Camoufox generate the fingerprint;
        does not inject a manual user-agent or WebGL renderer.
        """
        bs = self.browser_settings
        options: dict[str, Any] = {
            "os": bs.os,
            "locale": ",".join(bs.languages) if bs.languages else "en-US",
            "config": bs.to_camoufox_config(),
            "user_data_dir": self.get_storage_path(),
            "persistent_context": True,
            "humanize": True,
            "i_know_what_im_doing": True,
            # Let Camoufox derive geo/timezone from the proxy IP unless we set coordinates.
            "geoip": not bool(bs.geolocation),
        }
        # "none" fully disables WebRTC; other modes use Camoufox's default handling
        # (which reports the proxy's public IP when a proxy is set).
        if bs.webrtc_mode == WebRTCMode.NONE:
            options["block_webrtc"] = True
        if bs.window_width and bs.window_height:
            options["window"] = (bs.window_width, bs.window_height)
        if bs.fonts:
            options["fonts"] = bs.fonts
        if self.proxy:
            options["proxy"] = self.proxy.to_camoufox_format()
        return options


class ProfileGroup(BaseModel):
    """A group of profiles."""

    id: str = Field(default_factory=generate_group_id)
    name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    profile_count: int = 0


class UsageStats(BaseModel):
    """Profile usage record."""

    id: int | None = None
    profile_id: str
    action: str
    timestamp: datetime = Field(default_factory=datetime.now)
    duration: int | None = None  # seconds
    success: bool = True
    details: dict[str, Any] | None = None


class ProxyTestResult(BaseModel):
    """Result of testing a proxy."""

    proxy_id: str
    success: bool
    response_time: int | None = None  # milliseconds
    error_message: str | None = None
    ip_address: str | None = None
    country: str | None = None
    tested_at: datetime = Field(default_factory=datetime.now)


class SystemStatus(BaseModel):
    """System status snapshot."""

    total_profiles: int
    active_profiles: int
    running_browsers: int
    total_groups: int
    system_load: float
    memory_usage: float
    disk_usage: float
    uptime_seconds: int
