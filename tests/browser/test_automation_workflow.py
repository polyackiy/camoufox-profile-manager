"""Real managed workflows behind authenticated proxies, using isolated TLS trust."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from scripts.installed_proxy_acceptance import (
    DNS_NAMES,
    FixtureService,
    has_path,
    workflow_diagnostics,
)

from camoufox_pm.api.principal import Principal
from camoufox_pm.core.workflow_models import WorkflowDefinition, WorkflowStart
from camoufox_pm.core.workflows import WorkflowEngine, workflow_owner
from tests.browser.support import offline_launch
from tests.browser.test_proxy_protocols import trust_test_ca
from tests.support.proxy_protocols import certificates


def startup_diagnostics(error):
    """Only public library code locations/types, never text, locals or paths."""
    for _ in range(4):
        if error.__cause__ is None:
            break
        error = error.__cause__
    kinds = {
        "InvalidPropertyType",
        "InvalidIP",
        "InvalidProxy",
        "UnknownIPLocation",
        "InvalidLocale",
        "ProfileDirectoryError",
        "FpgenModelError",
        "VirtualDisplayError",
        "FileNotFoundError",
        "PermissionError",
        "ConnectionError",
        "ValueError",
        "TypeError",
        "KeyError",
        "TimeoutError",
        "Error",
        "BrowserLaunchError",
        "OSError",
        "RuntimeError",
        "AssertionError",
        "UnsupportedVersion",
        "InvalidOS",
        "NonFirefoxFingerprint",
    }
    kind = type(error).__name__
    frames = []
    trace = error.__traceback__
    while trace:
        code = trace.tb_frame.f_code
        path = Path(code.co_filename)
        if {"camoufox", "camoufox_pm", "playwright", "fpgen"}.intersection(path.parts):
            frames.append({"file": path.name, "function": code.co_name, "line": trace.tb_lineno})
        trace = trace.tb_next
    result = {"kind": kind if kind in kinds else "other", "frames": frames[-4:]}
    if isinstance(error, OSError):
        for key in ("errno", "winerror"):
            value = getattr(error, key, None)
            result[key] = value if type(value) is int else None
    return result


@pytest.mark.browser
@pytest.mark.parametrize("protocol", ["https", "socks5"])
@pytest.mark.parametrize("headless", [True, False], ids=["headless", "desktop"])
async def test_workflow_navigation_after_authenticated_launch_readiness(
    profile_manager, tmp_path, monkeypatch, protocol, headless
):
    certs = certificates(tmp_path, dns_names=DNS_NAMES)
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    manager = profile_manager
    launch = manager.browser_sessions.launch
    startup = []

    async def controlled_launch(profile_id, options, on_exit=None):
        try:
            return await launch(
                profile_id, {**offline_launch(options), "headless": headless}, on_exit
            )
        except Exception as error:
            startup.append(startup_diagnostics(error))
            raise

    monkeypatch.setattr(manager.browser_sessions, "launch", controlled_launch)
    engine = WorkflowEngine(manager.storage, manager)
    with FixtureService(protocol, certs, auth=True) as service:
        fixture = service.fixture
        profile = await manager.create_profile(
            "Disposable workflow",
            proxy_config={
                "type": protocol,
                "server": f"127.0.0.1:{fixture.proxy_port}",
                "username": fixture.username,
                "password": fixture.password,
            },
        )
        trust_test_ca(manager.profiles_dir / f"profile_{profile.id}", certs[0], existing=True)
        nonce = uuid4().hex[:24]
        path = f"/proof/{nonce}-https"
        definition = WorkflowDefinition(
            name="Controlled workflow",
            steps=[
                {"name": "Start", "action": "launch"},
                {
                    "name": "Visit",
                    "action": "navigate",
                    "url": f"https://cpm-origin.invalid:{fixture.https_port}{path}",
                },
                {"name": "Receipt window", "action": "wait", "seconds": 60},
                {"name": "Finish", "action": "close"},
            ],
        )
        value = engine.store.save(
            definition, await workflow_owner(manager.storage, Principal("anonymous_local"))
        )
        try:
            run = await engine.submit(
                value,
                WorkflowStart(
                    request_id=str(uuid4()),
                    revision=1,
                    targets=[{"profile_id": profile.id, "row_version": profile.row_version}],
                ),
            )
            task = engine.tasks[run["id"]]

            async def browser_receipt():
                while not has_path(service.snapshot()[0], path.replace("/proof/", "/receipt/")):
                    assert not task.done(), {
                        "workflow": workflow_diagnostics(engine.store.run(run["id"])),
                        "startup": startup,
                    }
                    await asyncio.sleep(0.05)

            # Observe an actual browser execution signal instead of assuming a
            # one-second sleep is enough on every native CI host. Then exercise
            # real cancellation/cleanup; installed acceptance covers full close.
            await asyncio.wait_for(browser_receipt(), 150)
            engine.cancel(run["id"])
            await asyncio.wait_for(task, 30)
            saved = engine.store.run(run["id"])
            assert saved["state"] == "cancelled", saved["targets"]
            assert [step["state"] for step in saved["targets"][0]["steps"]] == [
                "completed",
                "completed",
                "cancelled",
                "cancelled",
            ]
            assert has_path(service.snapshot()[0], path.replace("/proof/", "/receipt/"))
            assert not manager.browser_sessions.is_live(profile.id)
        finally:
            await engine.stop()
            await manager.close_all_browsers()
    assert not fixture.tasks
