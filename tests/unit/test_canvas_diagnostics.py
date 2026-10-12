"""Canvas failure diagnostics must stay bounded and avoid raw fixture content."""

import base64
import json

import pytest

from camoufox_pm.core.models import BrowserSettings, Profile
from tests.browser.test_fingerprint_stability import (
    canvas_launch_diagnostic,
    canvas_probe_options,
    canvas_profile_diagnostic,
    pinned_options,
    save_canvas_diagnostic,
)


def test_restart_diagnostic_contains_only_bounded_config_hashes_and_pixel_metrics():
    raw = "synthetic-sensitive-placeholder"
    config = {f"property-{index:03}": raw for index in range(100)}
    config["fonts"] = [raw] * 500
    options = {
        "config": config,
        "firefox_user_prefs": {"privacy.baselineFingerprintingProtection": False},
    }
    snapshot = {
        "value": "data:image/png;base64," + raw,
        "pixelHash": "0123456789abcdef",
        "fontStatus": "loaded",
        "fontAvailable": True,
        "metrics": {"width": 97.5},
        "dpr": 1,
    }
    result = canvas_launch_diagnostic(options, snapshot, "fedcba9876543210")
    assert result["baselineProtection"] is False
    assert result["pixelHash"] == snapshot["pixelHash"]
    assert result["metrics"] == {"width": 97.5}
    assert len(result["configKeyHashes"]) == 64
    encoded = json.dumps(result)
    assert raw not in encoded and "data:image" not in encoded
    assert len(encoded) < 4000
    assert result == canvas_launch_diagnostic(options, snapshot, "fedcba9876543210")


def test_profile_diagnostics_whitelist_renderer_prefs_and_never_publish_paths(tmp_path):
    private = "synthetic-private-placeholder"
    (tmp_path / "prefs.js").write_text(
        "\n".join(
            [
                'user_pref("gfx.canvas.accelerated", false);',
                'user_pref("gfx.font_rendering.cleartype_params.force_gdi_classic_for_families", "Arial");',
                f'user_pref("gfx.test.path", "C:\\\\{private}");',
                f'user_pref("browser.download.dir", "{private}");',
                'user_pref("privacy.baselineFingerprintingProtection", false);',
                'user_pref("gfx.malformed", not-json);',
            ]
        )
    )
    (tmp_path / "startupCache").mkdir()
    (tmp_path / "startupCache" / private).write_text("cache")
    (tmp_path / "unrelated-secret").write_text(private)
    result = canvas_profile_diagnostic(tmp_path)
    assert result["persistedPrefs"]["gfx.canvas.accelerated"] is False
    assert (
        result["persistedPrefs"][
            "gfx.font_rendering.cleartype_params.force_gdi_classic_for_families"
        ]
        == "Arial"
    )
    assert "sha256" in result["persistedPrefs"]["gfx.test.path"]
    assert "gfx.malformed" not in result["persistedPrefs"]
    assert result["caches"] == {"startupCache": {"present": True, "entries": 1}}
    assert private not in json.dumps(result)


def test_profile_diagnostics_are_bounded_and_handle_missing_or_oversize_prefs(tmp_path):
    assert canvas_profile_diagnostic(tmp_path) == {
        "persistedPrefs": {},
        "prefFiles": {},
        "caches": {},
    }
    (tmp_path / "prefs.js").write_text(
        "\n".join(f'user_pref("gfx.test.{i:03}", {i});' for i in range(200))
    )
    assert len(canvas_profile_diagnostic(tmp_path)["persistedPrefs"]) == 128
    (tmp_path / "prefs.js").write_bytes(b" " * 2_000_001)
    assert canvas_profile_diagnostic(tmp_path)["persistedPrefs"] == {}
    assert canvas_profile_diagnostic(tmp_path)["prefFiles"] == {"prefs.js": {"bytes": 2_000_001}}


def test_canvas_artifact_saves_synthetic_png_and_sanitized_metadata(tmp_path):
    png = b"\x89PNG\r\n\x1a\nsynthetic-fixture"
    value = "data:image/png;base64," + base64.b64encode(png).decode()
    record = {
        "pngHash": "0123456789abcdef",
        "graphics": [{"key": "AzureCanvasBackend", "value": "skia"}],
    }
    save_canvas_diagnostic(tmp_path, 1, value, record)
    assert (tmp_path / "launch-1.png").read_bytes() == png
    assert json.loads((tmp_path / "launch-1.json").read_text()) == record
    assert "data:image" not in (tmp_path / "launch-1.json").read_text()


@pytest.mark.parametrize("mode,value", [("gdi-classic", 2), ("natural-symmetric", 5)])
def test_native_text_probes_are_explicit_isolated_and_keep_default_acceptance(mode, value):
    profile = Profile(name="synthetic", browser_settings=BrowserSettings(stable_canvas=True))
    profile.fingerprint = {"fonts": ["Arial"], "screen.width": 1920}
    default = pinned_options(profile)
    assert canvas_probe_options(profile, "default") == default
    probe = canvas_probe_options(profile, mode)
    assert probe["config"] == default["config"]
    assert probe["firefox_user_prefs"] == {
        "privacy.baselineFingerprintingProtection": False,
        "gfx.font_rendering.cleartype_params.rendering_mode": value,
    }
    assert default["firefox_user_prefs"] == {"privacy.baselineFingerprintingProtection": False}
    assert canvas_probe_options(profile, "default") == default


def test_unknown_probe_fails_explicitly():
    profile = Profile(name="synthetic", fingerprint={"fonts": ["Arial"]})
    with pytest.raises(ValueError):
        canvas_probe_options(profile, "typo")
