"""Data models for the Camoufox profile management system."""

import re
import secrets
import string
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def generate_schedule_id() -> str:
    """Generate a short ID for a schedule."""
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

    # Make the canvas reproducible across launches instead of randomised per
    # session. Off by default: it trades cross-site unlinkability for a canvas
    # that behaves like real hardware. See to_camoufox_launch_options().
    stable_canvas: bool = False

    # Fonts
    fonts: list[str] | None = None

    def has_geography(self) -> bool:
        """Whether this profile states where it is instead of following its proxy."""
        return bool(self.timezone or self.geolocation)

    def clear_geography(self) -> bool:
        """Drop the stated location so the proxy's exit address supplies it again.

        Only the two fields Camoufox derives from that address. Languages and
        locale are left alone: they are not geography — Camoufox applies them
        after its IP lookup regardless, and an English browser is unremarkable
        from any country — so clearing them would change the profile's identity
        rather than free it to follow the proxy.
        """
        if not self.has_geography():
            return False
        self.timezone = None
        self.geolocation = None
        return True

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
        # webrtc_local_ips is intentionally not emitted. The webrtc:localipv4 key
        # does exist, but setting it replaces the mDNS candidate a real Firefox
        # sends with a literal address. Leaving it alone keeps the real behaviour.
        if self.webrtc_public_ip:
            config["webrtc:ipv4"] = self.webrtc_public_ip
        return config


# What a finding means, defined here because both the live check and the record
# kept on a profile carry one — two definitions would be two types for the same
# value in the same API response.
Level = Literal["error", "warning", "info"]


class ProxyCheckFinding(BaseModel):
    """One thing worth telling the user about this proxy and this profile."""

    level: Level
    field: str
    message: str


class ProxyCheckRecord(BaseModel):
    """The last answer a profile's proxy gave, kept so the list can show it.

    Deliberately smaller than the live check: no coordinates, because the list
    shows where the proxy comes out, not where on the map. Whether the dot is
    green, amber or red is derived from `reachable` and the findings rather than
    stored, so an old row cannot disagree with the rules that read it.
    """

    checked_at: datetime
    reachable: bool
    error: str | None = None
    latency_ms: int | None = None
    ip: str | None = None
    country: str | None = None
    timezone: str | None = None
    findings: list[ProxyCheckFinding] = Field(default_factory=list)


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

    # The machine this profile pretends to be, resolved once on first launch and
    # replayed from then on so the profile does not look like new hardware every
    # session. See core/fingerprint_store.py for what is frozen and what is not.
    fingerprint: dict[str, Any] | None = None

    # The last time this profile's proxy was checked, so the list can show it
    # without checking again. Cleared when the proxy changes: an answer from the
    # old proxy says nothing about the new one.
    proxy_check: ProxyCheckRecord | None = None

    # Optimistic-concurrency counter, bumped by every version-checked save.
    # Storage-only: a caller reads a profile, edits it, and writes back against
    # the version it read; a concurrent save in between makes the write fail
    # (StaleWriteError) instead of silently clobbering it. Not part of the API
    # model on purpose — it is storage bookkeeping, not profile data.
    row_version: int = 0

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
        if bs.stable_canvas:
            # Firefox's baseline fingerprinting protection randomises canvas image
            # export (toDataURL/toBlob, 2D and WebGL) per site and per session.
            # Turning it off makes the canvas reproducible, which is what a
            # long-lived profile needs — together with the pinned fonts:spacing_seed,
            # since text rendering follows that seed rather than the canvas path.
            #
            # The cost is deliberate: the canvas is then identical across sites,
            # exactly as real hardware behaves, so it can be correlated between
            # them. That is why this is per profile and off by default.
            options["firefox_user_prefs"] = {
                "privacy.baselineFingerprintingProtection": False,
            }
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


class ScheduleAction(str, Enum):
    """What a schedule does to its profile when it fires.

    Deliberately absent: regenerating the hardware fingerprint. The pinned
    machine exists so a profile stays the same computer between sessions;
    swapping its GPU, screen and cores on a timer would hand a warmed-up
    account new hardware overnight — the one thing the pin prevents. Hardware
    regeneration stays a manual action (reset-fingerprint), which warns about
    the cost. REFRESH_BROWSER is the honest scheduled rotation: it moves only
    the browser version, which is what a real machine does when it updates.
    """

    LAUNCH = "launch"
    REFRESH_BROWSER = "refresh_browser"


class ScheduleKind(str, Enum):
    """How a schedule's fire times are expressed."""

    INTERVAL = "interval"  # every N minutes
    DAILY = "daily"  # at HH:MM on the chosen weekdays


class ScheduleRunOutcome(str, Enum):
    """How one firing of a schedule ended."""

    OK = "ok"
    SKIPPED = "skipped"  # e.g. the browser was already running
    ERROR = "error"
    MISSED = "missed"  # fell due while the process was down; not replayed


_TIME_OF_DAY = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Schedule(BaseModel):
    """A recurring task against one profile.

    ``at_time`` is read on the server's own clock: this is a single-process
    tool that runs next to its browsers, so "09:00" means 09:00 where the
    process runs, with no timezone bookkeeping to get wrong. ``days`` uses
    Python's weekday numbering, 0 = Monday … 6 = Sunday; empty means every day.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=generate_schedule_id)
    profile_id: str
    action: ScheduleAction
    kind: ScheduleKind
    interval_minutes: int | None = Field(None, ge=1)
    at_time: str | None = None
    days: list[int] | None = None
    # Launch schedules only: close the browser this long after opening it, so a
    # warming session ends by itself instead of staying open until the next run
    # finds it and skips.
    run_minutes: int | None = Field(None, ge=1)
    enabled: bool = True
    next_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _expression_is_complete(self) -> "Schedule":
        if self.kind == ScheduleKind.INTERVAL:
            if not self.interval_minutes:
                raise ValueError("An interval schedule needs interval_minutes")
        else:
            if not self.at_time or not _TIME_OF_DAY.match(self.at_time):
                raise ValueError("A daily schedule needs at_time as HH:MM (24-hour)")
        if self.days is not None:
            if any(day < 0 or day > 6 for day in self.days):
                raise ValueError("days entries are weekdays, 0 (Monday) to 6 (Sunday)")
            self.days = sorted(set(self.days)) or None
        if self.action == ScheduleAction.REFRESH_BROWSER and self.run_minutes:
            raise ValueError("run_minutes only applies to launch schedules")
        return self


class ScheduleRun(BaseModel):
    """The record of one firing of a schedule."""

    id: int | None = None
    schedule_id: str
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
    outcome: ScheduleRunOutcome
    message: str | None = None

    model_config = ConfigDict(use_enum_values=True)
