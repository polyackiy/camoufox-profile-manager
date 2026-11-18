"""The optional WebExtension's privileges and message handling stay isolated."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.action_sync_probe import prepare_addon


def test_launch_credential_never_enters_addon_or_extension_storage(tmp_path):
    broker = SimpleNamespace(origin="https://site.test:12345", generation="g", key=b"private key")
    root = prepare_addon(tmp_path, broker, "cpm.host", "addon@cpm.local")
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["permissions"] == ["nativeMessaging", "tabs", "https://site.test/*"]
    assert manifest["content_scripts"][0]["all_frames"] is False
    assert "web_accessible_resources" not in manifest
    assert "externally_connectable" not in manifest
    assert all(broker.key not in path.read_bytes() for path in root.iterdir())
    assert all(b"exposeBinding" not in path.read_bytes() for path in root.iterdir())


def test_background_does_not_accept_page_frame_or_stale_input():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the extension contract; native workflow installs it")
    script = Path(__file__).with_name("action_capture_extension_test.cjs")
    subprocess.run([node, str(script)], check=True, capture_output=True, text=True, timeout=15)


def test_native_host_preparation_failure_does_not_leave_a_broker(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from camoufox import multiversion, pkgman
    from scripts import action_sync_probe

    monkeypatch.setattr(multiversion, "get_active_path", lambda: tmp_path)
    monkeypatch.setattr(pkgman, "launch_path", lambda active: str(tmp_path / "camoufox"))
    broker = Mock()
    monkeypatch.setattr(action_sync_probe, "ProbeBroker", broker)
    monkeypatch.setattr(
        action_sync_probe, "source_host", Mock(side_effect=OSError("host setup failed"))
    )
    with pytest.raises(OSError, match="host setup failed"):
        with action_sync_probe.capture_probe("https://site.test"):
            pytest.fail("Invalid preparation yielded a capture session")
    broker.assert_not_called()
