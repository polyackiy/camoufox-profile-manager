"""Real managed workflows behind authenticated proxies, using isolated TLS trust."""

import asyncio
from uuid import uuid4

import pytest
from scripts.installed_proxy_acceptance import DNS_NAMES, FixtureService, has_path

from camoufox_pm.api.principal import Principal
from camoufox_pm.core.workflow_models import WorkflowDefinition, WorkflowStart
from camoufox_pm.core.workflows import WorkflowEngine, workflow_owner
from tests.browser.support import offline_launch
from tests.browser.test_proxy_protocols import trust_test_ca
from tests.support.proxy_protocols import certificates


@pytest.mark.browser
@pytest.mark.parametrize("protocol", ["https", "socks5"])
async def test_workflow_navigation_after_authenticated_launch_readiness(
    profile_manager, tmp_path, monkeypatch, protocol
):
    certs = certificates(tmp_path, dns_names=DNS_NAMES)
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    manager = profile_manager
    launch = manager.browser_sessions.launch

    async def controlled_launch(profile_id, options, on_exit=None):
        return await launch(profile_id, {**offline_launch(options), "headless": True}, on_exit)

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
                {"name": "Receipt window", "action": "wait", "seconds": 1},
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
            await asyncio.wait_for(engine.tasks[run["id"]], 150)
            saved = engine.store.run(run["id"])
            assert saved["state"] == "completed", saved["targets"]
            assert has_path(service.snapshot()[0], path.replace("/proof/", "/receipt/"))
            assert not manager.browser_sessions.is_live(profile.id)
        finally:
            await engine.stop()
            await manager.close_all_browsers()
    assert not fixture.tasks
