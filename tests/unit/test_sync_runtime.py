"""Local leases, durable checkout and encrypted handoff exercised together."""
# ruff: noqa: F811

import pytest

from camoufox_pm.sync.engine import EngineError
from camoufox_pm.sync.runtime import SyncRuntime
from tests.unit.test_sync_apply import area  # noqa: F401
from tests.unit.test_sync_engine import Client, Server, Vault


@pytest.fixture
async def runtime(area, monkeypatch):
    runtime = SyncRuntime(area.manager, area.maintenance, compatibility=lambda: area.compatibility)
    await area.manager.storage.db.create_user("owner-user", "owner", "hash")
    vault = Vault()
    monkeypatch.setattr(runtime.service, "_vault", lambda state, password: vault)
    runtime.service.begin_enrollment("owner-user", "https://relay.example", "Owner", "native")
    record, owner = runtime.service._read("owner-user")
    runtime.service.control.update(
        "installation",
        owner.model_copy(update={"phase": "ready"}).model_dump(mode="json"),
        record.revision,
    )
    server = Server(owner.root, owner.membership)

    class LocalClient(Client):
        def close(self):
            pass

    runtime.client_factory = lambda root, device_id, keys: LocalClient(server, device_id, keys)
    return runtime, server


async def test_enable_and_handoff_preserve_local_profile_and_encrypt(runtime, area):
    runtime, server = runtime
    state = await runtime.enable("owner-user", area.profile.id)
    assert state["phase"] == "held"
    assert state["installed_revision"] is not None
    assert (area.old / ".camoufox-pm-sync.json").exists()
    state = await runtime.handoff("owner-user", area.profile.id)
    assert state["phase"] == "released"
    assert state["checkpoint"]["holder_device_id"] is None
    assert len(server.blobs) == 2
    assert (area.old / "cookies.sqlite").read_bytes() == b"old local cookies"
    for cipher in runtime.cache.glob("*/ciphertext"):
        assert b"old local cookies" not in cipher.read_bytes()


async def test_demoted_owner_cannot_start_transfer(runtime, area):
    runtime, server = runtime
    area.manager.storage.db._connection.execute(
        "UPDATE users SET role='viewer' WHERE id='owner-user'"
    )
    area.manager.storage.db._connection.commit()
    with pytest.raises(EngineError):
        await runtime.enable("owner-user", area.profile.id)
    assert server.events == []


async def test_live_browser_prevents_remote_enable(runtime, area):
    runtime, server = runtime
    area.manager.browser_sessions.is_live = lambda _: True
    from camoufox_pm.core.leases import ProfileLocked

    with pytest.raises(ProfileLocked):
        await runtime.enable("owner-user", area.profile.id)
    assert server.events == []
    assert runtime.service.control.get("profile/" + area.profile.id) is None


@pytest.mark.parametrize("failure_call", [1, 2])
async def test_initial_claim_interruption_resumes_and_cleans_plaintext(
    runtime, area, monkeypatch, failure_call
):
    from camoufox_pm.sync.engine import SyncEngine

    runtime, server = runtime
    original = SyncEngine.record_applied
    calls = 0

    def interrupted(self, local_id, revision):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise RuntimeError("power loss before local activation")
        return original(self, local_id, revision)

    monkeypatch.setattr(SyncEngine, "record_applied", interrupted)
    with pytest.raises(RuntimeError):
        await runtime.enable("owner-user", area.profile.id)
    assert not list(runtime.cache.glob("*/snapshot.zip"))
    first_remote = runtime.service.control.get("profile/" + area.profile.id).payload["checkpoint"][
        "profile_id"
    ]
    state = await runtime.retry("owner-user", area.profile.id)
    assert state["phase"] == "held" and state["checkpoint"]["profile_id"] == first_remote
    assert len(server.profiles) == 1
    assert not list(runtime.cache.glob("*/snapshot.zip"))


async def test_authorization_callback_rechecked_before_publication(runtime, area):
    runtime, server = runtime
    calls = 0

    async def authorize():
        nonlocal calls
        calls += 1
        if calls > 4:
            raise PermissionError("cookie revoked")

    with pytest.raises(PermissionError):
        await runtime.enable("owner-user", area.profile.id, authorize=authorize)
    assert not server.blobs
    assert not list(runtime.cache.glob("*/snapshot.zip"))


@pytest.mark.parametrize("pair_after_release", [False, True])
@pytest.mark.parametrize("wrong_build", [False, True])
async def test_two_devices_real_relay_encrypted_handoff(
    runtime, area, tmp_path, monkeypatch, pair_after_release, wrong_build
):
    import time
    from types import SimpleNamespace
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from camoufox_pm.core.database import StorageManager
    from camoufox_pm.core.maintenance import MaintenanceGate
    from camoufox_pm.core.update_permissions import create_private_directory
    from camoufox_pm.sync.client import RelayClient
    from camoufox_pm.sync.engine import SyncEngine
    from camoufox_pm.sync.identity import generate_device, pairing_fingerprint
    from camoufox_pm.sync.relay import create_relay_app

    runtime, _ = runtime
    _, owner = runtime.service._read("owner-user")
    app = create_relay_app(
        tmp_path.resolve() / "real-relay",
        relay_url=owner.root.relay_url,
        registration_token=SecretStr("operator-registration-token-for-runtime-test"),
    )
    http = TestClient(app, base_url=owner.root.relay_url)

    def factory(root, device_id, keys):
        return RelayClient(root, device_id, keys, transport=http._transport)

    runtime.client_factory = factory
    authority, keys = runtime.service._keys(owner, None)
    client = factory(owner.root, owner.device_id, keys)
    client.register(authority, owner.membership, "operator-registration-token-for-runtime-test")
    state = await runtime.enable("owner-user", area.profile.id)
    if pair_after_release:
        await runtime.handoff("owner-user", area.profile.id)
    engine = SyncEngine(runtime.service, "owner-user", client)
    device = generate_device()
    now = int(time.time())
    request = runtime.service.create_pairing_invitation(
        "owner-user", device.certificate(owner.root.owner_id, str(uuid4())), now=now
    )
    approval = runtime.service.approve_pairing_invitation(
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

    second_dir = tmp_path.resolve() / "second-device"
    create_private_directory(second_dir)
    create_private_directory(second_dir / "profiles")
    storage = StorageManager(str(second_dir / "profiles.db"))
    await storage.initialize()
    await storage.create_user("second-user", "owner", "unused")
    manager = SimpleNamespace(
        storage=storage,
        data_dir=second_dir,
        profiles_dir=second_dir / "profiles",
        lease_holder="second",
        browser_sessions=SimpleNamespace(is_live=lambda _: False),
    )
    second = SyncRuntime(
        manager,
        MaintenanceGate(second_dir / "maintenance.sqlite3"),
        client_factory=factory,
        compatibility=lambda: area.compatibility,
    )
    vault = Vault()
    monkeypatch.setattr(second.service, "_vault", lambda *_: vault)
    second.service.consume_pairing_approval(
        "second-user",
        owner.root,
        request,
        approval,
        device,
        "Second",
        "native",
        now=now,
        confirmed_fingerprint=pairing_fingerprint(request),
    )
    try:
        available = await second.list_profiles("second-user")
        assert len(available["available"]) == 1
        imported = await second.bind_paired("second-user", state["checkpoint"]["profile_id"])
        if pair_after_release:
            with pytest.raises(EngineError, match="publish a new snapshot after pairing"):
                await second.acquire("second-user", imported["local_id"])
            assert (await second.list_profiles("second-user"))["profiles"][0]["checkpoint"][
                "holder_device_id"
            ] is None
            await runtime.acquire("owner-user", area.profile.id)
        await runtime.handoff("owner-user", area.profile.id)
        if wrong_build:
            from camoufox_pm.sync.snapshots import SnapshotError

            second.compatibility = lambda: area.compatibility.model_copy(
                update={"browser_build": "incompatible-build"}
            )
            with pytest.raises(SnapshotError):
                await second.acquire("second-user", imported["local_id"])
            assert (await second.list_profiles("second-user"))["profiles"][0]["checkpoint"][
                "holder_device_id"
            ] is None
            second.compatibility = lambda: area.compatibility
        acquired = await second.acquire("second-user", imported["local_id"])
        assert acquired["phase"] == "held"
        profile = await storage.get_profile(imported["local_id"])
        from pathlib import Path

        assert (Path(profile.storage_path) / "cookies.sqlite").read_bytes() == b"old local cookies"
        assert not list(second.cache.glob("*/snapshot.zip"))
        assert not list(runtime.cache.glob("*/snapshot.zip"))
        await second.handoff("second-user", imported["local_id"])
        returned = await runtime.acquire("owner-user", area.profile.id)
        assert returned["phase"] == "held"
    finally:
        await storage.close()
        client.close()
        http.close()


async def test_handoff_retry_preserves_original_encrypted_revision(runtime, area, monkeypatch):
    from camoufox_pm.sync.engine import SyncEngine

    runtime, server = runtime
    await runtime.enable("owner-user", area.profile.id)
    original = SyncEngine._send_pending
    failed = False

    def interrupted(self, local_id):
        nonlocal failed
        if self.status(local_id).pending.kind == "commit" and not failed:
            failed = True
            raise RuntimeError("offline before commit")
        return original(self, local_id)

    monkeypatch.setattr(SyncEngine, "_send_pending", interrupted)
    with pytest.raises(RuntimeError):
        await runtime.handoff("owner-user", area.profile.id)
    record = runtime.service.control.get("profile/" + area.profile.id)
    pending = record.payload["pending"]
    revision = pending["body"]["manifest"]["revision_id"]
    ciphertexts = list(runtime.cache.glob("*/ciphertext"))
    payload = (await runtime.list_profiles("owner-user"))["profiles"][0]
    assert set(payload["pending"]) == {"kind", "operation_id"}
    state = await runtime.retry("owner-user", area.profile.id)
    assert state["phase"] == "released" and state["checkpoint"]["revision_id"] == revision
    assert list(runtime.cache.glob("*/ciphertext")) == ciphertexts
    assert not list(runtime.cache.glob("*/snapshot.zip"))


@pytest.mark.parametrize("phase", ["conflict", "unknown", "revoked"])
async def test_explicit_local_fork_preserves_original_fence_without_keys(
    runtime, area, monkeypatch, phase
):
    from pathlib import Path

    from camoufox_pm.core.sync_guard import SYNC_MARKER

    runtime, _ = runtime
    await runtime.enable("owner-user", area.profile.id)
    record = runtime.service.control.get("profile/" + area.profile.id)
    runtime.service.control.update(record.key, {**record.payload, "phase": phase}, record.revision)
    original = runtime.service.control.get(record.key)
    monkeypatch.setattr(
        runtime.service,
        "device_keys",
        lambda *_args, **_kw: pytest.fail("fork must not unlock vault"),
    )
    with pytest.raises(EngineError, match="Confirm"):
        await runtime.preserve_local_copy("owner-user", area.profile.id, acknowledge_fork=False)
    result = await runtime.preserve_local_copy("owner-user", area.profile.id, acknowledge_fork=True)
    assert result["local_id"] != area.profile.id and result["synchronized"] is False
    assert result["name"].startswith("Recovered copy")
    profile = await area.manager.storage.get_profile(result["local_id"])
    assert (Path(profile.storage_path) / "cookies.sqlite").read_bytes() == b"old local cookies"
    assert not (Path(profile.storage_path) / SYNC_MARKER).exists()
    assert (area.old / SYNC_MARKER).is_file()
    assert runtime.service.control.get(record.key) == original
    assert runtime.service.control.get("profile/" + profile.id) is None
    assert all(
        schedule.profile_id != profile.id
        for schedule in await area.manager.storage.list_schedules()
    )
    assert all(
        grant["profile_id"] != profile.id
        for grant in await area.manager.storage.list_profile_grants("operator")
    )
    assert not list(runtime.cache.glob("*/snapshot.zip"))


async def test_revoked_cookie_during_upload_blocks_commit_and_retains_ciphertext(
    runtime, area, monkeypatch
):
    runtime, server = runtime
    revoked = False
    original = Client.upload

    def delayed_upload(client, *args):
        nonlocal revoked
        reply = original(client, *args)
        revoked = True
        return reply

    async def authorize():
        if revoked:
            raise PermissionError("cookie revoked during upload")

    monkeypatch.setattr(Client, "upload", delayed_upload)
    with pytest.raises(PermissionError):
        await runtime.enable("owner-user", area.profile.id, authorize=authorize)
    pending = runtime.service.control.get("profile/" + area.profile.id).payload["pending"]
    assert pending["kind"] == "commit"
    import json

    assert all(
        json.loads(event["receipt"]["body"])["operation"] != "commit" for event in server.events
    )
    from pathlib import Path

    assert Path(pending["ciphertext_path"]).is_file()
    assert not list(runtime.cache.glob("*/snapshot.zip"))


async def test_revocation_during_history_read_blocks_new_claim(runtime, area):
    runtime, server = runtime
    revoked = False

    def delayed_history(operation, response):
        nonlocal revoked
        if operation == "checkpoint":
            revoked = True
        return response

    async def authorize():
        if revoked:
            raise PermissionError("cookie revoked during history read")

    server.transform = delayed_history
    with pytest.raises(PermissionError):
        await runtime.enable("owner-user", area.profile.id, authorize=authorize)
    assert server.events == []
    pending = runtime.service.control.get("profile/" + area.profile.id).payload["pending"]
    assert pending["kind"] == "claim"


async def test_background_transfer_rechecks_live_admin_after_upload(runtime, area, monkeypatch):
    import asyncio
    import json

    runtime, server = runtime
    loop = asyncio.get_running_loop()
    original = Client.upload

    async def demote():
        area.manager.storage.db._connection.execute(
            "UPDATE users SET role='viewer' WHERE id='owner-user'"
        )
        area.manager.storage.db._connection.commit()

    def delayed_upload(client, *args):
        reply = original(client, *args)
        asyncio.run_coroutine_threadsafe(demote(), loop).result()
        return reply

    monkeypatch.setattr(Client, "upload", delayed_upload)
    with pytest.raises(EngineError, match="administrator"):
        await runtime.enable("owner-user", area.profile.id)
    assert all(
        json.loads(event["receipt"]["body"])["operation"] != "commit" for event in server.events
    )
    assert (
        runtime.service.control.get("profile/" + area.profile.id).payload["pending"]["kind"]
        == "commit"
    )
