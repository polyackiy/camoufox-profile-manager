"""Tests for the browser session registry (no real browser launched)."""

import pytest

from camoufox_pm.core.browser_session import BrowserSession, BrowserSessionManager


def test_register_and_list_active():
    mgr = BrowserSessionManager()
    mgr.active_sessions["p1"] = BrowserSession("p1", browser=None, process_id=None)
    active = mgr.list_active()
    assert active[0]["profile_id"] == "p1"


def test_is_running_guard():
    mgr = BrowserSessionManager()
    mgr.active_sessions["p1"] = BrowserSession("p1", browser=None, process_id=None)
    assert mgr.is_running("p1") is True
    assert mgr.is_running("p2") is False


@pytest.mark.asyncio
async def test_close_unknown_returns_false():
    mgr = BrowserSessionManager()
    assert await mgr.close("missing") is False


@pytest.mark.asyncio
async def test_close_removes_session():
    mgr = BrowserSessionManager()
    mgr.active_sessions["p1"] = BrowserSession("p1", browser=None, process_id=None)
    assert await mgr.close("p1") is True
    assert mgr.is_running("p1") is False
