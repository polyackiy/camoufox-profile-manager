"""Browser fingerprint generator.

Produces high-level constraints only (os, screen, region-derived locale,
timezone and geolocation, and hardware characteristics). Camoufox owns the
concrete fingerprint — user-agent, WebGL, canvas and fonts — so those are never
hand-crafted here; doing so would break the fingerprint's internal consistency.
"""

import random
from typing import Any

from .models import BrowserSettings


class FingerprintGenerator:
    """Generate realistic, Camoufox-friendly browser constraints."""

    def __init__(self) -> None:
        # Realistic screen resolutions per operating system.
        self.os_screen_combinations = {
            "windows": ["1920x1080", "1366x768", "1536x864", "1440x900", "1600x900", "2560x1440"],
            "macos": ["1440x900", "1680x1050", "1920x1080", "2560x1600", "2880x1800", "3840x2160"],
            "linux": ["1920x1080", "1366x768", "1280x1024", "1600x900", "1440x900", "2560x1440"],
        }

        # Language sets per region.
        self.language_sets = {
            "us": ["en-US", "en"],
            "uk": ["en-GB", "en"],
            "russia": ["ru-RU", "ru", "en-US"],
            "germany": ["de-DE", "de", "en"],
            "france": ["fr-FR", "fr", "en"],
            "spain": ["es-ES", "es", "en"],
            "italy": ["it-IT", "it", "en"],
            "china": ["zh-CN", "zh", "en"],
            "japan": ["ja-JP", "ja", "en"],
        }

        # Locale per region.
        self.locale_map = {
            "us": "en_US",
            "uk": "en_GB",
            "russia": "ru_RU",
            "germany": "de_DE",
            "france": "fr_FR",
            "spain": "es_ES",
            "italy": "it_IT",
            "china": "zh_CN",
            "japan": "ja_JP",
        }

        self.timezone_map = {
            "us": "America/New_York",
            "uk": "Europe/London",
            "russia": "Europe/Moscow",
            "germany": "Europe/Berlin",
            "france": "Europe/Paris",
            "spain": "Europe/Madrid",
            "italy": "Europe/Rome",
            "china": "Asia/Shanghai",
            "japan": "Asia/Tokyo",
        }

        self.coords_map = {
            "us": {"lat": 40.7128, "lon": -74.0060},
            "uk": {"lat": 51.5074, "lon": -0.1278},
            "russia": {"lat": 55.7558, "lon": 37.6176},
            "germany": {"lat": 52.5200, "lon": 13.4050},
            "france": {"lat": 48.8566, "lon": 2.3522},
            "spain": {"lat": 40.4168, "lon": -3.7038},
            "italy": {"lat": 41.9028, "lon": 12.4964},
            "china": {"lat": 31.2304, "lon": 121.4737},
            "japan": {"lat": 35.6762, "lon": 139.6503},
        }

        # Realistic (cores, memory GB) hardware pairs.
        self.hardware_specs = [
            {"cores": 2, "memory": 4},
            {"cores": 4, "memory": 8},
            {"cores": 6, "memory": 8},
            {"cores": 8, "memory": 16},
            {"cores": 12, "memory": 16},
            {"cores": 16, "memory": 32},
        ]

    async def generate_fingerprint(
        self, constraints: dict[str, Any] | BrowserSettings | None = None
    ) -> BrowserSettings:
        """Generate a set of high-level browser constraints."""
        if isinstance(constraints, BrowserSettings):
            constraints = constraints.model_dump()

        os_choice = (constraints or {}).get("os") or random.choice(["windows", "macos", "linux"])
        region = (constraints or {}).get("region") or random.choice(list(self.language_sets))

        screen = random.choice(self.os_screen_combinations[os_choice])
        languages = self.language_sets.get(region, ["en-US", "en"])
        locale = self.locale_map.get(region, "en_US")
        timezone = self.timezone_map.get(region, "UTC")
        geolocation = self._geolocation_for_region(region)
        hardware = random.choice(self.hardware_specs)
        max_touch_points = 0  # desktop operating systems only

        return BrowserSettings(
            os=os_choice,
            screen=screen,
            languages=languages,
            locale=locale,
            timezone=timezone,
            geolocation=geolocation,
            hardware_concurrency=hardware["cores"],
            device_memory=hardware["memory"],
            max_touch_points=max_touch_points,
        )

    async def reset_fingerprint(self, profile_id: str) -> BrowserSettings:
        """Regenerate a fingerprint from scratch."""
        return await self.generate_fingerprint()

    async def rotate_fingerprint(self, current: BrowserSettings) -> BrowserSettings:
        """Rotate a fingerprint, keeping the same operating system."""
        return await self.generate_fingerprint({"os": current.os})

    def _geolocation_for_region(self, region: str) -> dict[str, float]:
        """Return approximate coordinates for a region with a small random offset."""
        base = self.coords_map.get(region, {"lat": 0.0, "lon": 0.0})
        return {
            "lat": base["lat"] + random.uniform(-0.5, 0.5),
            "lon": base["lon"] + random.uniform(-0.5, 0.5),
        }
