"""The actual browser runs the last hit check and the DOM click together."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from camoufox import AsyncCamoufox

from camoufox_pm.core.action_sync import Click
from camoufox_pm.core.action_sync_tabs import ReviewedTab, ReviewedTabRejected
from camoufox_pm.core.models import Profile
from tests.browser.support import offline_launch

VIEWPORT = {"width": 900, "height": 700}
PAGE_STYLE = """<!doctype html><style>
#target {position:absolute;left:10px;top:10px;width:100px;height:100px;}
</style>"""
BUTTON = """<button id=target type=button
    onclick="document.body.dataset.clicked = String(event.isTrusted)">Go</button>"""


async def exercise_reviewed_click(page, url, other_origin):
    session = SimpleNamespace(context=page.context)
    origin = url.rstrip("/")
    click = Click(1, 40, 40)
    await page.goto(url)

    @asynccontextmanager
    async def review(html=BUTTON):
        await page.set_content(PAGE_STYLE + html, wait_until="load")
        tab = await ReviewedTab.review(session, page, origin=origin, **VIEWPORT)
        try:
            yield tab
        finally:
            await tab.close()

    with pytest.raises(ReviewedTabRejected):
        await ReviewedTab.review(session, page, origin=other_origin.rstrip("/"), **VIEWPORT)

    async with review() as tab:
        send = await tab.prepare_click(click)
        assert await page.locator("body").get_attribute("data-clicked") is None
        await send()
        assert await page.locator("body").get_attribute("data-clicked") == "false"
        with pytest.raises(ReviewedTabRejected):
            await send()

    async with review() as tab:
        send = await tab.prepare_click(click)
        await page.evaluate("""() => {
            document.querySelector('button').outerHTML = '<input id=target type=password>';
        }""")
        with pytest.raises(ReviewedTabRejected):
            await send()
        assert await page.locator("body").get_attribute("data-clicked") is None

    for unsupported in (
        "<em id=target contenteditable>text</em>",
        "<iframe id=target srcdoc='<p>frame</p>'></iframe>",
        """<div id=target></div><script>
        document.getElementById('target').attachShadow({mode:'closed'}).innerHTML =
            '<input type=password>';
        </script>""",
    ):
        async with review(unsupported) as tab:
            with pytest.raises(ReviewedTabRejected):
                await tab.prepare_click(click)

    async with review() as tab:
        send = await tab.prepare_click(click)
        await page.evaluate("""() => {
            document.body.appendChild(document.createElement('dialog')).showModal();
        }""")
        with pytest.raises(ReviewedTabRejected):
            await send()

    async with review() as tab:
        send = await tab.prepare_click(click)
        await page.evaluate("() => alert('Synthetic browser dialog')")
        assert not tab.is_current()
        with pytest.raises(ReviewedTabRejected):
            await send()

    async with review() as tab:
        send = await tab.prepare_click(click)
        await page.evaluate("""() => {
            document.documentElement.replaceWith(document.documentElement.cloneNode(true));
        }""")
        with pytest.raises(ReviewedTabRejected):
            await send()
        assert await page.locator("body").get_attribute("data-clicked") is None

    async with review() as tab:
        send = await tab.prepare_click(click)
        await page.reload()
        assert page.url == url
        with pytest.raises(ReviewedTabRejected):
            await send()


@pytest.mark.browser
async def test_browser_dispatch_stays_in_reviewed_document(local_sites, tmp_path):
    options = offline_launch(Profile(name="Reviewed click").to_camoufox_launch_options())
    options.update(
        headless=True, viewport=VIEWPORT, user_data_dir=str(tmp_path), persistent_context=True
    )
    async with AsyncCamoufox(**options) as context:
        page = await context.new_page()
        await exercise_reviewed_click(page, local_sites.first, local_sites.second)
