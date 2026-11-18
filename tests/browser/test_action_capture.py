"""Developer gate: actual upstream extension/native host, two disposable browsers."""

import asyncio
import os
from pathlib import Path

import pytest
from camoufox import AsyncCamoufox
from scripts.action_sync_probe import capture_probe

from camoufox_pm.core.models import BrowserSettings, Profile
from tests.browser.support import offline_launch

pytestmark = pytest.mark.skipif(
    os.environ.get("CPM_ACTION_CAPTURE_ACCEPTANCE") != "1",
    reason="Explicit developer capture gate only",
)


@pytest.mark.browser
async def test_native_capture_routes_only_selected_trusted_input(local_sites, tmp_path):
    origin = local_sites.first.rstrip("/")
    host = os.environ.get("CPM_CAPTURE_FROZEN_HOST")
    with capture_probe(origin, frozen_host=Path(host) if host else None) as (
        root,
        broker,
        prepared,
    ):
        launch_key = broker.key
        options = offline_launch(
            Profile(
                name="Capture proof", browser_settings=BrowserSettings()
            ).to_camoufox_launch_options()
        )
        options.update(headless=True, viewport={"width": 900, "height": 700})
        async with (
            AsyncCamoufox(**{**options, **prepared}) as leader,
            AsyncCamoufox(**{**options, "user_data_dir": str(root / "follower")}) as follower,
        ):
            source = await leader.new_page()
            target = await follower.new_page()
            await source.goto(local_sites.first)
            await target.goto(local_sites.first)
            assert await asyncio.to_thread(broker.armed.wait, 15), broker.phase
            script = """() => {
                document.body.innerHTML='<button id="go" style="width:100px;height:70px">Go</button><input id="password" type="password"><div style="height:5000px"></div>';
                document.body.dataset.clicks='0';
                document.querySelector('#go').onclick=()=>document.body.dataset.clicks=String(Number(document.body.dataset.clicks)+1);
            }"""
            await source.evaluate(script)
            await target.evaluate(script)
            # Hostile page cannot reach extension messaging through ordinary JS.
            await source.evaluate("""() => {
                window.postMessage({kind:'click',sequence:1,x:10,y:10}, '*');
                document.querySelector('#go').dispatchEvent(new MouseEvent('click',{bubbles:true}));
                document.dispatchEvent(new WheelEvent('wheel',{deltaY:100,bubbles:true}));
            }""")
            await asyncio.sleep(0.2)
            assert broker.events.empty()
            await source.locator("#password").click()
            await source.locator("#password").fill("not-captured")
            await asyncio.sleep(0.2)
            assert broker.events.empty()
            await source.locator("#go").click()
            value = await asyncio.to_thread(broker.events.get, True, 10)
            assert value["kind"] == "click"
            assert set(value) == {
                "generation",
                "sequence",
                "kind",
                "tab",
                "origin",
                "width",
                "height",
                "x",
                "y",
            }
            # Test-only explicit mapping with the exact same top-level geometry.
            assert await target.evaluate("[innerWidth,innerHeight]") == [
                value["width"],
                value["height"],
            ]
            await target.mouse.click(value["x"], value["y"])
            assert await target.locator("body").get_attribute("data-clicks") == "1"
            await asyncio.sleep(0.2)
            assert broker.events.empty(), "Follower feedback entered the leader stream"
            await source.mouse.move(400, 300)
            await source.mouse.wheel(0, 250)
            wheel = await asyncio.to_thread(broker.events.get, True, 10)
            assert wheel["kind"] == "scroll" and wheel["delta_y"] == 250
            await target.mouse.move(wheel["x"], wheel["y"])
            await target.mouse.wheel(wheel["delta_x"], wheel["delta_y"])
            await target.wait_for_function("scrollY > 0")
            # Page navigation ends this generation; it must never silently rearm.
            await source.goto(local_sites.second)
            assert await asyncio.to_thread(broker.closed.wait, 10)
            assert broker.events.empty()
            # No launch capability or capture payload was written in the profile.
            key = launch_key.hex().encode()
            assert all(key not in p.read_bytes() for p in (root / "addon").glob("*"))


@pytest.mark.browser
async def test_a_copied_extension_and_key_cannot_change_the_bound_profile(local_sites):
    origin = local_sites.first.rstrip("/")
    host = os.environ.get("CPM_CAPTURE_FROZEN_HOST")
    with capture_probe(origin, frozen_host=Path(host) if host else None) as (
        root,
        broker,
        prepared,
    ):
        options = offline_launch(Profile(name="Wrong-profile control").to_camoufox_launch_options())
        options.update(headless=True, viewport={"width": 900, "height": 700})
        prepared["user_data_dir"] = str(root / "different-profile")
        # Even sharing the launch key is insufficient when ancestry/profile differs.
        async with AsyncCamoufox(**{**options, **prepared}) as browser:
            page = await browser.new_page()
            await page.goto(local_sites.first)
            assert await asyncio.to_thread(broker.closed.wait, 15)
            assert not broker.armed.is_set()
            await page.mouse.click(20, 20)
            assert broker.events.empty()
