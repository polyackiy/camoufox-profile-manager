"""Real Camoufox HTTP/HTTPS destinations, proxy authentication, TLS and cleanup.

Only the isolated browser profile trusts the ephemeral test CA. Verification is
never disabled, and no OS/user/browser trust store outside tmp_path is modified.
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from playwright.async_api import Error

from camoufox_pm.core.browser_session import BrowserSessionManager
from camoufox_pm.core.models import Profile, ProxyConfig
from tests.browser.support import offline_launch
from tests.support.proxy_protocols import certificates, proxy_fixture


def trust_test_ca(directory, ca):
    configured = os.environ.get("CPM_TEST_NSS_CERTUTIL")
    executable = configured or shutil.which("certutil")
    if not executable or (os.name == "nt" and not configured):
        if os.environ.get("CI"):
            pytest.fail("CI must supply the NSS certutil binary for isolated TLS acceptance")
        pytest.skip("Install NSS certutil to run isolated browser TLS acceptance")
    directory.mkdir()
    commands = [
        [executable, "-N", "-d", f"sql:{directory}", "--empty-password"],
        [
            executable,
            "-A",
            "-d",
            f"sql:{directory}",
            "-n",
            "CPM ephemeral fixture CA",
            "-t",
            "C,,",
            "-i",
            str(ca),
        ],
    ]
    env = {
        **os.environ,
        "PATH": str(Path(executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    for command in commands:
        subprocess.run(command, env=env, check=True, capture_output=True, timeout=20)


async def close_session(manager, session, profile_id):
    try:
        await session.camoufox.browser.close()
    except Error as error:
        assert "closed" in str(error)
    while manager._exit_tasks:
        await asyncio.gather(*list(manager._exit_tasks))
    await manager.close(profile_id)
    assert not manager.is_running(profile_id)
    if session.socks_relay:
        assert session.socks_relay._closed
        assert not session.socks_relay._tasks


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,auth",
    [
        ("http", False),
        ("http", True),
        ("https", False),
        ("https", True),
        ("socks4", False),
        ("socks5", False),
        ("socks5", True),
    ],
)
async def test_real_browser_both_destinations_refusal_and_cleanup(tmp_path, protocol, auth):
    certs = certificates(tmp_path)
    directory = tmp_path / "browser"
    trust_test_ca(directory, certs[0])
    async with proxy_fixture(protocol, certs, auth=auth) as fixture:
        profile = Profile(
            name="Protocol proof",
            proxy=ProxyConfig(
                type=protocol,
                server=f"127.0.0.1:{fixture.proxy_port}",
                username=fixture.username if auth else None,
                password=fixture.password if auth else None,
            ),
        )
        options = offline_launch(profile.to_camoufox_launch_options())
        options.update(headless=True, user_data_dir=str(directory))
        manager = BrowserSessionManager()
        session = await manager.launch(profile.id, options)
        try:
            page = await session.camoufox.browser.new_page()
            for scheme, port in [("http", fixture.http_port), ("https", fixture.https_port)]:
                response = await page.goto(
                    f"{scheme}://cpm-origin.invalid:{port}/proof", timeout=15000
                )
                assert response.status == 200
                assert await page.inner_text("body") == "CPM_PROXY_PROTOCOL_PROOF"
            if protocol.startswith("socks"):
                destinations = [event[1] for event in fixture.events if event[0] == "connect"]
                assert b"cpm-origin.invalid" in destinations
            if auth and protocol == "socks5":
                assert ("auth", True) in fixture.events
                assert set(session.camoufox.launch_options["proxy"]) == {"server"}
            before = len([event for event in fixture.events if event[0] == "origin"])
            fixture.reject = True
            with pytest.raises(Error):
                await page.goto(
                    f"http://cpm-rejected.invalid:{fixture.http_port}/refused", timeout=8000
                )
            assert len([event for event in fixture.events if event[0] == "origin"]) == before
        finally:
            await close_session(manager, session, profile.id)
    assert not fixture.tasks


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "https"])
async def test_real_browser_rejects_untrusted_destination_or_proxy(tmp_path, protocol):
    certs = certificates(tmp_path)
    async with proxy_fixture(protocol, certs) as fixture:
        profile = Profile(
            name="TLS refusal proof",
            proxy=ProxyConfig(type=protocol, server=f"127.0.0.1:{fixture.proxy_port}"),
        )
        options = offline_launch(profile.to_camoufox_launch_options())
        options.update(headless=True, user_data_dir=str(tmp_path / "untrusted-browser"))
        manager = BrowserSessionManager()
        session = await manager.launch(profile.id, options)
        try:
            page = await session.camoufox.browser.new_page()
            with pytest.raises(Error):
                await page.goto(
                    f"https://cpm-origin.invalid:{fixture.https_port}/untrusted", timeout=10000
                )
            assert not any(event[0] == "origin" for event in fixture.events)
        finally:
            await close_session(manager, session, profile.id)
