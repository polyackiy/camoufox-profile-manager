"""Lightweight CI watchdog privacy, bounds and timer cancellation checks."""

import asyncio

import pytest

from tests.browser import watchdog

SECRET = "synthetic-sensitive-cookie-value"


@pytest.mark.parametrize("raw", [None, "", "bad", "0", "-1", "nan", "inf"])
def test_watchdog_disabled_without_valid_positive_opt_in(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("CPM_BROWSER_WATCHDOG_SECONDS", raising=False)
    else:
        monkeypatch.setenv("CPM_BROWSER_WATCHDOG_SECONDS", raw)
    assert watchdog.watchdog_seconds() is None


def test_watchdog_reads_opt_in_seconds(monkeypatch):
    monkeypatch.setenv("CPM_BROWSER_WATCHDOG_SECONDS", "180")
    assert watchdog.watchdog_seconds() == 180


@pytest.mark.asyncio
async def test_watchdog_reports_nested_await_locations_without_names_values_or_source(capsys):
    blocker = asyncio.Event()

    async def wait_for_browser(cookie_value):
        await blocker.wait()

    async def outer_close(cookie_value):
        await wait_for_browser(cookie_value)

    task = asyncio.create_task(outer_close(SECRET), name=SECRET)
    try:
        await asyncio.sleep(0)
        await watchdog.report_stall(0)
        output = capsys.readouterr().err
        assert "test_browser_watchdog.py:" in output
        assert "in outer_close" in output and "in wait_for_browser" in output
        assert SECRET not in output and "await blocker.wait()" not in output
        assert str(__file__) not in output
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_watchdog_task_count_is_bounded_and_timer_cancels_cleanly(capsys):
    blocker = asyncio.Event()
    tasks = [asyncio.create_task(blocker.wait(), name=SECRET) for _ in range(40)]
    timer = asyncio.create_task(watchdog.report_stall(3600))
    try:
        await asyncio.sleep(0)
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)
        assert timer.cancelled() and not capsys.readouterr().err
        await watchdog.report_stall(0)
        output = capsys.readouterr().err
        assert "further tasks omitted" in output
        assert output.count("  Task ") == watchdog.MAX_TASKS
        assert SECRET not in output and len(output) < 16_000
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
