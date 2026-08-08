"""Tests for the fingerprint generator.

The generator sets only high-level constraints: the machine (os, screen,
hardware) always, and geography only when a region is asked for. Camoufox owns
the user-agent and WebGL, so those are never hand-crafted here.
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


@pytest.mark.asyncio
async def test_a_profile_with_no_region_has_no_geography():
    """A new profile is a machine, not a place.

    Generating a timezone and coordinates gave every profile a random country
    that contradicted whatever proxy it was later given — and coordinates turn
    Camoufox's IP lookup off entirely, so nothing followed the proxy at all.
    """
    gen = FingerprintGenerator()

    bs = await gen.generate_fingerprint({"os": "windows"})

    assert bs.timezone is None
    assert bs.geolocation is None
    assert bs.languages == ["en-US", "en"]
    # The machine is still generated.
    assert bs.screen in gen.os_screen_combinations["windows"]
    assert bs.hardware_concurrency


@pytest.mark.asyncio
async def test_rotating_a_fingerprint_does_not_move_the_profile():
    """Rotation changes the hardware; it must not hand the profile a country."""
    gen = FingerprintGenerator()
    current = await gen.generate_fingerprint({"os": "macos", "region": "germany"})

    rotated = await gen.rotate_fingerprint(current)

    assert rotated.os == "macos"
    assert rotated.timezone is None
    assert rotated.geolocation is None
