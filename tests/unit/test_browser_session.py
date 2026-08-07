"""Tests for the browser session registry (no real browser launched)."""

import pytest

from camoufox_pm.core.browser_session import BrowserSession, BrowserSessionManager


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
