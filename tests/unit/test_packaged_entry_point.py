"""A frozen multiprocessing helper must not start another desktop window."""

import builtins
import multiprocessing
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ENTRY_POINT = Path(__file__).resolve().parents[2] / "packaging" / "launch.py"


def test_normal_entry_dispatches_freeze_support_before_desktop(monkeypatch):
    events = []
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: events.append("dispatch"))
    monkeypatch.setitem(
        sys.modules,
        "camoufox_pm.desktop",
        SimpleNamespace(
            main=lambda: events.append("desktop"),
            finish_packaged_desktop=lambda: events.append("finish"),
        ),
    )
    runpy.run_path(str(ENTRY_POINT), run_name="__main__")
    assert events == ["dispatch", "desktop", "finish"]


def test_failed_desktop_never_takes_successful_process_exit(monkeypatch):
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: None)
    finished = []

    def fail():
        raise SystemExit(1)

    monkeypatch.setitem(
        sys.modules,
        "camoufox_pm.desktop",
        SimpleNamespace(main=fail, finish_packaged_desktop=lambda: finished.append(True)),
    )
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ENTRY_POINT), run_name="__main__")
    assert result.value.code == 1
    assert finished == []


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "camoufox-pm",
            "-B",
            "-S",
            "-I",
            "-c",
            "from multiprocessing.resource_tracker import main;main(22)",
        ],
        ["camoufox-pm", "--multiprocessing-fork", "tracker_fd=6", "pipe_handle=8"],
    ],
)
def test_helper_dispatch_exits_without_importing_desktop(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", arguments)
    imports = []
    real_import = builtins.__import__

    def record_import(name, *args, **kwargs):
        if name == "camoufox_pm.desktop":
            imports.append(name)
        return real_import(name, *args, **kwargs)

    def helper_dispatch():
        # PyInstaller's runtime hook handles these arguments and exits the
        # helper when its protocol ends. The app must never continue past it.
        assert sys.argv == arguments
        raise SystemExit(0)

    monkeypatch.setattr(builtins, "__import__", record_import)
    monkeypatch.setattr(multiprocessing, "freeze_support", helper_dispatch)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ENTRY_POINT), run_name="__main__")
    assert result.value.code == 0
    assert imports == []
