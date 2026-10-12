"""Canvas failure diagnostics must stay bounded and avoid raw fixture content."""

import json

from tests.browser.test_fingerprint_stability import canvas_launch_diagnostic


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
