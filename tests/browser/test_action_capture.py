"""Developer gate: actual upstream extension/native host, two disposable browsers."""

import asyncio
import os
import queue
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
            await source.evaluate("""() => {
                const host=document.createElement('div');
                host.id='shadow'; host.style='position:absolute;left:200px;top:100px;width:150px;height:50px';
                const input=document.createElement('input'); input.type='password';
                host.attachShadow({mode:'closed'}).appendChild(input);
                document.body.appendChild(host);
            }""")
            # Hostile page cannot reach extension messaging through ordinary JS.
            await source.add_script_tag(
                content="""(() => {
                window.postMessage({kind:'click',sequence:1,x:10,y:10}, '*');
                document.querySelector('#go').dispatchEvent(new MouseEvent('click',{bubbles:true}));
                document.dispatchEvent(new WheelEvent('wheel',{deltaY:100,bubbles:true}));
            })();"""
            )
            await asyncio.sleep(0.2)
            assert broker.events.empty()
            await source.locator("#password").click()
            await source.locator("#password").fill("not-captured")
            await asyncio.sleep(0.2)
            assert broker.events.empty()
            await source.mouse.click(240, 110)
            await source.mouse.wheel(0, 50)
            await asyncio.sleep(0.2)
            assert broker.events.empty(), "Closed shadow input was captured"
            await source.evaluate("scrollTo(0,0)")
            await source.locator("#go").click()
            try:
                value = await asyncio.to_thread(broker.events.get, True, 10)
            except queue.Empty:
                pytest.fail(
                    "No captured click: "
                    + broker.phase
                    + (" closed" if broker.closed.is_set() else " open")
                )
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
            await source.mouse.wheel(0, 100)
            try:
                wheel = await asyncio.to_thread(broker.events.get, True, 10)
            except queue.Empty:
                pytest.fail("No captured wheel: " + broker.phase)
            assert wheel["kind"] == "scroll" and wheel["delta_y"] > 0
            assert wheel["delta_mode"] in (0, 1)
            # This gate proves capture with explicit units. Upstream engine
            # versions/preferences produce different line amounts per notch;
            # do not invent a general pixel conversion for follower playback.
            # Unit-aware manual wheel replay belongs to broker acceptance.
            await source.wait_for_function("scrollY > 0")
            # Page navigation ends this generation; it must never silently rearm.
            await source.goto(local_sites.second)
            assert await asyncio.to_thread(broker.closed.wait, 10)
            assert broker.events.empty()
            # No launch capability or capture payload was written in the profile.
            key = launch_key.hex().encode()
            assert all(key not in p.read_bytes() for p in (root / "addon").glob("*"))
        # Closed browser data is the input to backup/export/snapshot code.
        # Neither the native credential nor raw event payloads may live there.
        for path in (root / "leader").rglob("*"):
            if path.is_file() and not path.is_symlink():
                payload = path.read_bytes()
                if launch_key in payload or launch_key.hex().encode() in payload:
                    pytest.fail("A capture credential entered closed-profile storage")


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
