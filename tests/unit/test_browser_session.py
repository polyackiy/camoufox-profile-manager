"""Tests for the browser session registry (no real browser launched)."""

import asyncio
import os
from types import SimpleNamespace

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
        self.exit_error: BaseException | None = None

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
    session = BrowserSession("p1", camoufox=None, process_id=None)
    session.monitor_task = asyncio.create_task(asyncio.sleep(3600))

    await session.terminate()

    assert session.monitor_task.cancelled()


# -- Teardown that goes wrong ---------------------------------------------------


@pytest.mark.asyncio
async def test_failed_sdk_close_is_forgotten_after_owned_driver_exit_is_confirmed(
    installed, monkeypatch
):
    """A verified dead owned driver permits recovery despite a stale SDK error."""
    camoufox = installed(FakeCamoufox(FakeBrowser(pid=4242)))
    monkeypatch.setattr(
        bs.psutil,
        "Process",
        lambda pid: SimpleNamespace(
            pid=pid,
            parents=lambda: [SimpleNamespace(pid=os.getpid())],
            children=lambda recursive: [],
            is_running=lambda: camoufox.exits == 0,
            terminate=lambda: None,
        ),
    )
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
            self.pid = pid
            self.alive = True
            actions.append(("open", pid))

        def parents(self):
            return [SimpleNamespace(pid=os.getpid())]

        def children(self, recursive=False):
            return []

        def terminate(self):
            actions.append(("terminate", None))

        def is_running(self):
            return self.alive

        def wait(self, timeout=None):
            actions.append(("wait", timeout))
            self.alive = False
            return 0

    monkeypatch.setattr(bs.psutil, "Process", FakeProcess)
    session = BrowserSession("p1", camoufox=FakeCamoufox(), process_id=4242)

    await session.terminate()

    assert actions[0] == ("open", 4242)
    assert actions[1] == ("terminate", None)
    assert [item for item in actions if item[0] == "wait"]


@pytest.mark.asyncio
async def test_a_driver_that_ignores_terminate_is_killed(monkeypatch):
    killed = []

    class StubbornProcess:
        def __init__(self, pid):
            self.pid = pid

        def parents(self):
            return [SimpleNamespace(pid=os.getpid())]

        def children(self, recursive=False):
            return []

        def terminate(self):
            pass

        def wait(self, timeout=None):
            if not killed:
                raise psutil.TimeoutExpired(timeout)
            return 0

        def is_running(self):
            return not killed

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


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["close", "natural", "failure", "cancel"])
@pytest.mark.parametrize("protocol", ["socks4", "socks5"])
async def test_socks_relay_follows_session_lifetime(monkeypatch, ending, protocol):
    from camoufox_pm.core.socks_relay import SocksRelay

    relays = []
    options_seen = []
    started = asyncio.Event()
    fake = FakeCamoufox()

    class RecordingRelay(SocksRelay):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            relays.append(self)

    async def start():
        started.set()
        if ending == "failure":
            raise RuntimeError("launch failed")
        if ending == "cancel":
            await asyncio.Future()
        return fake.browser

    fake.start = start

    def launch(**options):
        options_seen.append(options)
        return fake

    monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", True)
    monkeypatch.setattr(bs, "AsyncCamoufox", launch)
    monkeypatch.setattr(bs, "SocksRelay", RecordingRelay)
    manager = BrowserSessionManager()
    original = {
        "proxy": {
            "server": f"{protocol}://127.0.0.1:1",
            **({"username": "user", "password": "secret"} if protocol == "socks5" else {}),
        }
    }
    task = asyncio.create_task(manager.launch("p", original))
    await started.wait()
    if ending == "failure":
        with pytest.raises(BrowserLaunchError):
            await task
    elif ending == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        session = await task
        assert session.socks_relay is relays[0]
        if ending == "natural":
            fake.browser.fire("close")
            await settle(manager)
        else:
            assert await manager.close("p")
    assert set(options_seen[0]["proxy"]) == {"server"}
    assert options_seen[0]["firefox_user_prefs"]["network.proxy.socks_remote_dns"] is True
    if protocol == "socks5":
        assert original["proxy"]["password"] == "secret"
    assert not manager.is_live("p")
    assert relays[0]._closed and not relays[0]._tasks
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", relays[0].port)


@pytest.mark.asyncio
async def test_cancelled_close_drains_browser_relay_and_lease_before_untracking():
    from unittest.mock import AsyncMock

    entered = asyncio.Event()
    finish_relay = asyncio.Event()

    class PendingRelay:
        async def close(self):
            entered.set()
            await finish_relay.wait()

    storage = type("Storage", (), {"release_lease": AsyncMock()})()
    manager = BrowserSessionManager(storage=storage, holder="holder")
    camoufox = FakeCamoufox()
    session = BrowserSession("p", camoufox, socks_relay=PendingRelay())
    manager.active_sessions["p"] = session
    close = asyncio.create_task(manager.close_and_release("p"))
    await entered.wait()
    close.cancel()
    await asyncio.sleep(0)
    assert manager.is_live("p")
    assert not close.done() and not session._terminated
    assert camoufox.exits == 0
    storage.release_lease.assert_not_called()
    # A second cancellation must not cancel the owned teardown either.
    close.cancel()
    await asyncio.sleep(0)
    assert manager.is_live("p")
    finish_relay.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert camoufox.exits == 1 and session._terminated
    assert not manager.is_live("p")
    storage.release_lease.assert_awaited_once_with("p", "holder")
    await session.terminate()
    assert camoufox.exits == 1


@pytest.mark.asyncio
async def test_cancel_during_failed_launch_cleanup_keeps_starting_until_drained(monkeypatch):
    entered = asyncio.Event()
    finish_relay = asyncio.Event()

    class PendingRelay:
        def __init__(self, *args):
            pass

        async def start(self):
            pass

        @property
        def browser_proxy(self):
            return {"server": "socks5://127.0.0.1:1"}

        async def close(self):
            entered.set()
            await finish_relay.wait()

    camoufox = FakeCamoufox(fail=RuntimeError("failed"))
    monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", True)
    monkeypatch.setattr(bs, "SocksRelay", PendingRelay)
    monkeypatch.setattr(bs, "AsyncCamoufox", lambda **kwargs: camoufox)
    manager = BrowserSessionManager()
    launch = asyncio.create_task(
        manager.launch(
            "p", {"proxy": {"server": "socks5://provider:1080", "username": "u", "password": "p"}}
        )
    )
    await entered.wait()
    launch.cancel()
    await asyncio.sleep(0)
    assert manager.is_live("p") and not launch.done()
    assert camoufox.exits == 0
    finish_relay.set()
    with pytest.raises(asyncio.CancelledError):
        await launch
    assert camoufox.exits == 1 and not manager.is_live("p")


@pytest.mark.asyncio
async def test_monitor_can_initiate_shared_cleanup_without_deadlock(monkeypatch):
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    manager = BrowserSessionManager()
    camoufox = FakeCamoufox()
    session = BrowserSession("p", camoufox)
    manager.active_sessions["p"] = session
    session.monitor_task = asyncio.create_task(manager._monitor("p", 123))
    await asyncio.wait_for(asyncio.gather(session.monitor_task, return_exceptions=True), 1)
    assert camoufox.exits == 1 and not manager.is_live("p")


async def test_stopped_intent_is_durable_before_lease_release(installed):
    from unittest.mock import AsyncMock

    installed(FakeCamoufox())
    events = []

    async def release(*_):
        events.append("release")

    storage = type("Storage", (), {"release_lease": AsyncMock(side_effect=release)})()
    manager = BrowserSessionManager(storage=storage, holder="holder")

    async def stopped(profile_id):
        assert profile_id == "p"
        events.append("persisted")

    manager.on_stopped = stopped
    await manager.launch("p", {})
    assert await manager.close_and_release("p")
    assert events == ["persisted", "release"]


async def test_failed_stopped_intent_retains_local_lease_for_retry(installed):
    from unittest.mock import AsyncMock

    installed(FakeCamoufox())
    storage = type("Storage", (), {"release_lease": AsyncMock()})()
    manager = BrowserSessionManager(storage=storage, holder="holder")

    async def stopped(_):
        raise OSError("disk full")

    manager.on_stopped = stopped
    await manager.launch("p", {})
    with pytest.raises(OSError):
        await manager.close_and_release("p")
    storage.release_lease.assert_not_called()
    assert "p" in manager.active_sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("late_descendant", [False, True])
async def test_close_drains_owned_browser_child_after_driver_disappears(tmp_path, late_descendant):
    """A close acknowledgement must follow the owned child's native profile lock."""
    import subprocess
    import sys

    from camoufox_pm.sync.quiescence import browser_profile_lock

    profile = tmp_path / "profile"
    profile.mkdir()
    child_code = (
        "import os,sys,time; from pathlib import Path; "
        "from camoufox_pm.sync.quiescence import browser_profile_lock; "
        "guard=browser_profile_lock(Path(sys.argv[1])); guard.__enter__(); "
        "print(os.getpid(),flush=True); time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],sys.argv[2]]); time.sleep(60)"
    )
    if late_descendant:
        broker_code = (
            "import subprocess,sys,time,os; print(os.getpid(),flush=True); "
            "sys.stdin.readline(); "
            "subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],sys.argv[2]]); time.sleep(60)"
        )
        parent_code = (
            "import subprocess,sys,time; "
            "broker=subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],sys.argv[2],sys.argv[3]],"
            "stdin=subprocess.PIPE,text=True); "
            "sys.stdin.readline(); broker.stdin.write('go\\n'); broker.stdin.flush(); time.sleep(60)"
        )
        arguments = [broker_code, child_code, str(profile)]
    else:
        arguments = [child_code, str(profile)]
    driver = subprocess.Popen(
        [sys.executable, "-u", "-c", parent_code, *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    children = []
    try:
        assert driver.stdout is not None
        line = await asyncio.wait_for(asyncio.to_thread(driver.stdout.readline), 20)
        children.append(psutil.Process(int(line)))

        class DriverExit:
            async def __aexit__(self, *args):
                if late_descendant:
                    # This child is born after the manager's last pre-exit snapshot.
                    # Its known parent survives the SDK driver long enough to inspect.
                    assert driver.stdin is not None
                    driver.stdin.write("go\n")
                    driver.stdin.flush()
                    line = await asyncio.wait_for(asyncio.to_thread(driver.stdout.readline), 20)
                    children.append(psutil.Process(int(line)))
                driver.terminate()
                await asyncio.to_thread(driver.wait, 5)

        session = BrowserSession("test", DriverExit(), driver.pid)
        await session.terminate()
        with browser_profile_lock(profile):
            pass
        assert session._terminated
    finally:
        for child in reversed(children):
            try:
                child.kill()
                await asyncio.to_thread(child.wait, 5)
            except psutil.NoSuchProcess:
                pass
        if driver.poll() is None:
            driver.kill()
            await asyncio.to_thread(driver.wait, 5)
        if driver.stdout is not None:
            driver.stdout.close()
        if driver.stdin is not None:
            driver.stdin.close()


def test_teardown_never_adopts_unrelated_or_manager_pid(monkeypatch):
    calls = []

    class Unrelated:
        def __init__(self, pid):
            self.pid = pid

        def parents(self):
            return []

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(bs.psutil, "Process", Unrelated)
    for pid in (4242, os.getpid()):
        tree = bs._OwnedProcessTree(pid)
        with pytest.raises(bs.BrowserTerminationError):
            tree.finish()
    assert calls == []


def test_refresh_never_walks_a_recycled_pinned_descendant(monkeypatch):
    class RecycledChild:
        def is_running(self):
            return False

        def children(self, **kwargs):
            pytest.fail("A recycled process must never authorize descendant adoption")

    child = RecycledChild()
    root = SimpleNamespace(
        pid=4242,
        parents=lambda: [SimpleNamespace(pid=os.getpid())],
        is_running=lambda: True,
        children=lambda **kwargs: [child],
    )
    lookups = []

    def lookup(pid):
        lookups.append(pid)
        return root

    monkeypatch.setattr(bs.psutil, "Process", lookup)
    tree = bs._OwnedProcessTree(4242)
    tree.refresh()
    assert lookups == [4242] and tree.children == {child}


@pytest.mark.asyncio
async def test_unconfirmed_exit_retains_lease_and_explicit_close_can_retry(monkeypatch):
    from unittest.mock import AsyncMock

    storage = SimpleNamespace(release_lease=AsyncMock())
    manager = BrowserSessionManager(storage=storage, holder="local")
    session = BrowserSession("p", None)
    stopped = AsyncMock()
    session.on_stopped = stopped
    manager.active_sessions["p"] = session

    def failed():
        raise bs.BrowserTerminationError("Exit could not be confirmed")

    monkeypatch.setattr(session._owned_processes, "finish", failed)
    with pytest.raises(bs.BrowserTerminationError):
        await manager.close_and_release("p")
    assert manager.is_live("p") and not session._terminated
    storage.release_lease.assert_not_awaited()
    stopped.assert_not_awaited()
    monkeypatch.setattr(session._owned_processes, "finish", lambda: None)
    assert await manager.close_and_release("p")
    assert not manager.is_live("p")
    storage.release_lease.assert_awaited_once_with("p", "local")
    stopped.assert_awaited_once_with("p")


@pytest.mark.asyncio
async def test_close_all_attempts_remaining_profiles_after_unconfirmed_exit(monkeypatch):
    manager = BrowserSessionManager()
    first, second = BrowserSession("first", None), BrowserSession("second", None)
    manager.active_sessions.update(first=first, second=second)

    def failed():
        raise bs.BrowserTerminationError("Exit could not be confirmed")

    monkeypatch.setattr(first._owned_processes, "finish", failed)
    with pytest.raises(bs.BrowserTerminationError):
        await manager.close_all()
    assert manager.is_live("first") and not manager.is_live("second")


@pytest.mark.asyncio
async def test_failed_start_with_unconfirmed_child_keeps_live_fence(installed, monkeypatch):
    installed(FakeCamoufox(fail=RuntimeError("SDK start failed")))
    manager = BrowserSessionManager()
    original = bs._OwnedProcessTree.finish

    def unconfirmed(self):
        raise bs.BrowserTerminationError("Exit could not be confirmed")

    monkeypatch.setattr(bs._OwnedProcessTree, "finish", unconfirmed)
    with pytest.raises(bs.BrowserTerminationError):
        await manager.launch("p", {})
    assert manager.is_live("p") and manager.is_running("p")
    assert "p" not in manager._starting
    monkeypatch.setattr(bs._OwnedProcessTree, "finish", original)
    assert await manager.close_and_release("p")
    assert not manager.is_live("p")


@pytest.mark.asyncio
async def test_cancelled_waiter_drains_owned_process_worker_before_releasing_lease(monkeypatch):
    import threading
    from unittest.mock import AsyncMock

    entered, finish = threading.Event(), threading.Event()
    storage = SimpleNamespace(release_lease=AsyncMock())
    manager = BrowserSessionManager(storage=storage, holder="owner")
    session = BrowserSession("p", None)
    manager.active_sessions["p"] = session

    def slow_worker():
        entered.set()
        assert finish.wait(10)

    monkeypatch.setattr(session._owned_processes, "finish", slow_worker)
    close = asyncio.create_task(manager.close_and_release("p"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        close.cancel()
        await asyncio.sleep(0)
        assert not close.done() and manager.is_live("p")
        storage.release_lease.assert_not_awaited()
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert not manager.is_live("p")
    storage.release_lease.assert_awaited_once_with("p", "owner")


@pytest.mark.asyncio
async def test_driver_identity_is_pinned_before_sdk_exit_and_never_relooked_up(monkeypatch):
    lookups = []

    class Original:
        pid = 4242

        def parents(self):
            return [SimpleNamespace(pid=os.getpid())]

        def children(self, recursive=False):
            return []

        def is_running(self):
            return False

        def terminate(self):
            raise psutil.NoSuchProcess(self.pid)

    original = Original()

    def lookup(pid):
        lookups.append(pid)
        if len(lookups) != 1:
            pytest.fail("A recycled numeric PID must never be looked up during teardown")
        return original

    monkeypatch.setattr(bs.psutil, "Process", lookup)
    session = BrowserSession("p", FakeCamoufox(), 4242)
    await session.terminate()
    assert lookups == [4242] and session._terminated


@pytest.mark.asyncio
async def test_current_playwright_sdk_driver_identity_is_resolved_without_a_browser():
    from playwright.async_api import async_playwright

    first_context, second_context = async_playwright(), async_playwright()
    async with first_context as first, second_context as second:
        first_pid, second_pid = _resolve_process_id(first), _resolve_process_id(second)
        assert first_pid is not None and second_pid is not None
        assert first_pid != second_pid
        assert _resolve_process_id(first_context) == first_pid
        assert _resolve_process_id(SimpleNamespace(browser=first)) == first_pid
        assert any(parent.pid == os.getpid() for parent in psutil.Process(first_pid).parents())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failed_sdk_close_without_driver_identity_retains_fence_until_retry(
    installed, failure
):
    from unittest.mock import AsyncMock

    camoufox = installed(FakeCamoufox())
    camoufox.exit_error = failure("private SDK diagnostic")
    storage = SimpleNamespace(release_lease=AsyncMock())
    manager = BrowserSessionManager(storage=storage, holder="local")
    stopped = AsyncMock()
    manager.on_stopped = stopped
    session = await manager.launch("p", {})
    assert session.process_id is None
    with pytest.raises(bs.BrowserTerminationError):
        await manager.close_and_release("p")
    assert manager.is_live("p") and not session._terminated
    storage.release_lease.assert_not_awaited()
    stopped.assert_not_awaited()
    camoufox.exit_error = None
    assert await manager.close_and_release("p")
    assert not manager.is_live("p")
    storage.release_lease.assert_awaited_once_with("p", "local")
    stopped.assert_awaited_once_with("p")
