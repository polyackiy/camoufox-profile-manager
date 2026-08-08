"""The console script: what the flags do before the server starts.

Nothing here binds a port or opens a window — uvicorn, the browser timer and
desktop mode are all stood in for. What is checked is the part with its own
logic: the flags becoming the settings the rest of the app reads, and the URL a
browser is pointed at.
"""

import sys

import pytest

from camoufox_pm import cli
from camoufox_pm.config import get_settings


@pytest.fixture
def run(monkeypatch):
    """Run ``main()`` with the given arguments, capturing what it would start."""
    started: dict = {"uvicorn": None, "timers": [], "opened": [], "desktop": None}

    class FakeTimer:
        def __init__(self, delay, function):
            started["timers"].append((delay, function))

        def start(self):
            """Deliberately does nothing: the callback is invoked by the test."""

    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: started.update(uvicorn=kw))
    monkeypatch.setattr(cli.threading, "Timer", FakeTimer)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: started["opened"].append(url))

    # main() writes these itself; setting them through monkeypatch first means
    # the originals are restored however the run ends.
    monkeypatch.setenv("CPM_HOST", "127.0.0.1")
    monkeypatch.setenv("CPM_PORT", "8000")

    def invoke(*args: str):
        monkeypatch.setattr(sys, "argv", ["camoufox-pm", *args])
        get_settings.cache_clear()
        cli.main()
        return started

    yield invoke
    get_settings.cache_clear()


def test_the_flags_become_the_settings_the_rest_of_the_app_reads(run):
    """The Settings screen, CORS and the logs all read get_settings(). Leaving it
    on the defaults would have them report an address nothing is listening on."""
    started = run("--host", "0.0.0.0", "--port", "9123", "--no-browser")

    assert get_settings().host == "0.0.0.0"
    assert get_settings().port == 9123
    assert started["uvicorn"]["host"] == "0.0.0.0"
    assert started["uvicorn"]["port"] == 9123


def test_no_browser_opens_nothing(run):
    started = run("--no-browser")

    assert started["timers"] == []


def test_a_wildcard_bind_is_opened_as_localhost(run):
    """A browser cannot fetch http://0.0.0.0/ — the tab just fails."""
    started = run("--host", "0.0.0.0", "--port", "9123")

    assert len(started["timers"]) == 1
    _delay, open_the_browser = started["timers"][0]
    open_the_browser()

    assert started["opened"] == ["http://localhost:9123/"]


def test_a_real_address_is_opened_as_itself(run):
    started = run("--host", "192.168.1.5", "--port", "9123")

    started["timers"][0][1]()

    assert started["opened"] == ["http://192.168.1.5:9123/"]


def test_desktop_mode_hands_over_instead_of_serving_here(run, monkeypatch):
    """run_desktop starts its own server; starting a second one would take the port."""
    from camoufox_pm import desktop

    asked: list[dict] = []
    monkeypatch.setattr(desktop, "run_desktop", lambda **kw: asked.append(kw))

    started = run("--desktop", "--port", "9123")

    assert asked == [{"host": "127.0.0.1", "port": 9123}]
    assert started["uvicorn"] is None
    assert started["timers"] == []
