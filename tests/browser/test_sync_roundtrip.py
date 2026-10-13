"""Real closed-browser state survives authenticated encrypted device handoff.

Run only in CI's native-browser matrix. Devices use separate manager databases and
profile directories; the local ASGI relay executes production signature, receipt,
quota and ciphertext code. The in-memory credential store isolates OS keychain UI,
which has a separate native-vault matrix. No third-party account or network is used.
"""

import hashlib
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.sync_guard import guard_profile
from camoufox_pm.core.update_permissions import create_private_directory
from camoufox_pm.sync.client import RelayClient
from camoufox_pm.sync.engine import SyncEngine
from camoufox_pm.sync.identity import generate_device, pairing_fingerprint
from camoufox_pm.sync.relay import create_relay_app
from camoufox_pm.sync.runtime import SyncRuntime, installed_compatibility
from tests.browser.support import GEOIP_ADDRESS
from tests.browser.test_fingerprint_stability import CANVAS, IDENTITY
from tests.unit.test_sync_engine import Vault


async def observe(manager, profile_id, origin, *, initialize=False, advance=False):
    await manager.launch_browser(profile_id, headless=True, geoip=GEOIP_ADDRESS)
    browser = manager.browser_sessions.active_sessions[profile_id].camoufox.browser
    try:
        if initialize:
            await browser.add_cookies(
                [
                    {
                        "name": "sync-auth",
                        "value": "synthetic-authenticated-session",
                        "url": origin,
                        "httpOnly": True,
                        "sameSite": "Lax",
                        "expires": int(time.time()) + 86400,
                    }
                ]
            )
        authenticated = []

        async def account(route):
            valid = "sync-auth=synthetic-authenticated-session" in route.request.headers.get(
                "cookie", ""
            )
            authenticated.append(valid)
            await route.fulfill(
                status=200 if valid else 401,
                content_type="text/html",
                body="<!doctype html><title>Local account</title><p>Account proof</p>",
            )

        await browser.route(origin.rstrip("/") + "/account", account)
        page = await browser.new_page()
        await page.goto(origin.rstrip("/") + "/account", wait_until="domcontentloaded")
        assert authenticated == [True], (
            "The transferred persistent cookie must authenticate the local account"
        )
        assert "sync-auth" not in await page.evaluate("document.cookie"), "HttpOnly must survive"
        if initialize:
            await page.evaluate("localStorage.setItem('sync-state', 'first-device')")
        state = await page.evaluate("localStorage.getItem('sync-state')")
        if advance:
            await page.evaluate("localStorage.setItem('sync-state', 'second-device')")
        identity = await page.evaluate(IDENTITY)
        canvas = hashlib.sha256((await page.evaluate(CANVAS)).encode()).hexdigest()
        cookies = {cookie["name"]: cookie for cookie in await browser.cookies(origin)}
        assert cookies["sync-auth"]["httpOnly"]
        assert cookies["sync-auth"]["expires"] > time.time()
        return state, identity, canvas
    finally:
        await manager.close_browser(profile_id)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_closed_profile_two_device_authenticated_roundtrip(
    tmp_path, monkeypatch, local_sites
):
    compatibility = installed_compatibility()
    assert compatibility.browser_build and compatibility.os and compatibility.architecture
    managers = []
    clients = []
    http = None
    try:
        for name in ("owner", "recipient"):
            directory = tmp_path.resolve() / name
            create_private_directory(directory)
            storage = StorageManager(str(directory / "profiles.db"))
            await storage.initialize()
            await storage.create_user(name + "-user", name, "unused-browser-fixture-hash")
            manager = ProfileManager(storage, str(directory))
            await manager.initialize()
            managers.append(manager)
        owner_manager, recipient_manager = managers
        owner = SyncRuntime(
            owner_manager, MaintenanceGate(owner_manager.data_dir / "maintenance.db")
        )
        recipient = SyncRuntime(
            recipient_manager, MaintenanceGate(recipient_manager.data_dir / "maintenance.db")
        )
        for runtime in (owner, recipient):
            vault = Vault()
            monkeypatch.setattr(runtime.service, "_vault", lambda *_args, vault=vault: vault)
        owner.service.begin_enrollment("owner-user", "https://relay.example", "Owner", "native")
        record, enrolled = owner.service._read("owner-user")
        owner.service.control.update(
            "installation",
            enrolled.model_copy(update={"phase": "ready"}).model_dump(mode="json"),
            record.revision,
        )
        token = "synthetic-browser-relay-registration-token"
        relay = create_relay_app(
            tmp_path.resolve() / "relay",
            relay_url=enrolled.root.relay_url,
            registration_token=SecretStr(token),
        )
        http = TestClient(relay, base_url=enrolled.root.relay_url)

        def factory(root, device_id, keys):
            return RelayClient(root, device_id, keys, transport=http._transport)

        owner.client_factory = recipient.client_factory = factory
        authority, keys = owner.service._keys(enrolled, None)
        client = factory(enrolled.root, enrolled.device_id, keys)
        clients.append(client)
        client.register(authority, enrolled.membership, token)
        # Exact canvas continuity is an explicit per-profile opt-in: the
        # default deliberately randomizes exports per session, even locally.
        original = await owner_manager.create_profile(
            "Encrypted browser roundtrip",
            generate_fingerprint=False,
            browser_settings={"stable_canvas": True},
        )
        assert original.browser_settings.stable_canvas
        before = await observe(owner_manager, original.id, local_sites.first, initialize=True)
        original = await owner_manager.get_profile(original.id)
        pinned = original.fingerprint
        assert pinned, "Manager must persist the fingerprint before capturing the browser"
        enabled = await owner.enable("owner-user", original.id)
        remote_id = enabled["checkpoint"]["profile_id"]
        engine = SyncEngine(owner.service, "owner-user", client)
        device = generate_device()
        now = int(time.time())
        request = owner.service.create_pairing_invitation(
            "owner-user", device.certificate(enrolled.root.owner_id, str(uuid4())), now=now
        )
        approval = owner.service.approve_pairing_invitation(
            "owner-user",
            request,
            pairing_fingerprint(request),
            now=now,
            checkpoints=engine.pairing_checkpoints(),
        )
        client.rpc(
            {
                "operation": "update_membership",
                "membership": approval.membership.model_dump(mode="json"),
            }
        )
        recipient.service.consume_pairing_approval(
            "recipient-user",
            enrolled.root,
            request,
            approval,
            device,
            "Recipient",
            "native",
            now=now,
            confirmed_fingerprint=pairing_fingerprint(request),
        )
        released = await owner.handoff("owner-user", original.id)
        assert released["phase"] == "released"
        imported = await recipient.bind_paired("recipient-user", remote_id)
        local_id = imported["local_id"]
        assert local_id != original.id
        acquired = await recipient.acquire("recipient-user", local_id)
        assert acquired["phase"] == "held"
        transferred = await recipient_manager.get_profile(local_id)
        assert transferred.fingerprint == pinned
        assert transferred.browser_settings == original.browser_settings
        assert transferred.browser_settings.stable_canvas
        assert Path(transferred.storage_path).is_relative_to(recipient_manager.profiles_dir)
        assert transferred.storage_path != original.storage_path
        assert await guard_profile(recipient_manager, local_id)
        after = await observe(recipient_manager, local_id, local_sites.first, advance=True)
        assert after == before, (
            "Cookie-authenticated storage and pinned identity must survive transfer"
        )
        assert recipient.service.control.get("runtime/dirty/" + local_id), (
            "Browser close must queue its changed local state"
        )
        await recipient.handoff("recipient-user", local_id)
        returned = await owner.acquire("owner-user", original.id)
        assert returned["phase"] == "held"
        final = await observe(owner_manager, original.id, local_sites.first)
        assert final[0] == "second-device"
        assert final[1:] == before[1:]
        restored = await owner_manager.get_profile(original.id)
        assert restored.fingerprint == pinned
        assert restored.browser_settings == original.browser_settings
        for runtime in (owner, recipient):
            assert not list(runtime.cache.glob("*/snapshot.zip")), (
                "No plaintext transfer archive may remain"
            )
            assert list(runtime.cache.glob("*/ciphertext"))
    finally:
        for manager in reversed(managers):
            try:
                await manager.close_all_browsers()
            finally:
                await manager.browser_sessions.stop_heartbeat()
                await manager.storage.close()
        for client in clients:
            client.close()
        if http is not None:
            http.close()
