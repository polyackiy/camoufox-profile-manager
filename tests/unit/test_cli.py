"""The console script: what the flags do before the server starts.

Nothing here binds a port or opens a window — uvicorn, the browser timer and
desktop mode are all stood in for. What is checked is the part with its own
logic: the flags becoming the settings the rest of the app reads, and the URL a
browser is pointed at.
"""

import asyncio
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


# --- `camoufox-pm user ...` ---------------------------------------------------


@pytest.fixture
def run_user(monkeypatch, tmp_path):
    """Run a ``user`` subcommand against a throwaway database, with password
    prompts answered from a scripted list."""

    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("must not serve"))

    def invoke(*args: str, prompts: list[str] | None = None):
        answers = list(prompts or [])
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": answers.pop(0))
        monkeypatch.setattr(sys, "argv", ["camoufox-pm", "user", *args])
        get_settings.cache_clear()
        try:
            cli.main()
        finally:
            get_settings.cache_clear()

    yield invoke


async def _usernames(tmp_path) -> list[str]:
    from camoufox_pm.core.database import StorageManager

    storage = StorageManager(str(tmp_path / "cli.db"))
    await storage.initialize()
    try:
        return [user["username"] for user in await storage.list_users()]
    finally:
        await storage.close()


def test_user_add_creates_the_account(run_user, tmp_path):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])

    assert asyncio.run(_usernames(tmp_path)) == ["alice"]


def test_user_add_rejects_a_short_password(run_user, tmp_path):
    with pytest.raises(SystemExit):
        run_user("add", "alice", prompts=["short", "short"])

    assert asyncio.run(_usernames(tmp_path)) == []


def test_user_add_rejects_mismatched_prompts(run_user, tmp_path):
    with pytest.raises(SystemExit):
        run_user("add", "alice", prompts=["one-password-8", "another-password"])


def test_user_add_refuses_a_duplicate(run_user):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    with pytest.raises(SystemExit):
        run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])


def test_user_remove_deletes_the_account(run_user, tmp_path):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    run_user("remove", "alice")

    assert asyncio.run(_usernames(tmp_path)) == []


def test_user_list_shows_names_and_never_hashes(run_user, capsys):
    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    run_user("list")

    out = capsys.readouterr().out
    assert "alice" in out
    assert "argon2" not in out
    assert "hunter22-long" not in out


def test_user_passwd_changes_the_password(run_user, tmp_path):
    from camoufox_pm.core import auth
    from camoufox_pm.core.database import StorageManager

    run_user("add", "alice", prompts=["hunter22-long", "hunter22-long"])
    run_user("passwd", "alice", prompts=["new-password-9", "new-password-9"])

    async def check() -> bool:
        storage = StorageManager(str(tmp_path / "cli.db"))
        await storage.initialize()
        try:
            user = await storage.get_user_by_username("alice")
            assert user is not None
            return auth.verify_password(user["password_hash"], "new-password-9")
        finally:
            await storage.close()

    assert asyncio.run(check())
