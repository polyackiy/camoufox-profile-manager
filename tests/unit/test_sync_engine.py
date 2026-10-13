"""Hostile relay/state tests using real owner/device signatures and durable control."""

from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidSignature

from camoufox_pm.sync.client import RelayConflict, RelayError
from camoufox_pm.sync.engine import EngineError, SyncEngine
from camoufox_pm.sync.identity import (
    approve_membership,
    encode_key,
    generate_device,
    pairing_fingerprint,
)
from camoufox_pm.sync.protocol import SUITE, RecipientEnvelope, SnapshotManifest, signing_bytes
from camoufox_pm.sync.relay import request_signing_bytes
from camoufox_pm.sync.service import SyncService


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class Vault:
    def __init__(self):
        self.slots = {}

    def put(self, slot, value):
        self.slots[slot] = value

    def get(self, slot):
        return self.slots.get(slot)


class Server:
    def __init__(self, root, membership):
        self.root = root
        self.memberships = [membership]
        self.events = []
        self.profiles = {}
        self.blobs = {}
        self.offline = False
        self.lose_ack = None
        self.transform = None

    def checkpoint(self, profile):
        data = copy.deepcopy(
            self.profiles.get(
                profile,
                {
                    "owner_id": self.root.owner_id,
                    "profile_id": profile,
                    "membership_epoch": 1,
                    "checkout_generation": 0,
                    "revision_id": None,
                    "holder_device_id": None,
                },
            )
        )
        data["membership_epoch"] = self.memberships[-1].membership.epoch
        return data


class Client:
    def __init__(self, server, device_id, keys):
        self.server = server
        self.root = server.root
        self.device_id = device_id
        self.keys = keys

    def rpc(self, body):
        server = self.server
        if server.offline:
            raise RelayError("offline")
        operation = body["operation"]
        if operation == "membership":
            response = {
                "root": self.root.model_dump(mode="json"),
                "membership": server.memberships[-1].model_dump(mode="json"),
            }
        elif operation == "memberships":
            members = [m for m in server.memberships if m.membership.epoch > body["after_epoch"]][
                :128
            ]
            response = {
                "memberships": [m.model_dump(mode="json") for m in members],
                "last_epoch": members[-1].membership.epoch if members else body["after_epoch"],
            }
        elif operation == "history":
            events = [
                e
                for e in server.events
                if e["sequence"] > body["after_sequence"]
                and json.loads(e["receipt"]["body"])["profile_id"] == body["profile_id"]
            ][:128]
            response = {
                "events": copy.deepcopy(events),
                "last_sequence": events[-1]["sequence"] if events else body["after_sequence"],
            }
        elif operation == "checkpoint":
            response = {"checkpoint": server.checkpoint(body["profile_id"])}
        elif operation == "blob_status":
            response = copy.deepcopy(server.blobs[body["revision_id"]])
        elif operation in {"claim", "commit", "release", "takeover"}:
            current = server.checkpoint(body["profile_id"])
            if current != body["expected"]:
                raise RelayConflict("CAS")
            if operation == "claim":
                if current["holder_device_id"] is not None:
                    raise RelayConflict("holder")
                current["checkout_generation"] += 1
                current["holder_device_id"] = self.device_id
            elif operation == "takeover":
                current["checkout_generation"] += 1
                current["holder_device_id"] = self.device_id
            elif operation == "commit":
                if current["holder_device_id"] != self.device_id:
                    raise RelayConflict("holder")
                current["revision_id"] = body["manifest"]["revision_id"]
                server.blobs[current["revision_id"]]["state"] = "committed"
            else:
                if current["holder_device_id"] != self.device_id:
                    raise RelayConflict("holder")
                current["holder_device_id"] = None
            raw = canonical(body)
            receipt = {
                "relay_url": self.root.relay_url,
                "method": "POST",
                "path": f"/v1/owners/{self.root.owner_id}/rpc",
                "owner_id": self.root.owner_id,
                "device_id": self.device_id,
                "timestamp": 100,
                "nonce": str(uuid4()),
                "body_sha256": hashlib.sha256(raw).hexdigest(),
            }
            receipt["signature"] = encode_key(
                self.keys.signing_key.sign(request_signing_bytes(**receipt))
            )
            receipt["body"] = raw.decode()
            event = {"sequence": len(server.events) + 1, "receipt": receipt}
            server.events.append(event)
            server.profiles[body["profile_id"]] = current
            response = {"checkpoint": copy.deepcopy(current), "receipt": copy.deepcopy(receipt)}
            if server.lose_ack == operation:
                server.lose_ack = None
                raise RelayError("lost acknowledgement")
        else:
            raise AssertionError(operation)
        if server.transform:
            response = server.transform(operation, copy.deepcopy(response))
        return response

    def upload(self, revision, source, expected_hash, expected_size):
        if revision in self.server.blobs:
            raise RelayConflict("exists")
        data = source.read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected_hash
        assert len(data) == expected_size
        self.server.blobs[revision] = {
            "revision_id": revision,
            "writer_device_id": self.device_id,
            "ciphertext_sha256": expected_hash,
            "ciphertext_length": expected_size,
            "state": "ready",
        }
        if self.server.lose_ack == "upload":
            self.server.lose_ack = None
            raise RelayError("lost upload acknowledgement")
        return copy.deepcopy(self.server.blobs[revision])


@pytest.fixture
def system(tmp_path, monkeypatch):
    service = SyncService(tmp_path / "owner")
    vault = Vault()
    monkeypatch.setattr(service, "_vault", lambda state, password: vault)
    service.begin_enrollment("owner-user", "https://relay.example", "Owner", "native")
    record, owner = service._read("owner-user")
    service.control.update(
        "installation",
        owner.model_copy(update={"phase": "ready"}).model_dump(mode="json"),
        record.revision,
    )
    authority, device = service._keys(owner, None)
    server = Server(owner.root, owner.membership)
    client = Client(server, owner.device_id, device)
    engine = SyncEngine(service, "owner-user", client)
    return engine, server, authority, tmp_path


def held(system, local="profile1"):
    engine, _, _, _ = system
    profile = engine.enable(local)
    assert engine.claim(local).phase == "apply_required"
    assert not engine.may_launch(local)
    engine.record_applied(local, None)
    assert engine.may_launch(local)
    return profile


def snapshot(system, local="profile1"):
    engine, server, _, path = system
    cp = engine.status(local).checkpoint
    recipients = [("device", d.device_id) for d in server.memberships[-1].membership.devices] + [
        ("recovery", engine.root.recovery_id)
    ]
    revision = str(uuid4())
    data = b"private encrypted bytes" * 4
    source = path / (revision + ".cipher")
    source.write_bytes(data)
    manifest = SnapshotManifest(
        version=1,
        suite=SUITE,
        owner_id=engine.root.owner_id,
        profile_id=cp.profile_id,
        revision_id=revision,
        parent_revision_id=cp.revision_id,
        writer_device_id=engine.device_id,
        membership_epoch=cp.membership_epoch,
        checkout_generation=cp.checkout_generation,
        ciphertext_sha256=hashlib.sha256(data).hexdigest(),
        ciphertext_length=len(data),
        recipients=tuple(
            RecipientEnvelope(
                kind=kind,
                recipient_id=identifier,
                enc=encode_key(bytes(32)),
                wrapped_key=encode_key(bytes(48)),
            )
            for kind, identifier in sorted(recipients)
        ),
    )
    signature = encode_key(engine.client.keys.signing_key.sign(signing_bytes(manifest)))
    return manifest, signature, source


def test_genesis_claim_needs_activation_then_offline_work(system):
    engine, server, _, _ = system
    held(system)
    server.offline = True
    assert engine.may_launch("profile1")
    engine.invalidate("profile1")
    assert not engine.may_launch("profile1")
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert not engine.may_launch("profile1")
    server.offline = False
    assert engine.reconcile("profile1").phase == "apply_required"


def test_publish_handoff_and_release_persist_through_reconciliation(system):
    engine, _, _, _ = system
    held(system)
    engine.begin_handoff("profile1")
    assert not engine.may_launch("profile1")
    with pytest.raises(EngineError):
        engine.release("profile1")
    assert engine.reconcile("profile1").phase == "handoff"
    manifest, signature, source = snapshot(system)
    assert engine.publish("profile1", manifest, signature, source).phase == "handoff"
    assert engine.reconcile("profile1").phase == "handoff"
    assert not engine.may_launch("profile1")
    engine.record_applied("profile1", manifest.revision_id)
    assert not engine.may_launch("profile1")
    assert engine.release("profile1").phase == "released"


@pytest.mark.parametrize("operation", ["claim", "commit", "release"])
def test_crash_after_remote_transition_recovers_from_signed_history(system, operation):
    engine, server, _, _ = system
    if operation == "claim":
        engine.enable("profile1")
        server.lose_ack = operation
        with pytest.raises(RelayError):
            engine.claim("profile1")
    else:
        held(system)
        if operation == "release":
            engine.begin_handoff("profile1")
        manifest, signature, source = snapshot(system)
        if operation == "commit":
            server.lose_ack = operation
            with pytest.raises(RelayError):
                engine.publish("profile1", manifest, signature, source)
        else:
            engine.publish("profile1", manifest, signature, source)
            server.lose_ack = operation
            with pytest.raises(RelayError):
                engine.release("profile1")
    assert not engine.may_launch("profile1")
    assert engine.status("profile1").pending is not None
    reopened = SyncEngine(engine.service, engine.user_id, engine.client)
    state = reopened.reconcile("profile1")
    assert state.pending is None
    assert state.phase == ("released" if operation == "release" else "apply_required")


def test_upload_lost_ack_reuses_original_ciphertext(system):
    engine, server, _, _ = system
    held(system)
    manifest, signature, source = snapshot(system)
    server.lose_ack = "upload"
    with pytest.raises(RelayError):
        engine.publish("profile1", manifest, signature, source)
    pending_id = engine.status("profile1").pending.operation_id
    state = engine.publish("profile1", manifest, signature, source)
    assert state.checkpoint.revision_id == manifest.revision_id
    assert len(server.blobs) == 1
    assert pending_id
    assert source.exists()


def test_committed_upload_retry_returns_reconciled_head(system):
    engine, server, _, _ = system
    held(system)
    manifest, signature, source = snapshot(system)
    server.lose_ack = "commit"
    with pytest.raises(RelayError):
        engine.publish("profile1", manifest, signature, source)
    assert (
        engine.publish("profile1", manifest, signature, source).checkpoint.revision_id
        == manifest.revision_id
    )
    assert len(server.events) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("signature", encode_key(bytes(64))),
        ("body_sha256", "0" * 64),
        ("path", "/v1/owners/wrong/rpc"),
        ("relay_url", "https://evil.example"),
        ("owner_id", str(uuid4())),
        ("device_id", str(uuid4())),
    ],
)
def test_forged_history_never_restores_launch(system, field, value):
    engine, server, _, _ = system
    held(system)
    server.events[0]["receipt"][field] = value
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert not engine.may_launch("profile1")


def test_unsigned_checkpoint_cannot_grant_checkout(system):
    engine, server, _, _ = system
    engine.enable("profile1")

    def transform(operation, response):
        if operation == "checkpoint":
            response["checkpoint"]["holder_device_id"] = engine.device_id
            response["checkpoint"]["checkout_generation"] = 1
        return response

    server.transform = transform
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert not engine.may_launch("profile1")


def test_server_rollback_keeps_highwater_and_encrypted_conflict(system):
    engine, server, _, _ = system
    held(system)
    old_events = copy.deepcopy(server.events)
    old_profiles = copy.deepcopy(server.profiles)
    manifest, signature, source = snapshot(system)
    engine.publish("profile1", manifest, signature, source)
    engine.record_applied("profile1", manifest.revision_id)
    server.events, server.profiles = old_events, old_profiles
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert engine.status("profile1").checkpoint.revision_id == manifest.revision_id
    assert source.exists()
    assert not engine.may_launch("profile1")


def test_fork_at_existing_sequence_preserves_both_receipts(system):
    engine, server, _, _ = system
    held(system)
    old = copy.deepcopy(server.events[0])
    body = json.loads(old["receipt"]["body"])
    # A second signed nonce for the same consent is not silently substituted into
    # already observed history, even when its semantic result happens to match.
    receipt = server.events[0]["receipt"]
    receipt["nonce"] = str(uuid4())
    receipt["signature"] = encode_key(
        engine.client.keys.signing_key.sign(
            request_signing_bytes(
                **{k: v for k, v in receipt.items() if k not in {"signature", "body"}}
            )
        )
    )
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert engine.control.get(f"event/{body['profile_id']}/1").payload == old
    assert len(engine.control.list("conflict/")) == 1


def test_revoked_device_and_membership_fork_fail_closed(system):
    engine, server, authority, _ = system
    held(system)
    new_device = generate_device().certificate(engine.root.owner_id, str(uuid4()))
    successor = approve_membership(
        engine.root, authority.authority_key, (new_device,), previous=server.memberships[0]
    )
    server.memberships.append(successor)
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert engine.control.get("sync/revoked") is not None
    assert not engine.may_launch("profile1")


def test_unresolved_apply_and_missing_reverse_mapping_block_launch(system):
    engine, _, _, _ = system
    profile = held(system)
    engine.control.create("apply/profile1", {"phase": "publishing"})
    assert not engine.may_launch("profile1")
    apply = engine.control.get("apply/profile1")
    engine.control.update(apply.key, {"phase": "complete"}, apply.revision)
    assert engine.may_launch("profile1")
    mapping = engine.control.get("mapping/" + profile)
    engine.control.update(mapping.key, {"local_id": "other"}, mapping.revision)
    assert not engine.may_launch("profile1")


def test_pairing_uses_only_consumed_signed_checkpoint_and_device_keys(system, monkeypatch):
    engine, server, _, directory = system
    profile = held(system)
    recipient_keys = generate_device()
    cert = recipient_keys.certificate(engine.root.owner_id, str(uuid4()))
    request = engine.service.create_pairing_invitation(engine.user_id, cert, now=100)
    approval = engine.service.approve_pairing_invitation(
        engine.user_id,
        request,
        pairing_fingerprint(request),
        now=150,
        checkpoints=engine.pairing_checkpoints(),
    )
    server.memberships.append(approval.membership)
    receiver = SyncService(directory / "receiver")
    vault = Vault()
    monkeypatch.setattr(receiver, "_vault", lambda state, password: vault)
    receiver.consume_pairing_approval(
        "receiver-user",
        engine.root,
        request,
        approval,
        recipient_keys,
        "Receiver",
        "native",
        now=150,
        confirmed_fingerprint=pairing_fingerprint(request),
    )
    receiver_engine = SyncEngine(
        receiver, "receiver-user", Client(server, cert.device_id, recipient_keys)
    )
    with pytest.raises(EngineError):
        receiver_engine.enable("profile2")
    with pytest.raises(EngineError):
        receiver_engine.bind_paired("profile2", str(uuid4()))
    receiver_engine.bind_paired("profile2", profile)
    assert receiver_engine.reconcile("profile2").phase == "held_elsewhere"
    with pytest.raises(EngineError):
        receiver_engine.claim("profile2")
    assert not receiver_engine.may_launch("profile2")
    assert set(vault.slots) == {cert.device_id}


def test_unacknowledged_release_never_restores_offline_launch(system):
    engine, server, _, _ = system
    held(system)
    engine.begin_handoff("profile1")
    engine.publish("profile1", *snapshot(system))
    server.offline = True
    with pytest.raises(RelayError):
        engine.release("profile1")
    assert engine.status("profile1").pending.kind == "release"
    assert not engine.may_launch("profile1")
    server.offline = False
    assert engine.reconcile("profile1").phase == "pending"
    assert not engine.may_launch("profile1")
    assert engine.release("profile1").phase == "released"


def test_tampered_direct_ack_keeps_durable_intent(system):
    engine, server, _, _ = system
    engine.enable("profile1")

    def tamper(operation, response):
        if operation == "claim":
            response["receipt"]["signature"] = encode_key(bytes(64))
        return response

    server.transform = tamper
    with pytest.raises(InvalidSignature):
        engine.claim("profile1")
    assert engine.status("profile1").pending.kind == "claim"
    assert not engine.may_launch("profile1")
    server.transform = None
    assert engine.reconcile("profile1").phase == "apply_required"


def test_membership_rollback_and_signed_fork_are_rejected(system):
    engine, server, authority, _ = system
    held(system)
    first = server.memberships[0]
    new = generate_device().certificate(engine.root.owner_id, str(uuid4()))
    successor = approve_membership(
        engine.root, authority.authority_key, first.membership.devices + (new,), previous=first
    )
    server.memberships.append(successor)
    engine.reconcile("profile1")
    server.memberships = [first]
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    another = generate_device().certificate(engine.root.owner_id, str(uuid4()))
    alternate = approve_membership(
        engine.root, authority.authority_key, first.membership.devices + (another,), previous=first
    )
    server.memberships = [first, alternate]
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert not engine.may_launch("profile1")


def test_stale_recipient_set_cannot_publish_after_membership_change(system):
    engine, server, authority, _ = system
    held(system)
    manifest, signature, source = snapshot(system)
    first = server.memberships[0]
    new = generate_device().certificate(engine.root.owner_id, str(uuid4()))
    server.memberships.append(
        approve_membership(
            engine.root, authority.authority_key, first.membership.devices + (new,), previous=first
        )
    )
    with pytest.raises(EngineError):
        engine.publish("profile1", manifest, signature, source)
    assert not server.blobs
    assert source.exists()


def test_cannot_publish_unapplied_download_or_wrong_activation(system):
    engine, _, _, _ = system
    engine.enable("profile1")
    engine.claim("profile1")
    with pytest.raises(EngineError):
        engine.publish("profile1", *snapshot(system))
    with pytest.raises(EngineError):
        engine.record_applied("profile1", str(uuid4()))
    assert not engine.may_launch("profile1")


def test_pairing_checkpoint_export_requires_online_reconciliation(system):
    engine, server, _, _ = system
    held(system)
    server.offline = True
    with pytest.raises(EngineError):
        engine.pairing_checkpoints()


def test_member_signature_cannot_skip_predecessor_generation(system):
    engine, server, _, _ = system
    held(system)
    event = server.events[0]
    body = json.loads(event["receipt"]["body"])
    body["expected"]["checkout_generation"] = 9
    raw = canonical(body)
    receipt = event["receipt"]
    receipt["body"], receipt["body_sha256"] = raw.decode(), hashlib.sha256(raw).hexdigest()
    receipt["signature"] = encode_key(
        engine.client.keys.signing_key.sign(
            request_signing_bytes(
                **{k: v for k, v in receipt.items() if k not in {"signature", "body"}}
            )
        )
    )
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert engine.status("profile1").checkpoint.checkout_generation == 1


def test_observed_signed_advance_survives_failed_reconcile_then_truncation(system):
    engine, server, _, _ = system
    held(system)
    old_events = copy.deepcopy(server.events)
    old_profiles = copy.deepcopy(server.profiles)
    cp = engine.status("profile1").checkpoint
    # A validly signed release is visible in history before an invalid checkpoint
    # response interrupts reconciliation. The final checkpoint remains old locally.
    engine.client.rpc(
        {
            "operation": "release",
            "profile_id": cp.profile_id,
            "expected": cp.model_dump(mode="json"),
        }
    )

    def invalid_checkpoint(operation, response):
        if operation == "checkpoint":
            response["checkpoint"]["revision_id"] = str(uuid4())
        return response

    server.transform = invalid_checkpoint
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert engine.status("profile1").checkpoint == cp
    assert len(engine.control.list("event/" + cp.profile_id + "/")) == 2
    server.transform = None
    server.events, server.profiles = old_events, old_profiles
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert not engine.may_launch("profile1")


def test_known_signed_fork_stays_blocked_when_original_branch_returns(system):
    engine, server, _, _ = system
    held(system)
    original = copy.deepcopy(server.events)
    receipt = server.events[0]["receipt"]
    receipt["nonce"] = str(uuid4())
    receipt["signature"] = encode_key(
        engine.client.keys.signing_key.sign(
            request_signing_bytes(
                **{k: v for k, v in receipt.items() if k not in {"signature", "body"}}
            )
        )
    )
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    server.events = original
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert engine.status("profile1").forked
    assert not engine.may_launch("profile1")


def test_observed_membership_advance_survives_failed_pagination(system):
    engine, server, authority, _ = system
    held(system)
    first = server.memberships[0]
    second = approve_membership(
        engine.root, authority.authority_key, first.membership.devices, previous=first
    )
    third = approve_membership(
        engine.root, authority.authority_key, first.membership.devices, previous=second
    )
    server.memberships = [first, second, third]

    def partial_membership(operation, response):
        if operation == "memberships":
            if response["memberships"][0]["membership"]["epoch"] == 1:
                return {"memberships": response["memberships"][:2], "last_epoch": 2}
            raise RelayError("interrupted membership history")
        return response

    server.transform = partial_membership
    with pytest.raises(RelayError):
        engine.refresh_membership()
    assert engine.control.get("membership/2") is not None
    assert not engine.may_launch("profile1")
    server.transform = None
    server.memberships = [first]
    with pytest.raises(EngineError):
        engine.reconcile("profile1")
    assert not engine.may_launch("profile1")


def test_offline_fence_never_unlocks_private_vault(system, monkeypatch):
    from camoufox_pm.sync.engine import may_launch_from_control

    engine, server, _, _ = system
    held(system)

    def forbidden(*args, **kwargs):
        pytest.fail("offline fence must not unlock credentials")

    monkeypatch.setattr(engine.service, "_vault", forbidden)
    server.offline = True
    assert may_launch_from_control(engine.service, engine.user_id, "profile1")
    assert not may_launch_from_control(engine.service, "another-user", "profile1")


def test_takeover_requires_explicit_revocation_authority_and_warning(system):
    engine, server, authority, _ = system
    profile = engine.enable("profile1")
    first = server.memberships[0]
    other_keys = generate_device()
    other_cert = other_keys.certificate(engine.root.owner_id, str(uuid4()))
    second = approve_membership(
        engine.root,
        authority.authority_key,
        first.membership.devices + (other_cert,),
        previous=first,
    )
    server.memberships.append(second)
    other = Client(server, other_cert.device_id, other_keys)
    other.rpc({"operation": "claim", "profile_id": profile, "expected": server.checkpoint(profile)})
    assert engine.reconcile("profile1").phase == "held_elsewhere"
    with pytest.raises(EngineError):
        engine.takeover("profile1", authority, warning_ack=True)
    third = approve_membership(
        engine.root, authority.authority_key, first.membership.devices, previous=second
    )
    server.memberships.append(third)
    with pytest.raises(EngineError):
        engine.takeover("profile1", authority, warning_ack=False)
    state = engine.takeover("profile1", authority, warning_ack=True)
    assert state.phase == "apply_required"
    assert state.checkpoint.checkout_generation == 2
    assert state.checkpoint.holder_device_id == engine.device_id
    assert not engine.may_launch("profile1")


def test_claim_rejects_checkpoint_changed_after_preflight(system):
    engine, server, _, _ = system
    engine.enable("profile1")
    selected = engine.status("profile1").checkpoint
    engine.claim("profile1")
    before = list(server.events)
    with pytest.raises(EngineError, match="changed after snapshot preflight"):
        engine.claim("profile1", expected_checkpoint=selected)
    assert server.events == before
    assert engine.status("profile1").pending is None


def test_invitation_newer_membership_anchor_rejects_relay_signed_sibling(system):
    from camoufox_pm.sync.identity import TrustedCheckpoint, canonical_bytes, digest
    from camoufox_pm.sync.profile_invitation import (
        _DOMAIN,
        ProfileInvitation,
        ProfileInvitationBody,
        accept_profile_code,
    )

    engine, server, authority, _ = system
    first = server.memberships[0]
    invited_device = generate_device().certificate(engine.root.owner_id, str(uuid4()))
    relay_device = generate_device().certificate(engine.root.owner_id, str(uuid4()))
    trusted = approve_membership(
        engine.root,
        authority.authority_key,
        first.membership.devices + (invited_device,),
        previous=first,
    )
    sibling = approve_membership(
        engine.root,
        authority.authority_key,
        first.membership.devices + (relay_device,),
        previous=first,
    )
    remote = str(uuid4())
    body = ProfileInvitationBody(
        root_digest=digest(engine.root),
        target_device_id=engine.device_id,
        sequence=1,
        invitation_id=str(uuid4()),
        membership=trusted,
        checkpoints=(
            TrustedCheckpoint(
                profile_id=remote, checkout_generation=0, revision_id=None, holder_device_id=None
            ),
        ),
    )
    pack = ProfileInvitation(
        body=body,
        signature=encode_key(authority.authority_key.sign(_DOMAIN + canonical_bytes(body))),
    )
    accept_profile_code(engine.service, engine.user_id, pack, authorize=lambda: None)
    engine.bind_paired("invited1", remote)
    server.memberships.append(sibling)
    with pytest.raises(EngineError):
        engine.reconcile("invited1")
    assert not engine.may_launch("invited1")
    assert engine.control.get("sync/membership-conflict") is not None


def revoked_holder_history(system, *, refresh_profile=True):
    engine, server, authority, _ = system
    profile = engine.enable("profile1")
    other_keys = generate_device()
    other_id = str(uuid4())
    second = approve_membership(
        engine.root,
        authority.authority_key,
        server.memberships[0].membership.devices
        + (other_keys.certificate(engine.root.owner_id, other_id),),
        previous=server.memberships[0],
    )
    server.memberships.append(second)
    revoked = Client(server, other_id, other_keys)
    revoked.rpc(
        {"operation": "claim", "profile_id": profile, "expected": server.checkpoint(profile)}
    )
    assert engine.reconcile("profile1").phase == "held_elsewhere"
    third = approve_membership(
        engine.root,
        authority.authority_key,
        server.memberships[0].membership.devices,
        previous=second,
    )
    server.memberships.append(third)
    if refresh_profile:
        assert engine.reconcile("profile1").checkpoint.membership_epoch == 3
    else:
        engine.refresh_membership()
        assert engine.status("profile1").checkpoint.membership_epoch == 2
    return revoked, profile


@pytest.mark.parametrize("operation", ["release", "commit"])
@pytest.mark.parametrize("refresh_profile", [False, True])
def test_revoked_old_epoch_advanced_history_cannot_be_projected_to_current_membership(
    system, operation, refresh_profile
):
    engine, server, _, _ = system
    revoked, profile = revoked_holder_history(system, refresh_profile=refresh_profile)
    anchor = engine.status("profile1").checkpoint
    latest = server.memberships.pop()  # Malicious relay accepts old-epoch device requests.
    body = {"operation": operation, "profile_id": profile, "expected": server.checkpoint(profile)}
    if operation == "commit":
        manifest, _, _ = snapshot(system)
        manifest = manifest.model_copy(
            update={"writer_device_id": revoked.device_id, "membership_epoch": 2}
        )
        body.update(
            manifest=manifest.model_dump(mode="json"),
            signature=encode_key(revoked.keys.signing_key.sign(signing_bytes(manifest))),
        )
        server.blobs[manifest.revision_id] = {"state": "ready"}
    revoked.rpc(body)
    server.memberships.append(latest)
    for attempt in range(3):
        if attempt == 2:
            engine = SyncEngine(engine.service, engine.user_id, engine.client)
        with pytest.raises(EngineError):
            engine.reconcile("profile1")
        assert engine.status("profile1").checkpoint == anchor
        assert not engine.may_launch("profile1")


def test_unchanged_historical_head_survives_revocation_and_current_successor_can_endorse_extension(
    system,
):
    engine, server, _, _ = system
    revoked, profile = revoked_holder_history(system)
    assert engine.reconcile("profile1").phase == "held_elsewhere"
    latest = server.memberships.pop()
    revoked.rpc(
        {"operation": "release", "profile_id": profile, "expected": server.checkpoint(profile)}
    )
    server.memberships.append(latest)
    # A trusted current member can independently endorse a historical predecessor;
    # the old-epoch intermediate is not itself treated as a current head.
    engine.client.rpc(
        {"operation": "claim", "profile_id": profile, "expected": server.checkpoint(profile)}
    )
    result = engine.reconcile("profile1")
    assert result.phase == "apply_required"
    assert result.checkpoint.membership_epoch == 3


@pytest.mark.parametrize("tamper", [None, "hash", "holder", "commit"])
def test_expired_upload_restarts_only_unchanged_uncommitted_checkout(system, tamper):
    engine, server, _, _ = system
    remote = held(system)
    manifest, signature, source = snapshot(system)
    server.lose_ack = "upload"
    with pytest.raises(RelayError):
        engine.publish("profile1", manifest, signature, source)
    original = engine.status("profile1").pending
    server.blobs[manifest.revision_id].update(state="expired", ciphertext_length=0)
    if tamper == "hash":
        server.blobs[manifest.revision_id]["ciphertext_sha256"] = "0" * 64
    elif tamper == "holder":
        server.profiles[remote]["holder_device_id"] = None
    elif tamper == "commit":
        # A signed commit always wins over an untrusted expired-status claim.
        engine._send_pending("profile1")
    if tamper in {"hash", "holder"}:
        with pytest.raises(EngineError):
            engine.restart_expired_upload("profile1")
        assert engine.status("profile1").pending == original
    else:
        assert engine.restart_expired_upload("profile1") is (tamper is None)
        assert engine.status("profile1").pending is None
        if tamper is None:
            archived = engine.control.get("expired-upload/" + original.operation_id)
            assert archived.payload["pending"]["ciphertext_path"] == str(source.absolute())
            assert engine.may_launch("profile1")
    assert source.exists()


def test_unavailable_expiry_status_retains_original_upload_intent(system, monkeypatch):
    engine, server, _, _ = system
    held(system)
    manifest, signature, source = snapshot(system)
    server.lose_ack = "upload"
    with pytest.raises(RelayError):
        engine.publish("profile1", manifest, signature, source)
    pending = engine.status("profile1").pending
    rpc = engine.client.rpc

    def missing(payload):
        if payload["operation"] == "blob_status":
            raise RelayError("not found or temporarily unreachable")
        return rpc(payload)

    monkeypatch.setattr(engine.client, "rpc", missing)
    assert engine.restart_expired_upload("profile1") is False
    assert engine.status("profile1").pending == pending
    assert source.exists()
    assert not engine.control.list("expired-upload/")


@pytest.mark.parametrize("race", ["commit", "authorization"])
def test_expired_restart_rechecks_history_and_authorization_before_cas(system, monkeypatch, race):
    engine, server, _, _ = system
    held(system)
    manifest, signature, source = snapshot(system)
    server.lose_ack = "upload"
    with pytest.raises(RelayError):
        engine.publish("profile1", manifest, signature, source)
    pending = engine.status("profile1").pending
    server.blobs[manifest.revision_id].update(state="expired", ciphertext_length=0)
    rpc = engine.client.rpc

    def changed(payload):
        response = rpc(payload)
        if payload["operation"] == "blob_status":
            if race == "commit":
                # A signed commit arrives after the first reconciliation.
                rpc(pending.body)
            else:

                def denied():
                    raise PermissionError("live administrator revoked")

                engine.authorize = denied
        return response

    monkeypatch.setattr(engine.client, "rpc", changed)
    with pytest.raises((EngineError, PermissionError)):
        engine.restart_expired_upload("profile1")
    assert not engine.control.list("expired-upload/")
    assert source.exists()
    state = engine.status("profile1")
    if race == "commit":
        assert state.checkpoint.revision_id == manifest.revision_id
        assert state.pending is None
    else:
        assert state.pending == pending
