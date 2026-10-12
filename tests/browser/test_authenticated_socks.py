"""The production session path reaches authenticated SOCKS5 without browser credentials."""

import asyncio

import pytest
from playwright.async_api import Error

from camoufox_pm.core.browser_session import BrowserSessionManager
from camoufox_pm.core.models import Profile, ProxyConfig, ProxyType
from tests.browser.support import offline_launch
from tests.unit.test_socks_relay import upstream


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["relay_failure", "natural_close"])
async def test_authenticated_socks_session_routes_domain_and_cleans_up(tmp_path, ending):
    async with upstream(response_body=b"SOCKS_BROWSER_PROOF") as (port, events):
        profile = Profile(
            name="SOCKS proof",
            proxy=ProxyConfig(
                type=ProxyType.SOCKS5,
                server=f"127.0.0.1:{port}",
                username="alice",
                password="secret",
            ),
        )
        options = offline_launch(profile.to_camoufox_launch_options())
        options.update(headless=True, user_data_dir=str(tmp_path / "profile"))
        manager = BrowserSessionManager()
        session = await manager.launch(profile.id, options)
        relay = session.socks_relay
        try:
            browser = session.camoufox.browser
            page = await browser.new_page()
            await page.goto("http://must-not-resolve.invalid/proof", timeout=10000)
            assert await page.inner_text("body") == "SOCKS_BROWSER_PROOF"
            assert (b"alice", b"secret") in events
            assert (b"must-not-resolve.invalid", 80) in events
            assert set(session.camoufox.launch_options["proxy"]) == {"server"}
            if ending == "relay_failure":
                # Killing the relay must fail closed instead of loading a destination.
                await relay.close()
                with pytest.raises(Error, match="PROXY_CONNECTION_REFUSED|CONNECTION_REFUSED"):
                    await page.goto("http://must-not-resolve.invalid/fail", timeout=5000)
        finally:
            try:
                await session.camoufox.browser.close()
            except Error as exc:
                # The natural-close handler may stop the driver before the
                # close API request completes; the event is still delivered.
                assert "closed" in str(exc)
            while manager._exit_tasks:
                await asyncio.gather(*list(manager._exit_tasks))
            await manager.close(profile.id)
        assert not manager.is_running(profile.id)
        assert relay._closed
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", relay.port)
