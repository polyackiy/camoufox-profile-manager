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
@pytest.mark.parametrize("lost_device", [False, True])
async def test_two_devices_real_relay_encrypted_handoff(
    runtime, area, tmp_path, monkeypatch, pair_after_release, wrong_build, lost_device
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
    from camoufox_pm.sync.ceremonies import DeviceCeremonies

    ceremonies = DeviceCeremonies(runtime.service, checkpoints=lambda: (), client_factory=factory)
    ceremonies.connect_relay(
        "owner-user", "operator-registration-token-for-runtime-test", authorize=lambda: None
    )
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
        if lost_device:
            from camoufox_pm.sync.snapshots import SnapshotError

            with pytest.raises(EngineError, match="Confirm"):
                await runtime.takeover("owner-user", area.profile.id, acknowledge_lost_device=False)
            with pytest.raises(EngineError, match="owner authority"):
                await second.takeover(
                    "second-user", imported["local_id"], acknowledge_lost_device=True
                )
            with pytest.raises(EngineError, match="Revoke"):
                await runtime.takeover("owner-user", area.profile.id, acknowledge_lost_device=True)
            # The lost device publishes another head after the owner's last view.
            # A normal UI revocation must anchor it itself, without a prior takeover.
            await second.handoff("second-user", imported["local_id"])
            acquired = await second.acquire("second-user", imported["local_id"])
            before_revoke = runtime.service.control.get("profile/" + area.profile.id).payload
            assert (
                before_revoke["checkpoint"]["revision_id"] != acquired["checkpoint"]["revision_id"]
            )
            revoked = ceremonies.revoke_device(
                "owner-user", request.device.device_id, authorize=lambda: None
            )
            assert revoked["checkpoint_scan"] == {"attempted": 1, "anchored": 1, "unavailable": 0}
            runtime.compatibility = lambda: area.compatibility.model_copy(
                update={"browser_build": "incompatible-build"}
            )
            with pytest.raises(SnapshotError):
                await runtime.takeover("owner-user", area.profile.id, acknowledge_lost_device=True)
            assert (await runtime.list_profiles("owner-user"))["profiles"][0]["checkpoint"][
                "holder_device_id"
            ] == request.device.device_id
            runtime.compatibility = lambda: area.compatibility
            if not pair_after_release and not wrong_build:
                from camoufox_pm.sync.client import RelayError

                original_rpc = RelayClient.rpc
                failed = False

                def lost_ack(instance, payload):
                    nonlocal failed
                    reply = original_rpc(instance, payload)
                    if payload.get("operation") == "takeover" and not failed:
                        failed = True
                        raise RelayError("lost takeover acknowledgement")
                    return reply

                monkeypatch.setattr(RelayClient, "rpc", lost_ack)
                with pytest.raises(RelayError):
                    await runtime.takeover(
                        "owner-user", area.profile.id, acknowledge_lost_device=True
                    )
                pending = runtime.service.control.get("profile/" + area.profile.id).payload[
                    "pending"
                ]
                assert pending["kind"] == "takeover"
                assert (
                    await area.manager.storage.get_profile(area.profile.id)
                ).storage_path == area.profile.storage_path
            returned = await runtime.takeover(
                "owner-user", area.profile.id, acknowledge_lost_device=True
            )
            assert (
                returned["checkpoint"]["checkout_generation"]
                == acquired["checkpoint"]["checkout_generation"] + 1
            )
        else:
            await second.handoff("second-user", imported["local_id"])
            returned = await runtime.acquire("owner-user", area.profile.id)
        assert returned["phase"] == "held"
        if not (pair_after_release or wrong_build or lost_device):
            deleted = await runtime.trash("owner-user", area.profile.id, acknowledge_deletion=True)
            assert deleted["phase"] == "released"
            assert (
                await area.manager.storage.get_profile(area.profile.id, include_deleted=True)
            ).deleted_at
            with pytest.raises(EngineError, match="Authenticated deletion"):
                await second.acquire("second-user", imported["local_id"])
            # Explicit confirmation preserves the recipient's local data in Trash.
            await second.apply_trash("second-user", imported["local_id"], acknowledge_deletion=True)
            assert (
                await storage.get_profile(imported["local_id"], include_deleted=True)
            ).deleted_at
            restored = await runtime.restore(
                "owner-user", area.profile.id, acknowledge_restore=True
            )
            assert restored["phase"] == "held"
            assert restored["checkpoint"]["revision_id"] != deleted["checkpoint"]["revision_id"]
            assert (await area.manager.storage.get_profile(area.profile.id)).deleted_at is None
            assert not list(runtime.cache.glob("*/restored.zip"))
            await runtime.handoff("owner-user", area.profile.id)
            resumed = await second.acquire("second-user", imported["local_id"])
            assert resumed["phase"] == "held"
            assert (await storage.get_profile(imported["local_id"])).deleted_at is None
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


async def test_preserve_copy_does_not_delete_existing_target_on_collision(
    runtime, area, monkeypatch
):
    from camoufox_pm.sync import runtime as module
    from camoufox_pm.sync.snapshots import SnapshotError

    runtime, _ = runtime
    await runtime.enable("owner-user", area.profile.id)
    identity = "00000000-0000-4000-8000-000000000001"
    target = area.manager.profiles_dir / ("recovered-" + identity)
    target.mkdir()
    sentinel = target / "keep"
    sentinel.write_text("existing unrelated data")
    monkeypatch.setattr(module, "uuid4", lambda: identity)
    with pytest.raises(SnapshotError):
        await runtime.preserve_local_copy("owner-user", area.profile.id, acknowledge_fork=True)
    assert sentinel.read_text() == "existing unrelated data"


@pytest.mark.parametrize("initial", [False, True])
async def test_expired_retry_encrypts_fresh_revision_and_retains_original(
    runtime, area, monkeypatch, initial
):
    from pathlib import Path

    from camoufox_pm.sync.engine import SyncEngine

    runtime, server = runtime
    if not initial:
        await runtime.enable("owner-user", area.profile.id)
    original = SyncEngine._send_pending
    failed = False

    def interrupted(self, local_id):
        nonlocal failed
        if self.status(local_id).pending.kind == "commit" and not failed:
            failed = True
            raise RuntimeError("interrupted before commit")
        return original(self, local_id)

    monkeypatch.setattr(SyncEngine, "_send_pending", interrupted)
    with pytest.raises(RuntimeError):
        await (runtime.enable if initial else runtime.handoff)("owner-user", area.profile.id)
    pending = runtime.service.control.get("profile/" + area.profile.id).payload["pending"]
    manifest = pending["body"]["manifest"]
    old_path = Path(pending["ciphertext_path"])
    old_bytes = old_path.read_bytes()
    server.blobs[manifest["revision_id"]].update(state="expired", ciphertext_length=0)
    result = await runtime.retry("owner-user", area.profile.id)
    assert result["phase"] == ("held" if initial else "released")
    assert result["checkpoint"]["revision_id"] != manifest["revision_id"]
    assert old_path.read_bytes() == old_bytes
    assert runtime.service.control.get("expired-upload/" + pending["operation_id"])
    assert not list(runtime.cache.glob("*/snapshot.zip"))
    fresh = [p for p in runtime.cache.glob("*/ciphertext") if p != old_path]
    assert fresh and all(p.read_bytes() != old_bytes for p in fresh)


@pytest.fixture
async def lifecycle_runtime(runtime, tmp_path):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from camoufox_pm.sync.ceremonies import DeviceCeremonies
    from camoufox_pm.sync.client import RelayClient
    from camoufox_pm.sync.relay import create_relay_app

    runtime, _ = runtime
    _, owner = runtime.service._read("owner-user")
    token = "lifecycle-test-registration-token"
    app = create_relay_app(
        tmp_path.resolve() / "lifecycle-relay",
        relay_url=owner.root.relay_url,
        registration_token=SecretStr(token),
    )
    with TestClient(app, base_url=owner.root.relay_url) as http:
        runtime.client_factory = lambda root, device_id, keys: RelayClient(
            root, device_id, keys, transport=http._transport
        )
        DeviceCeremonies(
            runtime.service, checkpoints=lambda: (), client_factory=runtime.client_factory
        ).connect_relay("owner-user", token, authorize=lambda: None)
        yield runtime


@pytest.mark.parametrize("operation", ["trash", "restore"])
@pytest.mark.parametrize("failure", ["before_intent", "after_commit"])
async def test_lifecycle_publication_crash_resumes_exact_revision(
    lifecycle_runtime, area, monkeypatch, operation, failure
):
    from camoufox_pm.sync.engine import SyncEngine

    runtime = lifecycle_runtime
    await runtime.enable("owner-user", area.profile.id)
    if operation == "restore":
        await runtime.trash("owner-user", area.profile.id, acknowledge_deletion=True)
    original = SyncEngine.publish
    failed = False

    def interrupted(engine, *args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            if failure == "after_commit":
                original(engine, *args, **kwargs)
            raise RuntimeError("interrupted lifecycle publication")
        return original(engine, *args, **kwargs)

    monkeypatch.setattr(SyncEngine, "publish", interrupted)
    args = {"acknowledge_restore" if operation == "restore" else "acknowledge_deletion": True}
    with pytest.raises(RuntimeError):
        await getattr(runtime, operation)("owner-user", area.profile.id, **args)
    job = runtime.service.control.get("lifecycle/" + area.profile.id).payload
    target = job.get("publication", job["revision_id"])
    result = await runtime.retry("owner-user", area.profile.id)
    assert result["checkpoint"]["revision_id"] == target
    assert result["phase"] == ("released" if operation == "trash" else "held")
    assert (
        runtime.service.control.get("lifecycle/" + area.profile.id).payload["phase"] == "complete"
    )
    profile = await area.manager.storage.get_profile(area.profile.id, include_deleted=True)
    assert bool(profile.deleted_at) is (operation == "trash")
    assert not list(runtime.cache.glob("*/snapshot.zip"))
    assert not list(runtime.cache.glob("*/restored.zip"))


@pytest.mark.parametrize("where", ["claim", "apply"])
async def test_restore_resumes_after_checkout_or_activation_interruption(
    lifecycle_runtime, area, monkeypatch, where
):
    from camoufox_pm.sync import runtime as module
    from camoufox_pm.sync.engine import SyncEngine

    runtime = lifecycle_runtime
    await runtime.enable("owner-user", area.profile.id)
    await runtime.trash("owner-user", area.profile.id, acknowledge_deletion=True)
    failed = False
    if where == "claim":
        original = SyncEngine.claim

        def interrupted(self, *args, **kwargs):
            nonlocal failed
            result = original(self, *args, **kwargs)
            if not failed:
                failed = True
                raise RuntimeError("interrupted after checkout")
            return result

        monkeypatch.setattr(SyncEngine, "claim", interrupted)
    else:
        original = module.apply_snapshot

        async def interrupted(*args, **kwargs):
            nonlocal failed
            result = await original(*args, **kwargs)
            if not failed:
                failed = True
                raise RuntimeError("interrupted after activation")
            return result

        monkeypatch.setattr(module, "apply_snapshot", interrupted)
    with pytest.raises(RuntimeError):
        await runtime.restore("owner-user", area.profile.id, acknowledge_restore=True)
    result = await runtime.retry("owner-user", area.profile.id)
    assert result["phase"] == "held"
    assert runtime.service.control.get("lifecycle/" + area.profile.id).payload["state"] == "active"


async def test_synced_local_trash_bypass_and_missing_confirmation_are_rejected(
    lifecycle_runtime, area
):
    from camoufox_pm.core.profile_manager import ProfileManager
    from camoufox_pm.core.sync_guard import SyncBlocked

    area.manager.get_profile = area.manager.storage.get_profile

    runtime = lifecycle_runtime
    await runtime.enable("owner-user", area.profile.id)
    with pytest.raises(SyncBlocked):
        await ProfileManager.delete_profile(area.manager, area.profile.id)
    with pytest.raises(EngineError):
        await runtime.trash("owner-user", area.profile.id, acknowledge_deletion=1)
    assert await area.manager.storage.get_profile(area.profile.id)
    await runtime.trash("owner-user", area.profile.id, acknowledge_deletion=True)
    with pytest.raises(SyncBlocked):
        await ProfileManager.restore_trashed_profile(area.manager, area.profile.id)
    with pytest.raises(SyncBlocked):
        await ProfileManager.permanently_delete_profile(area.manager, area.profile.id)


@pytest.mark.parametrize("failure", ["before_commit", "after_record_applied"])
async def test_restore_publication_activation_is_idempotent(
    lifecycle_runtime, area, monkeypatch, failure
):
    from camoufox_pm.sync.engine import SyncEngine

    runtime = lifecycle_runtime
    await runtime.enable("owner-user", area.profile.id)
    await runtime.trash("owner-user", area.profile.id, acknowledge_deletion=True)
    failed = False
    original_send = SyncEngine._send_pending
    original_record = SyncEngine.record_applied
    calls = 0

    def interrupted_send(self, local_id):
        nonlocal failed
        if (
            failure == "before_commit"
            and self.status(local_id).pending.kind == "commit"
            and not failed
        ):
            failed = True
            raise RuntimeError("interrupted before commit")
        return original_send(self, local_id)

    def interrupted_record(self, *args):
        nonlocal calls
        result = original_record(self, *args)
        calls += 1
        if failure == "after_record_applied" and calls == 2:
            raise RuntimeError("interrupted after restoration installed")
        return result

    monkeypatch.setattr(SyncEngine, "_send_pending", interrupted_send)
    monkeypatch.setattr(SyncEngine, "record_applied", interrupted_record)
    with pytest.raises(RuntimeError):
        await runtime.restore("owner-user", area.profile.id, acknowledge_restore=True)
    result = await runtime.retry("owner-user", area.profile.id)
    assert result["phase"] == "held"
    assert (
        runtime.service.control.get("lifecycle/" + area.profile.id).payload["phase"] == "complete"
    )
