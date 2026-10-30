"""A native-control SDK acknowledgement must not leave owned lock holders."""

import asyncio
import base64
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import psutil
import pytest
from scripts import prepare_ci_windows_renderer as preflight

from tests.browser import test_native_canvas_control as native
from tests.browser.support import LocalSites


@pytest.mark.asyncio
async def test_renderer_preflight_drains_owned_children_before_accepting(tmp_path, monkeypatch):
    from camoufox_pm.sync.quiescence import browser_profile_lock

    drivers, children, profiles = [], [], []
    child_code = (
        "import os,sys,time; from pathlib import Path; "
        "from camoufox_pm.sync.quiescence import browser_profile_lock; "
        "guard=browser_profile_lock(Path(sys.argv[1])); guard.__enter__(); "
        "print(os.getpid(),flush=True); time.sleep(60)"
    )
    driver_code = (
        "import subprocess,sys,time; "
        "profile=sys.stdin.readline().strip(); "
        "subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],profile]); time.sleep(60)"
    )

    class Page:
        async def goto(self, *_args, **_kwargs):
            pass

        async def evaluate(self, _script):
            return {"value": "data:image/png;base64," + base64.b64encode(b"fixed-canvas").decode()}

    class SDK:
        async def __aenter__(self):
            self.firefox = SimpleNamespace(launch_persistent_context=self.launch)
            self.driver = subprocess.Popen(
                [sys.executable, "-u", "-c", driver_code, child_code],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            drivers.append(self.driver)
            self._impl_obj = SimpleNamespace(
                _connection=SimpleNamespace(
                    _transport=SimpleNamespace(_proc=SimpleNamespace(pid=self.driver.pid))
                )
            )
            return self

        async def launch(self, user_data_dir, **_kwargs):
            from pathlib import Path

            profile = Path(user_data_dir)
            profile.mkdir()
            profiles.append(profile)
            self.driver.stdin.write(str(profile) + "\n")
            self.driver.stdin.flush()
            child = await asyncio.wait_for(asyncio.to_thread(self.driver.stdout.readline), 20)
            children.append(psutil.Process(int(child)))
            return self

        @property
        def browser(self):
            return SimpleNamespace(version="synthetic-native-control")

        async def new_page(self):
            return Page()

        async def close(self):
            self.driver.terminate()
            await asyncio.to_thread(self.driver.wait, 5)

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(native, "async_playwright", SDK)
    monkeypatch.setattr(
        preflight.tempfile, "TemporaryDirectory", lambda **_: nullcontext(str(tmp_path))
    )
    monkeypatch.setattr(
        preflight,
        "serve_local_sites",
        lambda: nullcontext(LocalSites("http://127.0.0.1/", "http://localhost/")),
    )
    try:
        await preflight.prepare_renderer(tmp_path / "output")
        for profile in profiles:
            with browser_profile_lock(profile):
                pass
    finally:
        for child in children:
            try:
                child.kill()
                await asyncio.to_thread(child.wait, 5)
            except psutil.NoSuchProcess:
                pass
        for driver in drivers:
            if driver.poll() is None:
                driver.kill()
                await asyncio.to_thread(driver.wait, 5)
            if driver.stdout is not None:
                driver.stdout.close()
            if driver.stdin is not None:
                driver.stdin.close()
