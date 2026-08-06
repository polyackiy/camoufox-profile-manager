"""Tests for the fingerprint generator.

The generator sets only high-level constraints (os, screen, region-derived
locale/timezone/geolocation). Camoufox owns the user-agent and WebGL, so those
are never hand-crafted here.
"""

import pytest

from camoufox_pm.core.fingerprint_generator import FingerprintGenerator


@pytest.mark.asyncio
async def test_delegates_user_agent_to_camoufox():
    gen = FingerprintGenerator()
    bs = await gen.generate_fingerprint({"os": "windows", "region": "us"})
    assert bs.os == "windows"
    assert bs.languages[0].startswith("en")
    assert bs.timezone == "America/New_York"
    # UA is owned by Camoufox now — must not be hand-crafted.
    assert bs.user_agent is None


@pytest.mark.asyncio
async def test_screen_matches_os_pool():
    gen = FingerprintGenerator()
    bs = await gen.generate_fingerprint({"os": "macos", "region": "uk"})
    assert bs.screen in gen.os_screen_combinations["macos"]


@pytest.mark.asyncio
async def test_geolocation_set_for_region():
    gen = FingerprintGenerator()
    bs = await gen.generate_fingerprint({"os": "linux", "region": "germany"})
    assert bs.geolocation is not None
    assert "lat" in bs.geolocation and "lon" in bs.geolocation
