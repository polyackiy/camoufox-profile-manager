"""Public ceremony workflows against the real relay app, without OS keychain writes."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from camoufox_pm.sync.ceremonies import CeremonyError, DeviceCeremonies
from camoufox_pm.sync.client import RelayClient, RelayError
from camoufox_pm.sync.identity import (
    OwnerRoot,
    PairingRequest,
    TrustedCheckpoint,
    pairing_fingerprint,
)
from camoufox_pm.sync.relay import create_relay_app
from camoufox_pm.sync.service import SyncService

TOKEN = "operator-registration-secret-1234567890"
ORIGIN = "https://relay.example"
USER = "local-owner-id"
PEER = "local-peer-id"
NOW = 2000000000


def authorize():
    pass


class MemoryVault:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def pair(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    app = create_relay_app(root / "relay", relay_url=ORIGIN, registration_token=SecretStr(TOKEN))
    asgi = TestClient(app, base_url=ORIGIN)

    def transport(request):
        result = asgi.request(
            request.method, str(request.url), headers=dict(request.headers), content=request.read()
        )
        return httpx.Response(
            result.status_code,
            headers=dict(result.headers),
            stream=httpx.ByteStream(result.content),
        )

    def factory(owner, device_id, keys):
        return RelayClient(owner, device_id, keys, transport=httpx.MockTransport(transport))

    owner_service, peer_service = SyncService(root / "owner"), SyncService(root / "peer")
    owner_vault, peer_vault = MemoryVault(), MemoryVault()
    monkeypatch.setattr(owner_service, "_vault", lambda *args: owner_vault)
    monkeypatch.setattr(peer_service, "_vault", lambda *args: peer_vault)
    checkpoint = TrustedCheckpoint(
        profile_id=str(uuid4()),
        revision_id=str(uuid4()),
        checkout_generation=4,
        holder_device_id=None,
    )
    owner = DeviceCeremonies(
        owner_service, checkpoints=lambda: (checkpoint,), client_factory=factory
    )
    peer = DeviceCeremonies(peer_service, checkpoints=lambda: (), client_factory=factory)
    monkeypatch.setattr(peer, "_draft_vault", lambda *args: peer_vault)
    owner_service.begin_enrollment(USER, ORIGIN, "Owner laptop", "native", authorize=authorize)
    package, secret = owner_service.recovery_package(USER, authorize=authorize)
    owner_service.confirm_recovery(USER, package, secret, authorize=authorize)
    yield SimpleNamespace(
        owner=owner,
        peer=peer,
        owner_service=owner_service,
        peer_service=peer_service,
        owner_vault=owner_vault,
        peer_vault=peer_vault,
        checkpoint=checkpoint,
        factory=factory,
        token=TOKEN,
    )
    asgi.close()


def prepare(pair):
    code = pair.owner.export_owner_code(USER, authorize=authorize)
    root = OwnerRoot.model_validate(code)
    prepared = pair.peer.prepare_pairing(
        PEER, root, "Second desktop", "native", now=NOW, authorize=authorize
    )
    return PairingRequest.model_validate(prepared["request"]), prepared["fingerprint"]


def enroll_peer(pair):
    pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    request, fingerprint = prepare(pair)
    approval = pair.owner.approve_request(
        USER, request, fingerprint, now=NOW + 1, authorize=authorize
    )
    result = pair.peer.accept_approval(
        PEER, approval, fingerprint, now=NOW + 2, authorize=authorize
    )
    return request, approval, result


def test_real_two_device_ceremony_does_not_copy_authority(pair):
    request, approval, result = enroll_peer(pair)
    assert result["phase"] == "freshness_unknown" and result["authority"] is False
    assert list(pair.peer_vault.values) == [request.device.device_id]
    root = pair.owner_service._read(USER)[1].root
    assert root.owner_id not in pair.peer_vault.values
    assert approval.checkpoints == (pair.checkpoint,)
    received = pair.peer_service.control.get("received/" + request.invitation_id)
    assert received.payload["checkpoints"] == [pair.checkpoint.model_dump(mode="json")]
    assert len(pair.owner.list_devices(USER, authorize=authorize)["devices"]) == 2
    assert (
        pair.peer.accept_approval(
            PEER, approval, pairing_fingerprint(request), now=NOW + 3, authorize=authorize
        )["phase"]
        == "freshness_unknown"
    )


def test_operator_token_never_persisted_and_registration_retry_verified(pair):
    assert pair.owner.connect_relay(USER, TOKEN, authorize=authorize)["connected"]
    assert pair.owner.connect_relay(USER, TOKEN, authorize=authorize)["connected"]
    assert TOKEN.encode() not in pair.owner_service.control.path.read_bytes()
    assert TOKEN not in str(pair.owner_service.control.list())


def test_wrong_session_cannot_export_prepare_accept_or_revoke(pair):
    request, approval, _ = enroll_peer(pair)
    for operation in [
        lambda: pair.owner.export_owner_code("other-user", authorize=authorize),
        lambda: pair.owner.list_devices("other-user", authorize=authorize),
        lambda: pair.owner.revoke_device(
            "other-user", request.device.device_id, authorize=authorize
        ),
        lambda: pair.peer.accept_approval(
            "other-user", approval, pairing_fingerprint(request), now=NOW + 3, authorize=authorize
        ),
    ]:
        with pytest.raises(CeremonyError):
            operation()


def test_fingerprint_confirmation_and_expiry_checked_before_owner_mutation(pair):
    pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    request, fingerprint = prepare(pair)
    original = pair.owner_service.control.get("installation")
    with pytest.raises(CeremonyError):
        pair.owner.approve_request(USER, request, "wrong", now=NOW + 1, authorize=authorize)
    with pytest.raises(CeremonyError):
        pair.owner.approve_request(USER, request, fingerprint, now=NOW + 901, authorize=authorize)
    assert pair.owner_service.control.get("installation") == original
    assert pair.owner_service.control.get("ceremony/membership") is None


def test_revocation_publishes_chain_and_revoked_peer_loses_relay_access(pair):
    request, _, _ = enroll_peer(pair)
    response = pair.owner.revoke_device(USER, request.device.device_id, authorize=authorize)
    assert response == {"membership_epoch": 3, "revoked_device_id": request.device.device_id}
    state = pair.peer_service._read(PEER)[1]
    keys = pair.peer_service.device_keys(PEER, authorize=authorize)
    with pair.factory(state.root, state.device_id, keys) as client, pytest.raises(RelayError):
        client.rpc({"operation": "membership"})
    assert len(pair.owner.list_devices(USER, authorize=authorize)["devices"]) == 1
    with pytest.raises(CeremonyError):
        pair.peer.revoke_device(
            PEER, pair.owner_service._read(USER)[1].device_id, authorize=authorize
        )


def test_owner_cannot_accidentally_revoke_sole_authority_installation(pair):
    pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    device_id = pair.owner_service._read(USER)[1].device_id
    with pytest.raises(CeremonyError):
        pair.owner.revoke_device(USER, device_id, authorize=authorize)


def test_lost_membership_response_reconciles_exact_signed_successor(pair, monkeypatch):
    pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    request, fingerprint = prepare(pair)
    original = RelayClient.rpc
    lost = []

    def lose_reply(client, payload):
        response = original(client, payload)
        if payload["operation"] == "update_membership" and not lost:
            lost.append(True)
            raise RelayError("network response lost")
        return response

    monkeypatch.setattr(RelayClient, "rpc", lose_reply)
    with pytest.raises(CeremonyError):
        pair.owner.approve_request(USER, request, fingerprint, now=NOW + 1, authorize=authorize)
    assert pair.owner_service.control.get("ceremony/membership").payload["phase"] == "pending"
    approval = pair.owner.approve_request(
        USER, request, fingerprint, now=NOW + 2, authorize=authorize
    )
    assert approval.membership.membership.epoch == 2
    assert pair.owner_service.control.get("ceremony/membership").payload["phase"] == "published"


def test_lost_revocation_reply_can_resume_without_reissuing_membership(pair, monkeypatch):
    request, _, _ = enroll_peer(pair)
    original = RelayClient.rpc
    lost = []

    def lose_reply(client, payload):
        response = original(client, payload)
        if payload["operation"] == "update_membership" and not lost:
            lost.append(True)
            raise RelayError("network response lost")
        return response

    monkeypatch.setattr(RelayClient, "rpc", lose_reply)
    with pytest.raises(CeremonyError):
        pair.owner.revoke_device(USER, request.device.device_id, authorize=authorize)
    assert pair.owner.resume_membership(USER, authorize=authorize) == {
        "membership_epoch": 3,
        "phase": "published",
    }
    assert pair.owner_service._read(USER)[1].membership.membership.epoch == 3


def test_session_revoked_during_vault_access_blocks_state_change(pair, monkeypatch):
    request, fingerprint = prepare(pair)
    pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    allowed = [True]
    original = pair.owner_service._keys

    def keys(*args):
        value = original(*args)
        allowed[0] = False
        return value

    def check():
        if not allowed[0]:
            raise PermissionError("revoked session")

    monkeypatch.setattr(pair.owner_service, "_keys", keys)
    with pytest.raises(CeremonyError):
        pair.owner.approve_request(USER, request, fingerprint, now=NOW + 1, authorize=check)
    assert pair.owner_service.control.get("ceremony/membership") is None


def test_interrupted_recipient_vault_write_resumes_same_public_keys(pair, monkeypatch):
    root = OwnerRoot.model_validate(pair.owner.export_owner_code(USER, authorize=authorize))
    original = pair.peer_service.control.update
    failed = []

    def interrupt(key, *args, **kwargs):
        if key == "ceremony/pairing" and not failed:
            failed.append(True)
            raise OSError("interrupted journal")
        return original(key, *args, **kwargs)

    monkeypatch.setattr(pair.peer_service.control, "update", interrupt)
    with pytest.raises(CeremonyError):
        pair.peer.prepare_pairing(PEER, root, "Desktop", "native", now=NOW, authorize=authorize)
    original_request = pair.peer_service.control.get("ceremony/pairing").payload["request"]
    resumed = pair.peer.prepare_pairing(
        PEER, root, "Desktop", "native", now=NOW + 1, authorize=authorize
    )
    assert resumed["request"] == original_request
    assert len(pair.peer_vault.values) == 1


def test_explicit_abandon_retains_tombstone_and_new_pairing_uses_new_keys(pair):
    request, _ = prepare(pair)
    pair.peer.abandon_pairing(PEER, authorize=authorize)
    assert request.device.device_id not in pair.peer_vault.values
    assert pair.peer_service.control.get("abandoned-pairing/" + request.invitation_id) is not None
    new_request, _ = prepare(pair)
    assert new_request.device != request.device


def test_authorization_callback_required_even_with_local_user_id(pair):
    with pytest.raises(CeremonyError):
        pair.owner.export_owner_code(USER, authorize=None)


def test_protected_checkpoints_refuse_withheld_newer_membership(pair):
    from camoufox_pm.sync.ceremonies import protected_checkpoints
    from camoufox_pm.sync.identity import approve_membership

    service = pair.owner_service
    assert protected_checkpoints(service) == ()
    _, owner = service._read(USER)
    authority, _ = service._keys(owner, None)
    newer = approve_membership(
        owner.root,
        authority.authority_key,
        owner.membership.membership.devices,
        previous=owner.membership,
    )
    service.control.create("membership/2", newer.model_dump(mode="json"))
    with pytest.raises(CeremonyError):
        protected_checkpoints(service)


@pytest.mark.parametrize(
    "flag,payload",
    [
        ("sync/revoked", {"epoch": 1}),
        ("sync/membership-conflict", {"epoch": 1}),
        ("sync/restore-fence", {"phase": "pending"}),
    ],
)
def test_protected_checkpoints_global_fences(pair, flag, payload):
    from camoufox_pm.sync.ceremonies import protected_checkpoints

    pair.owner_service.control.create(flag, payload)
    with pytest.raises(CeremonyError):
        protected_checkpoints(pair.owner_service)


@pytest.mark.parametrize("problem", [None, "pending", "forked", "wrong_epoch", "mapping", "apply"])
def test_protected_checkpoints_validate_binding_and_mapping(pair, problem):
    from camoufox_pm.sync.ceremonies import protected_checkpoints
    from camoufox_pm.sync.engine import Binding, Checkpoint

    service = pair.owner_service
    _, owner = service._read(USER)
    profile_id, revision_id = str(uuid4()), str(uuid4())
    checkpoint = Checkpoint(
        owner_id=owner.root.owner_id,
        profile_id=profile_id,
        membership_epoch=2 if problem == "wrong_epoch" else 1,
        checkout_generation=1,
        revision_id=revision_id,
        holder_device_id=None,
    )
    binding = Binding(
        local_id="abcd1234",
        checkpoint=checkpoint,
        phase="pending" if problem == "pending" else "released",
        forked=problem == "forked",
    )
    service.control.create("profile/abcd1234", binding.model_dump(mode="json"))
    service.control.create(
        "mapping/" + profile_id, {"local_id": "other" if problem == "mapping" else "abcd1234"}
    )
    if problem == "apply":
        service.control.create("apply/abcd1234", {"phase": "publishing"})
    if problem:
        with pytest.raises(CeremonyError):
            protected_checkpoints(service)
    else:
        result = protected_checkpoints(service)
        assert len(result) == 1
        assert result[0].profile_id == profile_id
        assert result[0].revision_id == revision_id


def test_reconnect_after_pairing_verifies_current_membership(pair):
    pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    request, fingerprint = prepare(pair)
    pair.owner.approve_request(USER, request, fingerprint, now=NOW, authorize=authorize)
    assert pair.owner_service._read(USER)[1].membership.membership.epoch == 2
    result = pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    assert result == {"connected": True, "relay_url": ORIGIN}


def test_session_revoked_during_membership_read_blocks_publication(pair, monkeypatch):
    pair.owner.connect_relay(USER, TOKEN, authorize=authorize)
    request, fingerprint = prepare(pair)
    original = RelayClient.rpc
    revoked = False
    mutations = []

    def read_then_revoke(client, payload):
        nonlocal revoked
        if payload["operation"] == "update_membership":
            mutations.append(payload)
        response = original(client, payload)
        if payload["operation"] == "membership":
            revoked = True
        return response

    def check_session():
        if revoked:
            raise PermissionError("Session revoked")

    monkeypatch.setattr(RelayClient, "rpc", read_then_revoke)
    with pytest.raises(CeremonyError):
        pair.owner.approve_request(USER, request, fingerprint, now=NOW, authorize=check_session)
    assert not mutations
    assert pair.owner_service.control.get("ceremony/membership").payload["phase"] == "pending"


def test_revocation_unavailable_head_scan_warns_but_still_revokes(pair, monkeypatch):
    from camoufox_pm.sync.engine import SyncEngine

    request, _, _ = enroll_peer(pair)
    _, owner = pair.owner_service._read(USER)
    keys = pair.owner_service.device_keys(USER)
    with pair.factory(owner.root, owner.device_id, keys) as client:
        engine = SyncEngine(pair.owner_service, USER, client)
        engine.enable("profile1")
    original = RelayClient.rpc

    def missing_history(client, payload):
        if payload.get("operation") == "history":
            raise RelayError("private relay diagnostic must not leak")
        return original(client, payload)

    monkeypatch.setattr(RelayClient, "rpc", missing_history)
    result = pair.owner.revoke_device(USER, request.device.device_id, authorize=authorize)
    assert result["membership_epoch"] == 3
    assert result["checkpoint_scan"] == {"attempted": 1, "anchored": 0, "unavailable": 1}
    assert "historical-copy recovery" in result["warning"]
    assert "private relay diagnostic" not in str(result)
    assert pair.owner_service.control.get("profile/profile1").payload["phase"] in {
        "unknown",
        "conflict",
    }
    resumed = pair.owner.resume_membership(USER, authorize=authorize)
    assert resumed["warning"] == result["warning"]


def test_scan_authorization_loss_prevents_revocation(pair, monkeypatch):
    from camoufox_pm.sync.engine import SyncEngine

    request, _, _ = enroll_peer(pair)
    _, owner = pair.owner_service._read(USER)
    with pair.factory(owner.root, owner.device_id, pair.owner_service.device_keys(USER)) as client:
        SyncEngine(pair.owner_service, USER, client).enable("profile1")
    original = RelayClient.rpc
    revoked = False

    def delayed_scan(client, payload):
        nonlocal revoked
        result = original(client, payload)
        if payload.get("operation") == "history":
            revoked = True
        return result

    def live():
        if revoked:
            raise PermissionError("owner cookie revoked")

    monkeypatch.setattr(RelayClient, "rpc", delayed_scan)
    with pytest.raises(CeremonyError):
        pair.owner.revoke_device(USER, request.device.device_id, authorize=live)
    assert pair.owner_service._read(USER)[1].membership.membership.epoch == 2
