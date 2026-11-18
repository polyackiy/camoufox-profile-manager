"""Reviewed click playback for controlled pages, with no page-callable bridge.

The Playwright handle pins the document across preflight and dispatch. The page
still controls its own DOM, and the evaluation world can vary by engine.
Playback is a synthetic DOM click without a physical pointer sequence or
trusted user activation.
"""

from __future__ import annotations

from collections.abc import Awaitable
from contextlib import ExitStack, suppress
from typing import TYPE_CHECKING, Literal

from playwright.async_api import Error

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Dialog, JSHandle, Page

    from .action_sync import Click, SendClick
    from .browser_session import BrowserSession


# The URL and geometry come from the manager's review. Capture can only supply
# integer coordinates. A single synchronous evaluation guards the hit and clicks
# it within the execution context of the reviewed document's root handle.
_CLICK = """(root, {url, origin, width, height, point, dispatch}) => {
    if (window !== window.top || document.documentElement !== root || !root.isConnected ||
        (location.protocol !== 'http:' && location.protocol !== 'https:') ||
        location.href !== url || location.origin !== origin || window.origin !== origin ||
        innerWidth !== width || innerHeight !== height || document.designMode === 'on' ||
        document.querySelector('dialog:modal, [aria-modal="true"]')) {
        return false;
    }
    if (point === null) return true;
    const [x, y] = point;
    if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0 || x >= width || y >= height) {
        return false;
    }
    const hit = document.elementFromPoint(x, y);
    if (!(hit instanceof HTMLElement) || hit.getRootNode() !== document) return false;

    // Hit testing can return the host of a closed shadow tree. These built-in
    // elements can hide such a tree, so their direct coordinate hits are out of scope.
    const shadowHost = /^(article|aside|blockquote|body|div|footer|h[1-6]|header|main|nav|p|section|span)$/;
    if (shadowHost.test(hit.localName)) return false;
    const unsupported = 'input,textarea,select,option,label,iframe,frame,object,embed,' +
        'dialog,:disabled,[inert],a[download],area[download],[role~="textbox"],[role~="searchbox"],' +
        '[role~="combobox"],[role~="spinbutton"],[role~="listbox"],[role~="slider"]';
    for (let node = hit; node; node = node.parentElement) {
        if (node.matches(unsupported) || node.isContentEditable || node.shadowRoot ||
            node.localName.includes('-') || node.assignedSlot ||
            (node instanceof HTMLButtonElement && node.form && node.type !== 'button')) {
            return false;
        }
    }
    if (dispatch) HTMLElement.prototype.click.call(hit);
    return true;
}"""

_PAGE_EVENTS: tuple[
    Literal["close", "crash", "frameattached", "framedetached", "framenavigated"], ...
] = ("close", "crash", "frameattached", "framedetached", "framenavigated")


class ReviewedTabRejected(RuntimeError):
    """Fixed diagnostic without URLs, event content or browser exception text."""

    def __init__(self) -> None:
        super().__init__("Reviewed tab is no longer eligible for click playback")


class ReviewedTab:
    """One manager-selected Page and document. Close after the owning run drains."""

    def __init__(
        self, session: BrowserSession, page: Page, origin: str, width: int, height: int
    ) -> None:
        self._session = session
        self._context: BrowserContext = page.context
        self._page = page
        self._frame = page.main_frame
        self._url = page.url
        self._origin = origin
        self._width = width
        self._height = height
        self._handle: JSHandle | None = None
        self._valid = True
        self._listeners = ExitStack()

    @classmethod
    async def review(
        cls, session: BrowserSession, page: Page, *, origin: str, width: int, height: int
    ) -> ReviewedTab:
        """Pin the selected tab, checking it against the approved origin and viewport."""
        if (
            type(origin) is not str
            or not origin.startswith(("http://", "https://"))
            or type(width) is not int
            or type(height) is not int
            or width <= 0
            or height <= 0
        ):
            raise ReviewedTabRejected()
        reviewed = cls(session, page, origin, width, height)
        if not reviewed._url.startswith(("http://", "https://")) or not reviewed.is_current():
            raise ReviewedTabRejected()
        try:
            reviewed._listen()
            try:
                reviewed._handle = await page.evaluate_handle("() => document.documentElement")
            except Error:
                raise ReviewedTabRejected() from None
            if reviewed._handle.as_element() is None:
                raise ReviewedTabRejected()
            await reviewed._evaluate(None)
            return reviewed
        except BaseException:
            await reviewed.close()
            raise

    def _listen(self) -> None:
        invalidate = self._invalidate
        for event in _PAGE_EVENTS:
            self._page.on(event, invalidate)
            self._listeners.callback(self._page.remove_listener, event, invalidate)
        for context_event in ("page", "close"):
            self._context.on(context_event, invalidate)
            self._listeners.callback(self._context.remove_listener, context_event, invalidate)
        dialog = self._on_dialog
        self._context.on("dialog", dialog)
        self._listeners.callback(self._context.remove_listener, "dialog", dialog)

    def _invalidate(self, _event: object = None) -> None:
        self._valid = False

    def _on_dialog(self, dialog: Dialog) -> Awaitable[None]:
        self._invalidate()
        return self._settle_dialog(dialog)

    @staticmethod
    async def _settle_dialog(dialog: Dialog) -> None:
        # Match Playwright's default handling so subscribing to dialogs does
        # not leave the browser blocked. Never inspect the dialog's content.
        with suppress(Error):
            if dialog.type == "beforeunload":
                await dialog.accept()
            else:
                await dialog.dismiss()

    def is_current(self) -> bool:
        """Check locally observed identity and invalidation, without starting browser I/O."""
        if not self._valid:
            return False
        page = self._page
        viewport = page.viewport_size
        self._valid = (
            self._session.context is self._context
            and page.context is self._context
            and any(member is page for member in self._context.pages)
            and not page.is_closed()
            and page.main_frame is self._frame
            and page.url == self._url
            # Camoufox may use the native window without a configured viewport.
            # The in-document checks always inspect the actual viewport.
            and (viewport is None or viewport == {"width": self._width, "height": self._height})
        )
        return self._valid

    async def _evaluate(self, point: list[int] | None, *, dispatch: bool = False) -> None:
        handle = self._handle
        if handle is None or not self.is_current():
            raise ReviewedTabRejected()
        try:
            accepted = await handle.evaluate(
                _CLICK,
                {
                    "url": self._url,
                    "origin": self._origin,
                    "width": self._width,
                    "height": self._height,
                    "point": point,
                    "dispatch": dispatch,
                },
            )
        except Error:
            self._invalidate()
            raise ReviewedTabRejected() from None
        if accepted is not True or (not dispatch and not self.is_current()):
            self._invalidate()
            raise ReviewedTabRejected()

    async def prepare_click(self, click: Click) -> SendClick:
        """Check a bounded hit, then hand the broker a single-use browser command."""
        if (
            type(click.x) is not int
            or type(click.y) is not int
            or not 0 <= click.x < self._width
            or not 0 <= click.y < self._height
        ):
            raise ReviewedTabRejected()
        point = [click.x, click.y]
        await self._evaluate(point)
        spent = False

        async def send() -> None:
            nonlocal spent
            if spent:
                raise ReviewedTabRejected()
            spent = True
            await self._evaluate(point, dispatch=True)

        return send

    async def close(self) -> None:
        """Release only this review's listeners and browser handle."""
        self._invalidate()
        self._listeners.close()
        handle, self._handle = self._handle, None
        if handle is not None:
            with suppress(Error):
                await handle.dispose()
