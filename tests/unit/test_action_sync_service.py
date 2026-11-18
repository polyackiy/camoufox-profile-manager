"""Keep the prepared launch callback internal and out of browser receipts."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_pm.api.principal import Principal
from camoufox_pm.core.action_sync_service import ActionSyncUnavailable, CaptureLaunch


@pytest.mark.skipif(sys.platform == "win32", reason="Windows requires the bundled console host")
def test_source_console_host_keeps_launch_path_but_binds_os_process_image(tmp_path, monkeypatch):
    from camoufox_pm.core import action_sync_service

    interpreter = tmp_path / "test venv" / "python"
    image = tmp_path / "Python.app" / "Python"
    monkeypatch.setattr(action_sync_service.sys, "executable", str(interpreter))
    monkeypatch.setattr(
        action_sync_service.psutil, "Process", lambda: SimpleNamespace(exe=lambda: str(image))
    )
    launcher, expected_image = action_sync_service._console_host(tmp_path)
    assert f"'{interpreter}' -I -m camoufox_pm.core.action_capture_host" in launcher.read_text()
    assert expected_image == image


@pytest.mark.parametrize("kind", ["legacy_api_key", "anonymous_local", "system", "session"])
async def test_prepared_launch_requires_a_current_cookie(profile_manager, monkeypatch, kind):
    launch = AsyncMock()
    monkeypatch.setattr(profile_manager, "_launch_browser", launch)
    with pytest.raises(ActionSyncUnavailable):
        async with profile_manager.prepare_action_capture(
            Principal(kind, username="owner"),
            "f" * 64,
            profile_id="leader",
            origin="https://example.test",
        ):
            pytest.fail("Invalid authority reached the prepared browser")
    launch.assert_not_awaited()


async def test_private_capture_configuration_never_enters_launch_receipt(
    profile_manager, monkeypatch, tmp_path
):
    selected = await profile_manager.create_profile("Test", generate_fingerprint=False)
    from tests.unit.test_browser_session import FakeCamoufox

    captured = []
    public = []

    class Camoufox(FakeCamoufox):
        def __init__(self, **options):
            super().__init__()
            self.options = options

        async def start(self):
            captured.append(self.options["from_options"]["env"].copy())
            return await super().start()

    def sdk_options(**options):
        public.append(dict(options.get("env") or {}))
        return {"env": public[-1].copy()}

    monkeypatch.setattr("camoufox.utils.launch_options", sdk_options)
    monkeypatch.setattr("camoufox_pm.core.browser_session.AsyncCamoufox", Camoufox)
    monkeypatch.setattr("camoufox_pm.core.fingerprint_store.resolve", lambda _options: None)
    channel = SimpleNamespace(
        environment=lambda *_a, **_kw: "private-native-token", stop=lambda: None
    )
    capture = CaptureLaunch(
        SimpleNamespace(is_authenticated=AsyncMock(return_value=True)),
        SimpleNamespace(blocked=False),
        channel,
        tmp_path / "browser",
        "native-extension",
        tmp_path / "addon",
        tmp_path / "host",
    )
    try:
        receipt = await profile_manager._launch_browser(selected.id, capture_launch=capture)
        session = profile_manager.browser_sessions.active_sessions[selected.id]
        assert captured[0]["CPM_ACTION_CAPTURE_PROBE"] == "private-native-token"
        assert "CPM_ACTION_CAPTURE_PROBE" not in public[0]
        assert "private-native-token" not in str(receipt)
        assert "CPM_ACTION_CAPTURE_PROBE" not in session.camoufox.options["from_options"]["env"]
        assert session.action_capture is channel
    finally:
        await profile_manager.close_all_browsers()


async def test_prepared_options_refuse_revoked_cookie_before_issuing_capability(tmp_path):
    def refuse_config(*_args, **_kwargs):
        pytest.fail("The revoked session received a launch capability")

    capture = CaptureLaunch(
        SimpleNamespace(is_authenticated=AsyncMock(return_value=False)),
        SimpleNamespace(blocked=False),
        SimpleNamespace(environment=refuse_config),
        tmp_path / "browser",
        "native-extension",
        tmp_path / "addon",
        tmp_path / "host",
    )
    with pytest.raises(ActionSyncUnavailable):
        await capture.options({})


@pytest.mark.parametrize(
    "origin", ["https://example.test", "https://localhost/path", "http://localhost@elsewhere.test"]
)
def test_prepared_playback_is_restricted_to_canonical_loopback_origins(origin):
    from camoufox_pm.core.action_sync_service import _reviewed_origin

    with pytest.raises(ActionSyncUnavailable):
        _reviewed_origin(origin)
    assert _reviewed_origin("http://127.0.0.1:8080") == "http://127.0.0.1:8080"


@pytest.mark.parametrize(
    "variable,value", [("DEBUGP", ""), ("DEBUG", "pw:protocol"), ("DEBUG", "*")]
)
async def test_prepared_launch_refuses_protocol_logging(monkeypatch, tmp_path, variable, value):
    monkeypatch.setenv(variable, value)
    authority = SimpleNamespace(is_authenticated=AsyncMock(return_value=True))
    capture = CaptureLaunch(
        authority,
        SimpleNamespace(blocked=False),
        SimpleNamespace(),
        tmp_path / "browser",
        "native-extension",
        tmp_path / "addon",
        tmp_path / "host",
    )
    with pytest.raises(ActionSyncUnavailable):
        await capture.options({"persistent_context": True, "headless": False})
    authority.is_authenticated.assert_not_awaited()
