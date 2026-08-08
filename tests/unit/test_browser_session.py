"""Tests for the browser session registry (no real browser launched)."""

import asyncio

import psutil
import pytest

from camoufox_pm.core import browser_session as bs
from camoufox_pm.core.browser_session import (
    BrowserLaunchError,
    BrowserSession,
    BrowserSessionManager,
    _resolve_process_id,
)


class FakeBrowser:
    """Stands in for the Playwright browser/context returned by a launch.

    Records the handlers registered on it so that a user closing the window can
    be simulated, which is the primary teardown signal in production.
    """

    def __init__(self, pid: int | None = None):
        self.handlers: dict[str, list] = {}
        if pid is not None:
            self._browser_process = type("Proc", (), {"pid": pid})()

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def fire(self, event: str) -> None:
        for handler in self.handlers.get(event, []):
            handler()


class FakeCamoufox:
    """Stands in for AsyncCamoufox, counting how often it was started and closed."""

    def __init__(self, browser: FakeBrowser | None = None, fail: Exception | None = None):
        self.browser = browser if browser is not None else FakeBrowser()
        self.fail = fail
        self.starts = 0
        self.exits = 0
        self.exit_error: Exception | None = None

    async def start(self):
        self.starts += 1
        if self.fail is not None:
            raise self.fail
        return self.browser

    async def __aexit__(self, *_exc):
        self.exits += 1
        if self.exit_error is not None:
            raise self.exit_error


@pytest.fixture
def installed(monkeypatch):
    """Pretend Camoufox is installed and hand every launch the given stand-in."""

    def install(camoufox: FakeCamoufox) -> FakeCamoufox:
        monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", True)
        monkeypatch.setattr(bs, "AsyncCamoufox", lambda **_options: camoufox)
        return camoufox

    return install


async def settle(manager: BrowserSessionManager) -> None:
    """Await the teardown tasks a close event scheduled.

    The manager keeps strong references to them, so this is deterministic: no
    sleeping and no polling.
    """
    while manager._exit_tasks:
        await asyncio.gather(*list(manager._exit_tasks))


def test_register_and_list_active():
    mgr = BrowserSessionManager()
    mgr.active_sessions["p1"] = BrowserSession("p1", camoufox=None, process_id=None)
    active = mgr.list_active()
    assert active[0]["profile_id"] == "p1"


def test_is_running_guard():
    mgr = BrowserSessionManager()
    mgr.active_sessions["p1"] = BrowserSession("p1", camoufox=None, process_id=None)
    assert mgr.is_running("p1") is True
    assert mgr.is_running("p2") is False


@pytest.mark.asyncio
async def test_close_unknown_returns_false():
    mgr = BrowserSessionManager()
    assert await mgr.close("missing") is False


@pytest.mark.asyncio
async def test_close_removes_session():
    mgr = BrowserSessionManager()
    mgr.active_sessions["p1"] = BrowserSession("p1", camoufox=None, process_id=None)
    assert await mgr.close("p1") is True
    assert mgr.is_running("p1") is False


@pytest.mark.asyncio
async def test_terminate_is_idempotent():
    session = BrowserSession("p1", camoufox=None, process_id=None)
    await session.terminate()
    assert session._terminated is True
    # A second call (e.g. close event racing the fallback monitor) must not raise.
    await session.terminate()


@pytest.mark.asyncio
async def test_handle_exit_notifies_once_and_prunes():
    """A user-closed browser routes through _handle_exit exactly once."""
    mgr = BrowserSessionManager()
    calls = []
    session = BrowserSession("p1", camoufox=None, process_id=None)

    async def on_exit(profile_id):
        calls.append(profile_id)

    session.on_exit = on_exit
    mgr.active_sessions["p1"] = session

    await mgr._handle_exit("p1")
    assert calls == ["p1"]
    assert mgr.is_running("p1") is False

    # Fallback monitor firing after the close event must be a no-op.
    await mgr._handle_exit("p1")
    assert calls == ["p1"]


# -- Launching ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_launching_a_profile_twice_reuses_the_running_browser(installed):
    """Two windows on one profile would have them writing the same databases."""
    camoufox = installed(FakeCamoufox())
    mgr = BrowserSessionManager()

    first = await mgr.launch("p1", {})
    second = await mgr.launch("p1", {})

    assert second is first
    assert camoufox.starts == 1


@pytest.mark.asyncio
async def test_a_failed_launch_leaves_nothing_running(installed):
    """A half-registered session would make the profile unlaunchable forever:
    every later attempt would report it as already running."""
    installed(FakeCamoufox(fail=RuntimeError("no display")))
    mgr = BrowserSessionManager()

    with pytest.raises(BrowserLaunchError, match="no display"):
        await mgr.launch("p1", {})

    assert mgr.is_running("p1") is False
    assert mgr.list_active() == []


@pytest.mark.asyncio
async def test_without_camoufox_installed_the_error_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", False)
    mgr = BrowserSessionManager()

    with pytest.raises(BrowserLaunchError, match="pip install"):
        await mgr.launch("p1", {})


@pytest.mark.asyncio
async def test_no_watchdog_is_started_when_no_process_id_could_be_resolved(installed):
    """The watchdog tears the session down as soon as its pid stops existing, so
    starting one for a pid we never resolved would close the browser at once."""
    installed(FakeCamoufox(FakeBrowser(pid=None)))
    mgr = BrowserSessionManager()

    session = await mgr.launch("p1", {})

    assert session.process_id is None
    assert session.monitor_task is None


# -- The user closes the window ------------------------------------------------


@pytest.mark.asyncio
async def test_closing_the_window_tears_the_session_down(installed):
    camoufox = installed(FakeCamoufox())
    mgr = BrowserSessionManager()
    closed = []

    async def on_exit(profile_id):
        closed.append(profile_id)

    await mgr.launch("p1", {}, on_exit=on_exit)
    camoufox.browser.fire("close")
    await settle(mgr)

    assert closed == ["p1"]
    assert mgr.is_running("p1") is False
    assert camoufox.exits == 1


@pytest.mark.asyncio
async def test_close_and_disconnected_together_tear_down_only_once(installed):
    """Playwright fires both for one window; the profile must not be logged as
    closed twice, nor the context closed twice."""
    camoufox = installed(FakeCamoufox())
    mgr = BrowserSessionManager()
    closed = []

    async def on_exit(profile_id):
        closed.append(profile_id)

    await mgr.launch("p1", {}, on_exit=on_exit)
    camoufox.browser.fire("close")
    camoufox.browser.fire("disconnected")
    await settle(mgr)

    assert closed == ["p1"]
    assert camoufox.exits == 1


@pytest.mark.asyncio
async def test_a_session_whose_process_vanished_is_still_listed(monkeypatch):
    """Listing used to prune those sessions, which skipped terminate() and the
    exit handler and so leaked the Camoufox context. Listing is a pure read."""
    monkeypatch.setattr(psutil, "pid_exists", lambda _pid: False)
    mgr = BrowserSessionManager()
    mgr.active_sessions["p1"] = BrowserSession("p1", camoufox=None, process_id=4242)

    assert [s["profile_id"] for s in mgr.list_active()] == ["p1"]
    assert mgr.is_running("p1") is True


@pytest.mark.asyncio
async def test_the_watchdog_tears_down_when_the_process_disappears(monkeypatch):
    monkeypatch.setattr(psutil, "pid_exists", lambda _pid: False)
    mgr = BrowserSessionManager()
    camoufox = FakeCamoufox()
    session = BrowserSession("p1", camoufox=camoufox, process_id=4242)
    closed = []

    async def on_exit(profile_id):
        closed.append(profile_id)

    session.on_exit = on_exit
    mgr.active_sessions["p1"] = session

    await mgr._monitor("p1", 4242)

    assert closed == ["p1"]
    assert mgr.is_running("p1") is False
    assert camoufox.exits == 1


@pytest.mark.asyncio
async def test_terminating_stops_the_watchdog():
    """A watchdog outliving its session would tear down whatever session took its
    place under the same profile id."""
    session = BrowserSession("p1", camoufox=None, process_id=4242)
    session.monitor_task = asyncio.create_task(asyncio.sleep(3600))

    await session.terminate()

    assert session.monitor_task.cancelled()


# -- Teardown that goes wrong ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_browser_that_fails_to_close_is_still_forgotten(installed):
    """A crashed browser raises on close. Keeping the session would leave the
    profile permanently 'running' and impossible to launch again."""
    camoufox = installed(FakeCamoufox())
    camoufox.exit_error = RuntimeError("connection already closed")
    mgr = BrowserSessionManager()

    await mgr.launch("p1", {})

    assert await mgr.close("p1") is True
    assert mgr.is_running("p1") is False


@pytest.mark.asyncio
async def test_an_exit_handler_that_raises_does_not_break_teardown(installed):
    camoufox = installed(FakeCamoufox())
    mgr = BrowserSessionManager()

    async def on_exit(_profile_id):
        raise RuntimeError("database gone")

    await mgr.launch("p1", {}, on_exit=on_exit)
    camoufox.browser.fire("close")
    await settle(mgr)

    assert mgr.is_running("p1") is False
    assert camoufox.exits == 1


@pytest.mark.asyncio
async def test_a_driver_that_survived_the_close_is_killed(monkeypatch):
    """The graceful close is the primary path; the pid is the backstop, and a
    surviving driver process holds the profile directory open."""
    actions = []

    class FakeProcess:
        def __init__(self, pid):
            actions.append(("open", pid))

        def terminate(self):
            actions.append(("terminate", None))

        def wait(self, timeout=None):
            actions.append(("wait", timeout))

    monkeypatch.setattr(bs.psutil, "Process", FakeProcess)
    session = BrowserSession("p1", camoufox=FakeCamoufox(), process_id=4242)

    await session.terminate()

    assert actions == [("open", 4242), ("terminate", None), ("wait", 5)]


@pytest.mark.asyncio
async def test_a_driver_that_ignores_terminate_is_killed(monkeypatch):
    killed = []

    class StubbornProcess:
        def __init__(self, pid):
            pass

        def terminate(self):
            pass

        def wait(self, timeout=None):
            raise psutil.TimeoutExpired(timeout)

        def kill(self):
            killed.append(True)

    monkeypatch.setattr(bs.psutil, "Process", StubbornProcess)
    session = BrowserSession("p1", camoufox=FakeCamoufox(), process_id=4242)

    await session.terminate()

    assert killed == [True]


@pytest.mark.asyncio
async def test_a_process_that_is_already_gone_is_not_an_error(monkeypatch):
    def gone(_pid):
        raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(bs.psutil, "Process", gone)
    session = BrowserSession("p1", camoufox=FakeCamoufox(), process_id=4242)

    await session.terminate()  # must not raise


@pytest.mark.asyncio
async def test_close_all_closes_every_browser(installed):
    installed(FakeCamoufox())
    mgr = BrowserSessionManager()
    for profile_id in ("p1", "p2", "p3"):
        mgr.active_sessions[profile_id] = BrowserSession(profile_id, camoufox=FakeCamoufox())

    assert await mgr.close_all() == 3
    assert mgr.list_active() == []


# -- Resolving a process id -----------------------------------------------------


def test_a_process_id_is_never_fabricated():
    """Returning a placeholder pid would make the watchdog kill an unrelated
    process, and would start a watchdog that immediately tears the session down."""

    class Opaque:
        pass

    assert _resolve_process_id(Opaque()) is None


def test_a_process_id_is_found_through_the_nested_transport():
    """Playwright does not expose the pid, so it is read from internals that move
    between versions; each known shape must keep working."""

    class Nested:
        pass

    obj = Nested()
    obj.browser = Nested()
    obj.browser._impl = Nested()
    obj.browser._impl._connection = Nested()
    obj.browser._impl._connection._transport = Nested()
    obj.browser._impl._connection._transport._proc = Nested()
    obj.browser._impl._connection._transport._proc.pid = 777

    assert _resolve_process_id(obj) == 777
