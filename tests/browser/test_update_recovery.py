"""Same-engine data recovery smoke, not a signed binary downgrade acceptance test.

Exercise real persisted Firefox sessions and pinned manager identity. The current
CI engine stays installed: platform package replacement and engine downgrade need
separate signed package-to-package acceptance trials.
"""

import asyncio
from copy import deepcopy

import pytest

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.update_checkpoint import create_checkpoint, restore_checkpoint
from tests.browser.support import GEOIP_ADDRESS

IDENTITY = """() => ({userAgent: navigator.userAgent, platform: navigator.platform,
    cores: navigator.hardwareConcurrency, screen: screen.width + 'x' + screen.height})"""


@pytest.mark.browser
@pytest.mark.asyncio
async def test_complete_profile_checkpoint_restores_real_session_and_identity(
    tmp_path, local_sites
):
    async def exercise():
        data = tmp_path / "data"
        data.mkdir()
        database = data / "profiles.db"
        gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
        checkpoint = tmp_path / "checkpoint"
        paths = {"profiles": data / "profiles"}
        manager = None
        storage = None

        async def open_manager():
            nonlocal manager, storage
            storage = StorageManager(str(database))
            await storage.initialize()
            manager = ProfileManager(storage, str(data))
            await manager.initialize()

        async def close_manager():
            nonlocal manager, storage
            if manager is not None:
                await manager.browser_sessions.close_all()
                await manager.browser_sessions.stop_heartbeat()
                await manager.release_all_leases()
                manager = None
            if storage is not None:
                await storage.close()
                storage = None

        def offline():
            return gate.blocked and manager is None and storage is None

        async def page_for(profile_id):
            await manager.launch_browser(profile_id, headless=True, geoip=GEOIP_ADDRESS)
            context = manager.browser_sessions.active_sessions[profile_id].camoufox.browser
            page = await context.new_page()
            await page.goto(local_sites.first, wait_until="domcontentloaded", timeout=15000)
            return page

        try:
            await open_manager()
            profile = await manager.create_profile(
                "Recovery browser proof", generate_fingerprint=False
            )
            await manager.update_profile(profile.id, {"tags": ["before-checkpoint"]})
            page = await page_for(profile.id)
            await page.evaluate("""() => {
                document.cookie = 'recovery=original; Max-Age=3600; Path=/; SameSite=Lax';
                localStorage.setItem('recovery', 'original');
            }""")
            original_identity = await page.evaluate(IDENTITY)
            pinned = deepcopy((await manager.get_profile(profile.id)).fingerprint)
            assert pinned, "First real launch must persist the pinned identity"
            await close_manager()

            gate.acquire("browser-checkpoint")
            create_checkpoint(
                checkpoint,
                db_path=database,
                paths=paths,
                required_paths=("profiles",),
                maintenance_check=offline,
            )
            gate.release("browser-checkpoint")

            await open_manager()
            page = await page_for(profile.id)
            assert "recovery=original" in await page.evaluate("document.cookie")
            assert await page.evaluate("localStorage.getItem('recovery')") == "original"
            await page.evaluate("""() => {
                document.cookie = 'recovery=newer; Max-Age=3600; Path=/; SameSite=Lax';
                localStorage.setItem('recovery', 'newer');
            }""")
            assert "recovery=newer" in await page.evaluate("document.cookie")
            assert await page.evaluate("localStorage.getItem('recovery')") == "newer"
            await manager.close_browser(profile.id)
            changed_pin = {**pinned, "navigator.hardwareConcurrency": 31}
            await manager.update_profile(
                profile.id, {"tags": ["after-checkpoint"], "fingerprint": changed_pin}
            )
            changed = await manager.get_profile(profile.id)
            assert changed.tags == ["after-checkpoint"]
            assert changed.fingerprint == changed_pin
            await close_manager()

            gate.acquire("browser-restore")
            restore_checkpoint(checkpoint, db_path=database, paths=paths, maintenance_check=offline)
            gate.release("browser-restore")

            await open_manager()
            recovered = await manager.get_profile(profile.id)
            assert recovered.tags == ["before-checkpoint"]
            assert recovered.fingerprint == pinned
            page = await page_for(profile.id)
            assert "recovery=original" in await page.evaluate("document.cookie")
            assert "recovery=newer" not in await page.evaluate("document.cookie")
            assert await page.evaluate("localStorage.getItem('recovery')") == "original"
            assert await page.evaluate(IDENTITY) == original_identity
        finally:
            await close_manager()

    await asyncio.wait_for(exercise(), timeout=180)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_installed_validation_command_with_real_paired_browser(tmp_path):
    """CI-only: run the actual fresh-process validation against disposable data."""
    import json
    import os
    import sqlite3
    import sys
    from contextlib import closing

    from camoufox.multiversion import CONFIG_FILE, get_active_path, list_installed

    from camoufox_pm import __version__
    from camoufox_pm.core.schema_migrations import CURRENT_SCHEMA
    from camoufox_pm.core.update_permissions import create_private_file

    if os.environ.get("GITHUB_ACTIONS") != "true":
        pytest.skip("Uses the disposable CI browser cache, never a developer's active config")
    active = get_active_path()
    paired = next(item for item in list_installed() if item.path == active)
    before = CONFIG_FILE.read_bytes() if CONFIG_FILE.exists() else None
    database = tmp_path / "profiles.db"
    storage = StorageManager(str(database))
    await storage.initialize()
    await storage.close()
    root = tmp_path / "control"
    gate = MaintenanceGate(root / "maintenance.sqlite3")
    gate.acquire("validation-smoke")
    manifest = {
        "version": __version__,
        "schema_result": CURRENT_SCHEMA,
        "browser_version": paired.version.full_string,
    }
    record = {
        "transaction_id": "validation-smoke",
        "manifest": manifest,
        "db_path": str(database),
        "paths": {},
    }
    for name, value in (
        ("transaction.json", record),
        (
            "validation.json",
            {
                "transaction_id": record["transaction_id"],
                "manifest": manifest,
            },
        ),
    ):
        with os.fdopen(create_private_file(root / name), "w") as stream:
            json.dump(value, stream)
    environment = dict(os.environ, CPM_UPDATE_STATE_DIR=str(root), CPM_DB_PATH=str(database))
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "camoufox_pm.cli",
            "--update-validate",
            str(root / "validation.json"),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=150)
        assert process.returncode == 0, (stdout + stderr).decode(errors="replace")
        assert json.loads((root / "validation.json").read_text()) == {
            "validated": "validation-smoke",
        }
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA
        assert gate.blocked, "Only the parent helper may release maintenance after acceptance"
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        gate.release("validation-smoke")
        if before is None:
            CONFIG_FILE.unlink(missing_ok=True)
        else:
            CONFIG_FILE.write_bytes(before)
