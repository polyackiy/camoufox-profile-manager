"""Reviewed click commands are scoped to one exact browser document."""

from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error

from camoufox_pm.core.action_sync import Click
from camoufox_pm.core.action_sync_tabs import ReviewedTab, ReviewedTabRejected

ORIGIN = "https://example.test"
URL = f"{ORIGIN}/reviewed"
WIDTH, HEIGHT = 900, 700


class Events:
    def __init__(self):
        self.listeners = defaultdict(list)

    def on(self, event, callback):
        self.listeners[event].append(callback)

    def remove_listener(self, event, callback):
        self.listeners[event].remove(callback)

    def emit(self, event, value):
        return [callback(value) for callback in self.listeners[event][:]]


class PinnedDocument:
    def __init__(self):
        self.evaluate = AsyncMock(return_value=True)
        self.dispose = AsyncMock()

    def as_element(self):
        return self


class Page(Events):
    def __init__(self):
        super().__init__()
        self.context = Events()
        self.context.pages = [self]
        self.main_frame = object()
        self.url = URL
        self.viewport_size = {"width": WIDTH, "height": HEIGHT}
        self.closed = False
        self.document = PinnedDocument()
        self.evaluate_handle = AsyncMock(return_value=self.document)
        self.mouse = SimpleNamespace(click=AsyncMock())

    def is_closed(self):
        return self.closed


@pytest.fixture
def tab():
    page = Page()
    return SimpleNamespace(context=page.context), page


async def review(tab):
    return await ReviewedTab.review(*tab, origin=ORIGIN, width=WIDTH, height=HEIGHT)


async def test_preflight_is_read_only_and_send_evaluates_once_in_pinned_document(tab):
    _, page = tab
    reviewed = await review(tab)
    try:
        send = await reviewed.prepare_click(Click(1, 142, 231))
        review_call, preflight_call = page.document.evaluate.await_args_list
        assert review_call.args[1] == {
            "url": URL,
            "origin": ORIGIN,
            "width": WIDTH,
            "height": HEIGHT,
            "point": None,
            "dispatch": False,
        }
        assert preflight_call.args[1] == {
            **review_call.args[1],
            "point": [142, 231],
        }

        await send()
        assert page.document.evaluate.await_count == 3
        assert page.document.evaluate.await_args.args[1] == {
            **preflight_call.args[1],
            "dispatch": True,
        }
        with pytest.raises(ReviewedTabRejected):
            await send()
        assert page.document.evaluate.await_count == 3
        page.mouse.click.assert_not_awaited()
        assert page.evaluate_handle.await_count == 1
    finally:
        await reviewed.close()
    page.document.dispose.assert_awaited_once()
    assert not any(page.listeners.values())
    assert not any(page.context.listeners.values())


@pytest.mark.parametrize(
    "change",
    ["closed", "removed", "context", "frame", "url", "viewport", "navigation", "child", "popup"],
)
async def test_state_change_after_preflight_rejects_send_before_browser_eval(tab, change):
    session, page = tab
    reviewed = await review(tab)
    try:
        send = await reviewed.prepare_click(Click(1, 10, 11))
        match change:
            case "closed":
                page.closed = True
            case "removed":
                page.context.pages.remove(page)
            case "context":
                session.context = Events()
            case "frame":
                page.main_frame = object()
            case "url":
                page.url = f"{ORIGIN}/different-document"
            case "viewport":
                page.viewport_size = {"width": WIDTH + 1, "height": HEIGHT}
            case "navigation":
                # Reload can leave the same URL and main Frame wrapper.
                page.emit("framenavigated", page.main_frame)
            case "child":
                page.emit("frameattached", object())
            case "popup":
                page.context.emit("page", object())

        with pytest.raises(ReviewedTabRejected):
            await send()
        assert page.document.evaluate.await_count == 2
        assert not reviewed.is_current()
    finally:
        await reviewed.close()


@pytest.mark.parametrize("result", [False, Error("Execution context destroyed on private URL")])
async def test_send_failure_never_reacquires_current_document_or_reveals_browser_error(tab, result):
    _, page = tab
    reviewed = await review(tab)
    try:
        send = await reviewed.prepare_click(Click(1, 10, 11))
        page.document.evaluate.side_effect = [result]
        with pytest.raises(ReviewedTabRejected) as rejection:
            await send()
        assert "private URL" not in str(rejection.value)
        assert page.document.evaluate.await_count == 3
        assert page.evaluate_handle.await_count == 1
        page.mouse.click.assert_not_awaited()
        assert not reviewed.is_current()
    finally:
        await reviewed.close()


@pytest.mark.parametrize("x,y", [(-1, 0), (900, 0), (0, 700), (1.0, 0), (0, True)])
async def test_coordinates_are_validated_before_browser_preflight(tab, x, y):
    _, page = tab
    reviewed = await review(tab)
    try:
        with pytest.raises(ReviewedTabRejected):
            await reviewed.prepare_click(Click(1, x, y))
        assert page.document.evaluate.await_count == 1
    finally:
        await reviewed.close()


async def test_nonmember_is_rejected_without_reading_document(tab):
    _, page = tab
    with pytest.raises(ReviewedTabRejected):
        await review((SimpleNamespace(context=Events()), page))
    page.evaluate_handle.assert_not_awaited()


async def test_failed_review_releases_handle_and_listeners(tab):
    _, page = tab
    page.document.evaluate.return_value = False
    with pytest.raises(ReviewedTabRejected):
        await review(tab)
    page.document.dispose.assert_awaited_once()
    assert not any(page.listeners.values())
    assert not any(page.context.listeners.values())


async def test_dialog_invalidates_review_before_it_is_dismissed(tab):
    _, page = tab
    reviewed = await review(tab)
    try:
        send = await reviewed.prepare_click(Click(1, 10, 11))
        dialog = SimpleNamespace(type="prompt", dismiss=AsyncMock())
        (settlement,) = page.context.emit("dialog", dialog)
        try:
            with pytest.raises(ReviewedTabRejected):
                await send()
            assert page.document.evaluate.await_count == 2
        finally:
            await settlement
        dialog.dismiss.assert_awaited_once()
    finally:
        await reviewed.close()
