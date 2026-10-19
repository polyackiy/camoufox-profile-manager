"""Host graphics drivers must use matching host C++/X11 runtimes."""

import importlib.util
from pathlib import Path


def test_host_graphics_runtime_is_not_shadowed():
    path = Path(__file__).resolve().parents[2] / "packaging" / "linux_graphics_bundle.py"
    spec = importlib.util.spec_from_file_location("linux_graphics_bundle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    collected = [
        ("libstdc++.so.6", "/build/libstdc++.so.6", "BINARY"),
        ("libstdc++.so.6.0.30", "/build/libstdc++.so.6.0.30", "BINARY"),
        ("libX11.so.6", "/build/libX11.so.6", "BINARY"),
        ("libX11.so.6.4.0", "/build/libX11.so.6.4.0", "BINARY"),
        ("libQt6Core.so.6", "/qt/libQt6Core.so.6", "BINARY"),
        ("libX11-xcb.so.1", "/build/libX11-xcb.so.1", "BINARY"),
        ("other/libstdc++helper.so", "/other", "EXTENSION"),
    ]
    original = list(collected)
    assert module.use_host_graphics_runtime(collected) == collected[4:]
    assert collected == original
