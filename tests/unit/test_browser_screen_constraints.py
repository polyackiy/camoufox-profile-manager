"""Declared profile geometry must be independent of the current host display."""

import pytest
from camoufox.fingerprints import Screen

from camoufox_pm.core.browser_session import _browser_launch_options


def test_declared_headed_screen_keeps_its_identity_without_implicit_monitor_bounds():
    config = {"screen.width": 1920, "screen.height": 1080, "window.outerWidth": 1280}
    original = {"headless": False, "config": config}
    prepared = _browser_launch_options(original)
    assert isinstance(prepared["screen"], Screen)
    assert prepared["screen"].as_conditions() == {}
    assert prepared["config"] == config
    assert "screen" not in original


def test_explicit_generator_screen_constraints_are_preserved():
    constraint = Screen(max_width=1280, max_height=720)
    prepared = _browser_launch_options(
        {"config": {"screen.width": 1920, "screen.height": 1080}, "screen": constraint}
    )
    assert prepared["screen"] is constraint
    assert prepared["screen"].as_conditions()


@pytest.mark.parametrize(
    "options",
    [
        {"headless": True, "config": {"screen.width": 1920, "screen.height": 1080}},
        {"config": {}},
        {"config": {"screen.width": 1920}},
        {"config": {"screen.width": True, "screen.height": 1080}},
        {"config": {"screen.width": -1, "screen.height": 1080}},
    ],
)
def test_unpinned_incomplete_and_headless_launches_keep_upstream_screen_policy(options):
    assert "screen" not in _browser_launch_options(options)
