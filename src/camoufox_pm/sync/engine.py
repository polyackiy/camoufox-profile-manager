"""Durable, fail-closed profile transfer coordination over an untrusted relay.

Browser/process proof, encryption, activation and local access roles are supplied
by the integrating manager. This module never treats a relay checkpoint alone as
permission to launch. Receipt signatures establish device consent, not Byzantine
consensus: a malicious relay can still freeze or fork isolated devices.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, field_validator

from camoufox_pm.sync.client import RelayClient, RelayConflict, RelayError
from camoufox_pm.sync.control import ControlMutation, ControlRecord
from camoufox_pm.sync.identity import (
    AuthoritySecrets,
    PairingApproval,
    PairingRequest,
    SignedMembership,
    TrustedCheckpoint,
    canonical_bytes,
    decode_key,
    digest,
    pairing_fingerprint,
    verify_membership,
    verify_pairing,
)
from camoufox_pm.sync.protocol import (
    Counter,
    SnapshotManifest,
    WireModel,
    _uuid,
    parse_manifest,
    signing_bytes,
)
from camoufox_pm.sync.relay import request_signing_bytes, takeover_signing_bytes
from camoufox_pm.sync.service import SyncService

MAX_EVENTS = 10000


def _local_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise EngineError("Invalid local profile identity")
    return value


class EngineError(ValueError):
    """Safe failure; pending encrypted data and protected history are retained."""


def _json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def _pairs(values):
    out = {}
    for key, value in values:
        if key in out:
            raise EngineError("Duplicate relay field")
        out[key] = value
    return out


class Checkpoint(WireModel):
    owner_id: str
    profile_id: str
    membership_epoch: Counter
    checkout_generation: Annotated[int, Field(ge=0, le=2**53 - 1)]
    revision_id: str | None
    holder_device_id: str | None

    _ids = field_validator("owner_id", "profile_id")(_uuid)

    @field_validator("revision_id", "holder_device_id")
    @classmethod
    def optional_id(cls, value):
        return _uuid(value) if value is not None else None


class Receipt(WireModel):
    relay_url: str
    method: Literal["POST"]
    path: Annotated[str, Field(max_length=256)]
    owner_id: str
    device_id: str
    timestamp: Annotated[int, Field(ge=0, le=2**53 - 1)]
    nonce: str
    body_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    signature: Annotated[str, Field(max_length=86)]
    body: Annotated[str, Field(max_length=128 * 1024)]
    _ids = field_validator("owner_id", "device_id", "nonce")(_uuid)


class Pending(WireModel):
    operation_id: str
    kind: Literal["claim", "commit", "release", "takeover"]
    body: dict[str, Any]
    ciphertext_path: str | None = None
    handoff: bool = False
    _id = field_validator("operation_id")(_uuid)


class Binding(WireModel):
    local_id: str
    checkpoint: Checkpoint
    phase: Literal[
        "released",
        "held_elsewhere",
        "held",
        "apply_required",
        "unknown",
        "pending",
        "handoff",
        "conflict",
        "revoked",
    ]
    installed_revision: str | None = None
    installed_generation: Annotated[int, Field(ge=1, le=2**53 - 1)] | None = None
    pending: Pending | None = None
    needs_snapshot: bool = False
    handoff: bool = False
    forked: bool = False
    _id = field_validator("local_id")(_local_id)

    @field_validator("installed_revision")
    @classmethod
    def optional_id(cls, value):
        return _uuid(value) if value is not None else None


class SyncEngine:
    def __init__(self, service: SyncService, user_id: str, client: RelayClient, *, authorize=None):
        self.service, self.user_id, self.client = service, user_id, client
        self.control = service.control
        self.authorize = authorize
        _, state = service._read(user_id)
        if state.phase not in {"ready", "freshness_unknown"}:
            raise EngineError("Verified owner enrollment is required")
        if client.root != state.root or client.device_id != state.device_id:
            raise EngineError("Relay client identity mismatch")
        self.root, self.device_id = state.root, state.device_id

    def _enrollment(self):
        record, state = self.service._read(self.user_id)
        if (
            state.root != self.root
            or state.device_id != self.device_id
            or state.phase not in {"ready", "freshness_unknown"}
        ):
            raise EngineError("Owner enrollment changed")
        return record, state

    def _read(self, local_id: str) -> tuple[ControlRecord, Binding]:
        _local_id(local_id)
        self._enrollment()
        record = self.control.get("profile/" + local_id)
        if record is None:
            raise EngineError("Profile is not synchronized")
        state = Binding.model_validate(record.payload)
        if state.local_id != local_id or state.checkpoint.owner_id != self.root.owner_id:
            raise EngineError("Profile mapping is invalid")
        reverse = self.control.get("mapping/" + state.checkpoint.profile_id)
        if reverse is None or reverse.payload != {"local_id": local_id}:
            raise EngineError("Profile mapping is missing or inconsistent")
        return record, state

    def _save(self, record: ControlRecord, state: Binding) -> Binding:
        checked = Binding.model_validate(state.model_dump())
        self.control.update(record.key, checked.model_dump(mode="json"), record.revision)
        return checked

    def _failure(
        self, local_id: str, phase: Literal["unknown", "conflict", "revoked"] = "unknown"
    ) -> None:
        record, state = self._read(local_id)
        self._save(record, state.model_copy(update={"phase": phase}))

    def enable(self, local_id: str) -> str:
        """Reserve a NEW opaque remote identity. Only original owner can trust genesis."""
        _local_id(local_id)
        owner_record, owner = self._enrollment()
        if not owner.authority or owner.phase != "ready":
            raise EngineError("Only a verified owner can create a synchronized profile")
        profile_id = str(uuid4())
        checkpoint = Checkpoint(
            owner_id=self.root.owner_id,
            profile_id=profile_id,
            membership_epoch=owner.membership.membership.epoch,
            checkout_generation=0,
            revision_id=None,
            holder_device_id=None,
        )
        binding = Binding(local_id=local_id, checkpoint=checkpoint, phase="released")
        self.control.batch(
            (
                ControlMutation(
                    "installation", owner.model_dump(mode="json"), owner_record.revision
                ),
                ControlMutation("profile/" + local_id, binding.model_dump(mode="json")),
                ControlMutation("mapping/" + profile_id, {"local_id": local_id}),
            )
        )
        return profile_id

    def bind_paired(self, local_id: str, profile_id: str) -> None:
        """Use only authority-signed checkpoints already consumed in protected pairing."""
        _local_id(local_id)
        _uuid(profile_id)
        owner_record, owner = self._enrollment()
        matches = []
        for record in self.control.list("received/"):
            value = record.payload
            if value.get("phase") != "consumed" or value.get("local_user_id") != self.user_id:
                continue
            request = PairingRequest.model_validate(value["request"])
            approval = PairingApproval.model_validate(value["approval"])
            if request.device.device_id != self.device_id:
                continue
            verified = verify_pairing(
                self.root,
                request,
                approval,
                approval.membership,
                now=request.issued_at,
                confirmed_fingerprint=pairing_fingerprint(request),
            )
            for cp in verified:
                if cp.profile_id == profile_id:
                    matches.append(
                        Checkpoint(
                            owner_id=self.root.owner_id,
                            profile_id=profile_id,
                            membership_epoch=approval.membership.membership.epoch,
                            checkout_generation=cp.checkout_generation,
                            revision_id=cp.revision_id,
                            holder_device_id=cp.holder_device_id,
                        )
                    )
        from .profile_invitation import trusted_profile_checkpoints

        invited = trusted_profile_checkpoints(self.service, self.user_id).get(profile_id)
        if invited is not None:
            matches.append(Checkpoint.model_validate(invited))
        if not matches or any(cp != matches[0] for cp in matches):
            raise EngineError("No unique independently paired profile checkpoint")
        binding = Binding(local_id=local_id, checkpoint=matches[0], phase="unknown")
        self.control.batch(
            (
                ControlMutation(
                    "installation", owner.model_dump(mode="json"), owner_record.revision
                ),
                ControlMutation("profile/" + local_id, binding.model_dump(mode="json")),
                ControlMutation("mapping/" + profile_id, {"local_id": local_id}),
            )
        )

    def status(self, local_id: str) -> Binding:
        return self._read(local_id)[1]

    def may_launch(self, local_id: str) -> bool:
        return may_launch_from_control(self.service, self.user_id, local_id)

    def invalidate(self, local_id: str) -> None:
        """Update/app-data restore invalidates activation proof, preserving high-water."""
        record, state = self._read(local_id)
        self._save(
            record, state.model_copy(update={"phase": "unknown", "installed_generation": None})
        )

    def _remember_membership(self, membership: SignedMembership) -> None:
        key = "membership/" + str(membership.membership.epoch)
        old = self.control.get(key)
        payload = membership.model_dump(mode="json")
        if old is None:
            self.control.create(key, payload)
        elif old.payload != payload:
            if self.control.get("sync/membership-conflict") is None:
                self.control.create(
                    "sync/membership-conflict",
                    {"epoch": membership.membership.epoch, "candidate_digest": digest(membership)},
                )
            raise EngineError("Signed membership fork")

    def refresh_membership(self) -> dict[int, SignedMembership]:
        if self.control.get("sync/membership-conflict") is not None:
            raise EngineError("Membership fork requires explicit owner recovery")
        owner_record, owner = self._enrollment()
        pinned = owner.membership
        response = self.client.rpc({"operation": "membership"})
        if set(response) != {"root", "membership"} or response["root"] != self.root.model_dump(
            mode="json"
        ):
            raise EngineError("Relay owner substitution")
        latest = SignedMembership.model_validate(response["membership"])
        observed_epoch = max(
            (
                SignedMembership.model_validate(r.payload).membership.epoch
                for r in self.control.list("membership/")
            ),
            default=0,
        )
        if latest.membership.epoch < max(pinned.membership.epoch, observed_epoch):
            raise EngineError("Relay membership rollback")
        # Validate from genesis through the independently pinned membership and all
        # successors. This also retains the historical keys needed for old receipts.
        chain: dict[int, SignedMembership] = {}
        after = 0
        previous = None
        while after < latest.membership.epoch:
            batch = self.client.rpc({"operation": "memberships", "after_epoch": after})
            if (
                set(batch) != {"memberships", "last_epoch"}
                or not isinstance(batch["memberships"], list)
                or not 1 <= len(batch["memberships"]) <= 128
            ):
                raise EngineError("Incomplete membership history")
            for value in batch["memberships"]:
                member = SignedMembership.model_validate(value)
                verify_membership(self.root, member, previous=previous)
                after = member.membership.epoch
                if after > latest.membership.epoch or after > 10000:
                    raise EngineError("Inconsistent membership history")
                if after == pinned.membership.epoch and digest(member) != digest(pinned):
                    self.control.create(
                        "sync/membership-conflict",
                        {"trusted_digest": digest(pinned), "candidate_digest": digest(member)},
                    )
                    raise EngineError("Membership history forks trusted authority")
                chain[after] = member
                if after == pinned.membership.epoch:
                    for known in chain.values():
                        self._remember_membership(known)
                elif after > pinned.membership.epoch:
                    self._remember_membership(member)
                previous = member
            if type(batch["last_epoch"]) is not int or batch["last_epoch"] != after:
                raise EngineError("Invalid membership pagination")
        if previous is None or digest(previous) != digest(latest):
            raise EngineError("Membership endpoint disagrees with history")
        for member in chain.values():
            self._remember_membership(member)
        if latest != pinned:
            updated = owner.model_copy(
                update={"membership": latest, "membership_digest": digest(latest)}
            )
            mutations = [
                ControlMutation(
                    "installation", updated.model_dump(mode="json"), owner_record.revision
                )
            ]
            if self.device_id not in {d.device_id for d in latest.membership.devices}:
                mutations.append(
                    ControlMutation("sync/revoked", {"epoch": latest.membership.epoch})
                )
            self.control.batch(mutations)
        if self.device_id not in {d.device_id for d in latest.membership.devices}:
            raise EngineError("This device has been revoked")
        return chain

    def _transition(
        self, raw: dict, prior: Checkpoint, chain: dict[int, SignedMembership]
    ) -> tuple[Checkpoint, dict]:
        receipt = Receipt.model_validate(raw)
        canonical_bytes(receipt)
        if (
            receipt.relay_url != self.root.relay_url
            or receipt.owner_id != self.root.owner_id
            or receipt.path != f"/v1/owners/{self.root.owner_id}/rpc"
        ):
            raise EngineError("Receipt endpoint or owner mismatch")
        body = json.loads(receipt.body, object_pairs_hook=_pairs)
        if (
            type(body) is not dict
            or hashlib.sha256(receipt.body.encode()).hexdigest() != receipt.body_sha256
        ):
            raise EngineError("Receipt body integrity failed")
        expected = Checkpoint.model_validate(body.get("expected"))
        epoch = expected.membership_epoch
        if epoch < prior.membership_epoch or epoch not in chain:
            raise EngineError("Receipt membership rollback")
        projected = prior.model_copy(update={"membership_epoch": epoch})
        if expected != projected or body.get("profile_id") != prior.profile_id:
            raise EngineError("Receipt predecessor differs from trusted history")
        membership = chain[epoch]
        devices = {d.device_id: d for d in membership.membership.devices}
        signer = devices.get(receipt.device_id)
        if signer is None:
            raise EngineError("Receipt signed by revoked or unknown device")
        Ed25519PublicKey.from_public_bytes(decode_key(signer.signing_public)).verify(
            decode_key(receipt.signature, 64),
            request_signing_bytes(
                receipt.relay_url,
                receipt.method,
                receipt.path,
                receipt.owner_id,
                receipt.device_id,
                receipt.timestamp,
                receipt.nonce,
                receipt.body_sha256,
            ),
        )
        operation = body.get("operation")
        fields = {"operation", "profile_id", "expected"}
        update: dict[str, Any] = {}
        if operation == "claim":
            if expected.holder_device_id is not None:
                raise EngineError("Claim stole a held checkout")
            update = {
                "checkout_generation": expected.checkout_generation + 1,
                "holder_device_id": receipt.device_id,
            }
        elif operation == "release":
            if expected.holder_device_id != receipt.device_id:
                raise EngineError("Release not authorized by holder")
            update = {"holder_device_id": None}
        elif operation == "takeover":
            fields |= {"operation_id", "warning_ack", "authority_signature"}
            if (
                body.get("warning_ack") is not True
                or expected.holder_device_id is None
                or expected.holder_device_id in devices
            ):
                raise EngineError("Takeover requires owner-approved revocation")
            Ed25519PublicKey.from_public_bytes(decode_key(self.root.authority_public)).verify(
                decode_key(body["authority_signature"], 64),
                takeover_signing_bytes(
                    self.root.owner_id,
                    prior.profile_id,
                    expected.model_dump(mode="json"),
                    body["operation_id"],
                    receipt.device_id,
                ),
            )
            update = {
                "checkout_generation": expected.checkout_generation + 1,
                "holder_device_id": receipt.device_id,
            }
        elif operation == "commit":
            fields |= {"manifest", "signature"}
            manifest = parse_manifest(_json(body["manifest"]))
            recipients = sorted(
                [("device", d) for d in devices] + [("recovery", self.root.recovery_id)]
            )
            if (
                expected.holder_device_id != receipt.device_id
                or manifest.owner_id != self.root.owner_id
                or manifest.profile_id != prior.profile_id
                or manifest.writer_device_id != receipt.device_id
                or manifest.membership_epoch != epoch
                or manifest.checkout_generation != expected.checkout_generation
                or manifest.parent_revision_id != expected.revision_id
                or [(r.kind, r.recipient_id) for r in manifest.recipients] != recipients
            ):
                raise EngineError("Snapshot publication binding failed")
            Ed25519PublicKey.from_public_bytes(decode_key(signer.signing_public)).verify(
                decode_key(body["signature"], 64), signing_bytes(manifest)
            )
            update = {"revision_id": manifest.revision_id}
        else:
            raise EngineError("Unknown history transition")
        if set(body) != fields:
            raise EngineError("Unexpected receipt fields")
        return Checkpoint.model_validate({**expected.model_dump(), **update}), body

    @staticmethod
    def _same_position(a: Checkpoint, b: Checkpoint) -> bool:
        return a.model_dump(exclude={"membership_epoch"}) == b.model_dump(
            exclude={"membership_epoch"}
        )

    def reconcile(self, local_id: str) -> Binding:
        record, state = self._read(local_id)
        if state.forked:
            raise EngineError("Signed history fork requires explicit owner recovery")
        self._save(record, state.model_copy(update={"phase": "unknown"}))
        try:
            chain = self.refresh_membership()
            record, state = self._read(local_id)
            profile = state.checkpoint.profile_id
            checkpoint = Checkpoint(
                owner_id=self.root.owner_id,
                profile_id=profile,
                membership_epoch=1,
                checkout_generation=0,
                revision_id=None,
                holder_device_id=None,
            )
            seen_anchor = self._same_position(checkpoint, state.checkpoint)
            matched_pending = False
            after = count = 0
            observed_event_keys: set[str] = set()
            while True:
                response = self.client.rpc(
                    {"operation": "history", "profile_id": profile, "after_sequence": after}
                )
                if (
                    set(response) != {"events", "last_sequence"}
                    or not isinstance(response["events"], list)
                    or len(response["events"]) > 128
                ):
                    raise EngineError("Invalid relay history")
                if not response["events"]:
                    if (
                        type(response["last_sequence"]) is not int
                        or response["last_sequence"] != after
                    ):
                        raise EngineError("Invalid empty history cursor")
                    break
                for event in response["events"]:
                    if (
                        type(event) is not dict
                        or set(event) != {"sequence", "receipt"}
                        or type(event["sequence"]) is not int
                        or not after < event["sequence"] <= 2**53 - 1
                    ):
                        raise EngineError("History replay or invalid sequence")
                    checkpoint, body = self._transition(event["receipt"], checkpoint, chain)
                    after = event["sequence"]
                    count += 1
                    if count > MAX_EVENTS:
                        raise EngineError("History exceeds its resource bound")
                    key = f"event/{profile}/{after}"
                    observed_event_keys.add(key)
                    remembered = self.control.get(key)
                    if remembered is None:
                        self.control.create(key, event)
                    elif remembered.payload != event:
                        self.control.create(
                            "conflict/" + str(uuid4()), {"local_id": local_id, "event": event}
                        )
                        self._save(
                            record, state.model_copy(update={"phase": "conflict", "forked": True})
                        )
                        raise EngineError("Relay history fork")
                    seen_anchor |= self._same_position(checkpoint, state.checkpoint)
                    if (
                        state.pending
                        and body == state.pending.body
                        and event["receipt"]["device_id"] == self.device_id
                    ):
                        matched_pending = True
                if type(response["last_sequence"]) is not int or response["last_sequence"] != after:
                    raise EngineError("Invalid history pagination")
            # Individually authenticated receipts survive a later response failure.
            # A relay must not erase such observed advancement by returning an old
            # otherwise-valid prefix on the next attempt.
            remembered_keys = {r.key for r in self.control.list("event/" + profile + "/")}
            if not remembered_keys.issubset(observed_event_keys):
                raise EngineError("Relay omitted previously observed signed history")
            if not seen_anchor:
                if checkpoint.checkout_generation >= state.checkpoint.checkout_generation:
                    self._save(
                        record, state.model_copy(update={"phase": "conflict", "forked": True})
                    )
                raise EngineError("History does not descend from trusted checkpoint")
            current_epoch = max(chain)
            # A relay cannot make a newly advanced old-epoch head current merely
            # by projecting it onto newer membership. Once revocation is known,
            # require a current-member signed transition endorsing any advancement
            # beyond our independent anchor. Historical intermediate receipts are
            # allowed when such a successor authenticates their resulting head.
            # Compare positions, not remembered-event presence: candidates saved
            # during a failed reconciliation must not bypass this gate on retry.
            if (
                not self._same_position(checkpoint, state.checkpoint)
                and checkpoint.membership_epoch < current_epoch
            ):
                raise EngineError("Advanced history needs current-membership confirmation")
            checkpoint = checkpoint.model_copy(update={"membership_epoch": current_epoch})
            remote = self.client.rpc({"operation": "checkpoint", "profile_id": profile})
            if (
                set(remote) != {"checkpoint"}
                or Checkpoint.model_validate(remote["checkpoint"]) != checkpoint
            ):
                raise EngineError("Relay checkpoint disagrees with signed history")
            pending = state.pending
            needs_snapshot = state.needs_snapshot
            handoff = state.handoff
            if pending is not None and not matched_pending:
                phase = "pending"
            elif pending is not None and pending.kind == "commit" and pending.handoff:
                phase, needs_snapshot, pending = "handoff", False, None
            else:
                pending = None
                phase = self._phase(state, checkpoint)
                if state.handoff and checkpoint.holder_device_id == self.device_id:
                    phase = "handoff"
            if checkpoint.holder_device_id is None and pending is None:
                handoff = False
            return self._save(
                record,
                state.model_copy(
                    update={
                        "checkpoint": checkpoint,
                        "phase": phase,
                        "pending": pending,
                        "needs_snapshot": needs_snapshot,
                        "handoff": handoff,
                    }
                ),
            )
        except Exception:
            # A durable intent and every previously observed receipt remain intact.
            # Even a previously held profile is blocked after contradictory history.
            try:
                self._failure(local_id, "conflict")
            except Exception:
                pass
            raise EngineError("Synchronization history could not be reconciled") from None

    def _phase(self, state: Binding, checkpoint: Checkpoint) -> str:
        if checkpoint.holder_device_id is None:
            return "released"
        if checkpoint.holder_device_id != self.device_id:
            return "held_elsewhere"
        if (
            state.installed_generation == checkpoint.checkout_generation
            and state.installed_revision == checkpoint.revision_id
        ):
            return "held"
        return "apply_required"

    def _intent(
        self,
        local_id: str,
        kind: Literal["claim", "commit", "release", "takeover"],
        body: dict,
        *,
        ciphertext: Path | None = None,
        handoff: bool = False,
    ) -> Pending:
        record, state = self._read(local_id)
        if state.pending is not None:
            if state.pending.kind != kind or state.pending.body != body:
                raise EngineError("A different transfer is already pending")
            return state.pending
        pending = Pending(
            operation_id=body.get("operation_id", str(uuid4())),
            kind=kind,
            body=body,
            ciphertext_path=str(ciphertext.absolute()) if ciphertext else None,
            handoff=handoff,
        )
        self._save(record, state.model_copy(update={"phase": "pending", "pending": pending}))
        return pending

    def _send_pending(self, local_id: str) -> Binding:
        _, state = self._read(local_id)
        if state.pending is None:
            raise EngineError("No durable request is pending")
        # Reconciliation validates exact signed receipts, including a lost ACK. A
        # conflict can mean the request already committed, never permission to steal.
        try:
            if self.authorize is not None:
                self.authorize()
            reply = self.client.rpc(state.pending.body)
            if set(reply) != {"checkpoint", "receipt"}:
                raise EngineError("Invalid transition acknowledgement")
            chain = {
                int(r.key.split("/")[1]): SignedMembership.model_validate(r.payload)
                for r in self.control.list("membership/")
            }
            derived, body = self._transition(reply["receipt"], state.checkpoint, chain)
            if (
                body != state.pending.body
                or reply["receipt"]["device_id"] != self.device_id
                or Checkpoint.model_validate(reply["checkpoint"]) != derived
            ):
                raise EngineError("Transition acknowledgement differs from durable intent")
        except RelayConflict:
            pass
        return self.reconcile(local_id)

    def claim(self, local_id: str, *, expected_checkpoint: Checkpoint | None = None) -> Binding:
        state = self.reconcile(local_id)
        if expected_checkpoint is not None and state.checkpoint != Checkpoint.model_validate(
            expected_checkpoint.model_dump()
        ):
            raise EngineError("Profile changed after snapshot preflight; retry")
        if state.pending:
            if state.pending.kind != "claim":
                raise EngineError("Another operation is pending")
            return self._send_pending(local_id)
        if state.checkpoint.holder_device_id is not None:
            raise EngineError("Profile is checked out on a device")
        self._intent(
            local_id,
            "claim",
            {
                "operation": "claim",
                "profile_id": state.checkpoint.profile_id,
                "expected": state.checkpoint.model_dump(mode="json"),
            },
        )
        return self._send_pending(local_id)

    def record_applied(self, local_id: str, revision_id: str | None) -> Binding:
        """Manager calls only after journalled activation/quiescence verified exact head."""
        record, state = self._read(local_id)
        if (
            state.pending is not None
            or state.phase not in {"apply_required", "handoff"}
            or state.checkpoint.holder_device_id != self.device_id
            or revision_id != state.checkpoint.revision_id
        ):
            raise EngineError("Activation does not match confirmed checkout")
        return self._save(
            record,
            state.model_copy(
                update={
                    "installed_revision": revision_id,
                    "installed_generation": state.checkpoint.checkout_generation,
                    "phase": "handoff" if state.phase == "handoff" else "held",
                }
            ),
        )

    def begin_handoff(self, local_id: str) -> None:
        record, state = self._read(local_id)
        if not self.may_launch(local_id):
            raise EngineError("Handoff requires confirmed local checkout")
        self._save(
            record,
            state.model_copy(update={"phase": "handoff", "needs_snapshot": True, "handoff": True}),
        )

    def publish(
        self, local_id: str, manifest: SnapshotManifest, signature: str, ciphertext: Path
    ) -> Binding:
        before = self.status(local_id)
        handoff = before.handoff
        state = self.reconcile(local_id)
        if (
            state.pending is None
            and state.checkpoint.revision_id == manifest.revision_id
            and before.pending
            and before.pending.kind == "commit"
            and before.pending.body["manifest"] == manifest.model_dump(mode="json")
        ):
            return state
        if state.checkpoint.holder_device_id != self.device_id or (
            state.pending is None and state.phase not in {"held", "handoff"}
        ):
            raise EngineError("Publication requires confirmed checkout")
        _, owner = self._enrollment()
        devices = {d.device_id: d for d in owner.membership.membership.devices}
        checked = parse_manifest(_json(manifest.model_dump(mode="json")))
        expected = state.checkpoint
        if (
            checked.owner_id != self.root.owner_id
            or checked.profile_id != expected.profile_id
            or checked.writer_device_id != self.device_id
            or checked.parent_revision_id != expected.revision_id
            or checked.checkout_generation != expected.checkout_generation
            or checked.membership_epoch != expected.membership_epoch
            or [(r.kind, r.recipient_id) for r in checked.recipients]
            != sorted([("device", d) for d in devices] + [("recovery", self.root.recovery_id)])
        ):
            raise EngineError("Encrypted snapshot does not match current checkout/membership")
        Ed25519PublicKey.from_public_bytes(
            decode_key(devices[self.device_id].signing_public)
        ).verify(decode_key(signature, 64), signing_bytes(checked))
        body = {
            "operation": "commit",
            "profile_id": expected.profile_id,
            "manifest": checked.model_dump(mode="json"),
            "signature": signature,
            "expected": expected.model_dump(mode="json"),
        }
        pending = self._intent(local_id, "commit", body, ciphertext=ciphertext, handoff=handoff)
        if pending.ciphertext_path != str(ciphertext.absolute()):
            raise EngineError("Retry must use the original encrypted file")
        try:
            if self.authorize is not None:
                self.authorize()
            self.client.upload(
                checked.revision_id,
                ciphertext,
                checked.ciphertext_sha256,
                checked.ciphertext_length,
            )
        except RelayConflict:
            status = self.client.rpc(
                {"operation": "blob_status", "revision_id": checked.revision_id}
            )
            if status != {
                "revision_id": checked.revision_id,
                "writer_device_id": self.device_id,
                "ciphertext_sha256": checked.ciphertext_sha256,
                "ciphertext_length": checked.ciphertext_length,
                "state": "ready",
            }:
                raise EngineError("Encrypted upload is uncertain; original file retained") from None
        return self._send_pending(local_id)

    def restart_expired_upload(self, local_id: str) -> bool:
        """Retire only an expired, uncommitted intent under the same checkout.

        The relay's expiry claim never establishes ownership. Signed history is
        reconciled on both sides of the status read, and the original ciphertext
        and intent remain retained for recovery.
        """
        state = self.reconcile(local_id)
        pending = state.pending
        if pending is None or pending.kind != "commit":
            return False
        manifest = SnapshotManifest.model_validate(pending.body["manifest"])
        try:
            status = self.client.rpc(
                {"operation": "blob_status", "revision_id": manifest.revision_id}
            )
        except RelayError:
            # A missing/inaccessible upload proves no expiry. Retry only the
            # original intent and bytes; never mint a replacement on uncertainty.
            return False
        if status.get("state") != "expired":
            return False
        if status != {
            "revision_id": manifest.revision_id,
            "writer_device_id": self.device_id,
            "ciphertext_sha256": manifest.ciphertext_sha256,
            "ciphertext_length": 0,
            "state": "expired",
        }:
            raise EngineError("Expired upload identity is uncertain; original file retained")
        self.reconcile(local_id)
        record, state = self._read(local_id)
        if (
            state.pending != pending
            or state.forked
            or state.checkpoint.model_dump(mode="json") != pending.body["expected"]
            or state.checkpoint.holder_device_id != self.device_id
            or state.installed_revision != state.checkpoint.revision_id
            or state.installed_generation != state.checkpoint.checkout_generation
        ):
            raise EngineError("Expired upload checkout changed; original file retained")
        if self.authorize is not None:
            self.authorize()
        replacement = state.model_copy(
            update={
                "pending": None,
                "phase": "handoff" if state.handoff else "held",
                "needs_snapshot": state.handoff,
            }
        )
        self.control.batch(
            (
                ControlMutation(
                    "expired-upload/" + pending.operation_id,
                    {"local_id": local_id, "pending": pending.model_dump(mode="json")},
                    None,
                ),
                ControlMutation(record.key, replacement.model_dump(mode="json"), record.revision),
            )
        )
        return True

    def release(self, local_id: str) -> Binding:
        _, state = self._read(local_id)
        if state.pending:
            if state.pending.kind != "release":
                raise EngineError("Another operation is pending")
            return self._send_pending(local_id)
        if state.phase != "handoff" or state.needs_snapshot:
            raise EngineError("Close and publish a handoff snapshot before release")
        self._intent(
            local_id,
            "release",
            {
                "operation": "release",
                "profile_id": state.checkpoint.profile_id,
                "expected": state.checkpoint.model_dump(mode="json"),
            },
            handoff=True,
        )
        return self._send_pending(local_id)

    def takeover(
        self,
        local_id: str,
        authority: AuthoritySecrets,
        *,
        warning_ack: bool,
        expected_checkpoint: Checkpoint | None = None,
    ) -> Binding:
        """Explicit lost-device recovery AFTER signed revocation, never a timer.

        The old browser may still run offline. The integrating owner UI must state
        this and preserve unsent candidates; this operation only fences publication.
        """
        if warning_ack is not True:
            raise EngineError("Lost-device takeover warning must be acknowledged")
        state = self.reconcile(local_id)
        if expected_checkpoint is not None and state.checkpoint != Checkpoint.model_validate(
            expected_checkpoint.model_dump()
        ):
            raise EngineError("Profile changed after recovery preflight; retry")
        if state.pending:
            if state.pending.kind != "takeover":
                raise EngineError("Another operation is pending")
            return self._send_pending(local_id)
        _, owner = self._enrollment()
        holder = state.checkpoint.holder_device_id
        if (
            not owner.authority
            or holder is None
            or holder in {d.device_id for d in owner.membership.membership.devices}
        ):
            raise EngineError("Revoke the lost holder before owner takeover")
        from camoufox_pm.sync.identity import encode_key

        if (
            encode_key(authority.authority_key.public_key().public_bytes_raw())
            != self.root.authority_public
        ):
            raise EngineError("Wrong owner authority")
        operation_id = str(uuid4())
        expected = state.checkpoint.model_dump(mode="json")
        signature = encode_key(
            authority.authority_key.sign(
                takeover_signing_bytes(
                    self.root.owner_id,
                    state.checkpoint.profile_id,
                    expected,
                    operation_id,
                    self.device_id,
                )
            )
        )
        body = {
            "operation": "takeover",
            "profile_id": state.checkpoint.profile_id,
            "expected": expected,
            "operation_id": operation_id,
            "warning_ack": True,
            "authority_signature": signature,
        }
        self._intent(local_id, "takeover", body)
        return self._send_pending(local_id)

    def pairing_checkpoints(self) -> tuple[TrustedCheckpoint, ...]:
        """Read protected reconciled profile heads; never accept HTTP-supplied heads."""
        result = []
        for record in self.control.list("profile/"):
            local = Binding.model_validate(record.payload).local_id
            state = self.reconcile(local)
            if state.phase not in {"held", "released", "held_elsewhere"} or state.pending:
                raise EngineError("Reconcile all profiles before pairing")
            cp = state.checkpoint
            result.append(
                TrustedCheckpoint(
                    profile_id=cp.profile_id,
                    revision_id=cp.revision_id,
                    checkout_generation=cp.checkout_generation,
                    holder_device_id=cp.holder_device_id,
                )
            )
        return tuple(sorted(result, key=lambda c: c.profile_id))


def may_launch_from_control(service: SyncService, user_id: str, local_id: str) -> bool:
    """Offline key-free fence. Caller additionally checks live local role, data
    activation journal/DB pointer, process locks and application maintenance gate.
    Missing, corrupted or uncertain state is never local-launch permission.
    """
    try:
        _local_id(local_id)
        lifecycle = service.control.get("lifecycle/" + local_id)
        if lifecycle is not None and (
            lifecycle.payload.get("state") != "active"
            or lifecycle.payload.get("phase") != "complete"
        ):
            return False
        restore_fence = service.control.get("sync/restore-fence")
        if restore_fence is not None and restore_fence.payload.get("phase") != "complete":
            return False
        _, owner = service._read(user_id)
        if owner.phase not in {"ready", "freshness_unknown"}:
            return False
        record = service.control.get("profile/" + local_id)
        if record is None:
            return False
        state = Binding.model_validate(record.payload)
        if state.local_id != local_id or state.checkpoint.owner_id != owner.root.owner_id:
            return False
        mapping = service.control.get("mapping/" + state.checkpoint.profile_id)
        if mapping is None or mapping.payload != {"local_id": local_id}:
            return False
        apply_record = service.control.get("apply/" + local_id)
        if apply_record is not None and apply_record.payload.get("phase") not in {
            "complete",
            "aborted",
        }:
            return False
        for member_record in service.control.list("membership/"):
            member = SignedMembership.model_validate(member_record.payload)
            if member.membership.epoch > owner.membership.membership.epoch:
                return False
            if (
                member.membership.epoch == owner.membership.membership.epoch
                and digest(member) != owner.membership_digest
            ):
                return False
        return (
            state.phase == "held"
            and not state.forked
            and not state.handoff
            and state.pending is None
            and state.checkpoint.holder_device_id == owner.device_id
            and state.installed_generation == state.checkpoint.checkout_generation
            and state.installed_revision == state.checkpoint.revision_id
            and owner.device_id in {d.device_id for d in owner.membership.membership.devices}
            and service.control.get("sync/revoked") is None
            and service.control.get("sync/membership-conflict") is None
        )
    except Exception:
        return False
