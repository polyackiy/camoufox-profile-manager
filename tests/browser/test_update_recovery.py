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
