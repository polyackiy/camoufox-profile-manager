"""Regressions for native input methods in Linux desktop browser launches."""

import os

import pytest

from camoufox_pm.core import browser_session as bs


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(bs.sys, "platform", "linux")
    monkeypatch.setattr(bs.sys, "frozen", False, raising=False)


def test_headed_linux_disables_focus_test_mode_and_keeps_fingerprint_prefs(linux):
    """With test mode enabled, Fcitx shows candidates but commits no text."""
    original = {"firefox_user_prefs": {"privacy.baselineFingerprintingProtection": False}}
    options = bs._browser_launch_options(original)

    assert options["firefox_user_prefs"] == {
        "privacy.baselineFingerprintingProtection": False,
        "focusmanager.testmode": False,
    }
    assert "focusmanager.testmode" not in original["firefox_user_prefs"]


def test_explicit_focus_preference_is_preserved(linux):
    options = bs._browser_launch_options({"firefox_user_prefs": {"focusmanager.testmode": True}})
    assert options["firefox_user_prefs"]["focusmanager.testmode"] is True


@pytest.mark.parametrize(
    "platform,headless", [("linux", True), ("win32", False), ("darwin", False)]
)
def test_headless_and_other_platforms_keep_their_preferences(monkeypatch, platform, headless):
    monkeypatch.setattr(bs.sys, "platform", platform)
    original = {"headless": headless, "firefox_user_prefs": {"custom.pref": True}}
    assert bs._browser_launch_options(original) == original


@pytest.mark.parametrize("original_path", [None, "", "/usr/local/lib:/opt/custom/lib"])
def test_frozen_linux_restores_host_libraries_for_browser_only(linux, monkeypatch, original_path):
    """Bundled GLib used to make the system im-fcitx5.so fail to load."""
    monkeypatch.setattr(bs.sys, "frozen", True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/camoufox-pm/_internal")
    if original_path is None:
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original_path)
    monkeypatch.setenv("GTK_IM_MODULE", "fcitx")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    parent_env = os.environ.copy()

    env = bs._browser_launch_options({})["env"]

    if original_path:
        assert env["LD_LIBRARY_PATH"] == original_path
    else:
        assert "LD_LIBRARY_PATH" not in env
    assert env["GTK_IM_MODULE"] == "fcitx"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == parent_env["DBUS_SESSION_BUS_ADDRESS"]
    assert os.environ == parent_env


def test_source_install_keeps_its_library_environment(linux, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/custom/lib")
    assert "env" not in bs._browser_launch_options({})


def test_explicit_browser_environment_is_preserved(linux, monkeypatch):
    monkeypatch.setattr(bs.sys, "frozen", True)
    env = {"DISPLAY": ":1", "LD_LIBRARY_PATH": "/opt/custom/lib"}
    assert bs._browser_launch_options({"env": env})["env"] == env


@pytest.mark.asyncio
async def test_session_launch_applies_input_method_defaults(linux, monkeypatch):
    """The compatibility defaults must reach Camoufox on the real launch path."""
    received = {}

    class Camoufox:
        def __init__(self, **options):
            received.update(options)

        async def start(self):
            return object()

        async def __aexit__(self, *_exc):
            pass

    monkeypatch.setattr(bs, "AsyncCamoufox", Camoufox)
    monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", True)
    manager = bs.BrowserSessionManager()
    await manager.launch("ime", {})
    await manager.close("ime")
    assert received["firefox_user_prefs"]["focusmanager.testmode"] is False


@pytest.mark.parametrize("existing", [None, b'// custom\r\nuser_pref("custom.pref", 7);\r\n'])
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.asyncio
async def test_focus_preference_is_present_before_launch_and_user_js_is_restored(
    linux, monkeypatch, tmp_path, existing, fail
):
    """Runtime-only prefs in Playwright 1.60 arrived too late for GTK input."""
    path = tmp_path / "user.js"
    if existing is not None:
        path.write_bytes(existing)
    expected = (existing or b"") + b'\nuser_pref("focusmanager.testmode", false);\n'

    class Camoufox:
        def __init__(self, **options):
            pass

        async def start(self):
            assert path.read_bytes() == expected
            if fail:
                raise RuntimeError("launch failed")
            return object()

        async def __aexit__(self, *_exc):
            pass

    monkeypatch.setattr(bs, "AsyncCamoufox", Camoufox)
    monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", True)
    manager = bs.BrowserSessionManager()
    options = {"persistent_context": True, "user_data_dir": str(tmp_path)}
    if fail:
        with pytest.raises(bs.BrowserLaunchError, match="launch failed"):
            await manager.launch("ime", options)
    else:
        await manager.launch("ime", options)
        await manager.close("ime")
    if existing is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == existing


def test_explicit_focus_override_is_also_used_at_startup(linux, tmp_path):
    options = bs._browser_launch_options(
        {
            "persistent_context": True,
            "user_data_dir": tmp_path,
            "firefox_user_prefs": {"focusmanager.testmode": True},
        }
    )
    with bs._linux_ime_startup_preferences(options):
        assert b'user_pref("focusmanager.testmode", true);' in (tmp_path / "user.js").read_bytes()


def test_concurrent_preferences_edit_is_not_overwritten(linux, tmp_path):
    options = bs._browser_launch_options({"persistent_context": True, "user_data_dir": tmp_path})
    path = tmp_path / "user.js"
    with bs._linux_ime_startup_preferences(options):
        path.write_bytes(b"// changed while launching\n")
    assert path.read_bytes() == b"// changed while launching\n"


@pytest.mark.parametrize(
    "platform,headless", [("linux", True), ("win32", False), ("darwin", False)]
)
def test_other_launches_do_not_touch_startup_preferences(monkeypatch, tmp_path, platform, headless):
    monkeypatch.setattr(bs.sys, "platform", platform)
    path = tmp_path / "user.js"
    path.write_bytes(b"// keep me\n")
    options = {"persistent_context": True, "user_data_dir": tmp_path, "headless": headless}
    with bs._linux_ime_startup_preferences(options):
        assert path.read_bytes() == b"// keep me\n"
    assert path.read_bytes() == b"// keep me\n"
