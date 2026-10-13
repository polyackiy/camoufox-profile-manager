"""Snapshot cryptography bound to the engine's authenticated receipt history."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

from .crypto import decrypt_file, encrypt_file
from .engine import EngineError, SyncEngine
from .identity import DeviceSecrets, SignedMembership, decode_key, encode_key
from .protocol import SUITE, SnapshotContext, SnapshotManifest


class SnapshotTransfer:
    def __init__(self, engine: SyncEngine, keys: DeviceSecrets):
        self.engine, self.keys = engine, keys
        _, owner = engine._enrollment()
        certificate = keys.certificate(engine.root.owner_id, engine.device_id)
        if certificate not in owner.membership.membership.devices:
            raise EngineError("Snapshot keys do not belong to the enrolled device")

    def encrypt(self, local_id: str, archive: Path, ciphertext: Path):
        state = self.engine.reconcile(local_id)
        if state.pending or state.phase not in {"held", "handoff"}:
            raise EngineError("Snapshot capture requires a confirmed checkout")
        _, owner = self.engine._enrollment()
        recipients = {
            ("device", device.device_id): X25519PublicKey.from_public_bytes(
                decode_key(device.encryption_public)
            )
            for device in owner.membership.membership.devices
        }
        recipients[("recovery", owner.root.recovery_id)] = X25519PublicKey.from_public_bytes(
            decode_key(owner.root.recovery_public)
        )
        checkpoint = state.checkpoint
        template = SnapshotManifest.model_validate(
            {
                "version": 1,
                "suite": SUITE,
                "owner_id": checkpoint.owner_id,
                "profile_id": checkpoint.profile_id,
                "revision_id": str(uuid4()),
                "parent_revision_id": checkpoint.revision_id,
                "writer_device_id": self.engine.device_id,
                "membership_epoch": checkpoint.membership_epoch,
                "checkout_generation": checkpoint.checkout_generation,
                "ciphertext_sha256": "0" * 64,
                "ciphertext_length": 41,
                "recipients": tuple(
                    {
                        "kind": kind,
                        "recipient_id": identity,
                        "enc": encode_key(bytes(32)),
                        "wrapped_key": encode_key(bytes(48)),
                    }
                    for kind, identity in sorted(recipients)
                ),
            }
        )
        manifest, signature = encrypt_file(
            archive, ciphertext, template, recipients, self.keys.signing_key
        )
        return manifest, encode_key(signature)

    def selected(self, local_id: str, *, require_checkout: bool = True):
        """Select only a commit retained after successful signed-history reconciliation."""
        state = self.engine.reconcile(local_id)
        # A pending authority takeover changes holder/generation only, never the
        # selected encrypted head. Read-only preflight may validate that head again
        # before retrying the exact durable takeover; ordinary activation cannot.
        if (state.pending and (require_checkout or state.pending.kind != "takeover")) or (
            require_checkout and state.checkpoint.holder_device_id != self.engine.device_id
        ):
            raise EngineError("Snapshot activation requires a confirmed checkout")
        revision = state.checkpoint.revision_id
        if revision is None:
            raise EngineError("This profile has no published snapshot")
        matches = []
        for record in self.engine.control.list(f"event/{state.checkpoint.profile_id}/"):
            receipt = record.payload["receipt"]
            body = json.loads(receipt["body"])
            if body.get("operation") == "commit" and body["manifest"]["revision_id"] == revision:
                matches.append(body)
        if len(matches) != 1:
            raise EngineError("Selected snapshot has no unique authenticated publication")
        body = matches[0]
        manifest = SnapshotManifest.model_validate(body["manifest"])
        epoch = body["expected"]["membership_epoch"]
        remembered = self.engine.control.get(f"membership/{epoch}")
        if remembered is None:
            raise EngineError("Snapshot membership history is unavailable")
        membership = SignedMembership.model_validate(remembered.payload)
        devices = {d.device_id: d for d in membership.membership.devices}
        writer = devices.get(body["expected"]["holder_device_id"])
        if writer is None:
            raise EngineError("Snapshot writer was not authorized")
        context = SnapshotContext(
            owner_id=state.checkpoint.owner_id,
            profile_id=state.checkpoint.profile_id,
            revision_id=revision,
            parent_revision_id=body["expected"]["revision_id"],
            writer_device_id=writer.device_id,
            membership_epoch=epoch,
            checkout_generation=body["expected"]["checkout_generation"],
            recipient_identities=tuple(
                sorted(
                    [("device", d) for d in devices] + [("recovery", self.engine.root.recovery_id)]
                )
            ),
        )
        return manifest, decode_key(body["signature"], 64), context, writer

    def download(
        self, local_id: str, ciphertext: Path, archive: Path, *, require_checkout: bool = True
    ) -> str:
        manifest, signature, context, writer = self.selected(
            local_id, require_checkout=require_checkout
        )
        self.engine.client.download(
            manifest.revision_id,
            ciphertext,
            manifest.ciphertext_sha256,
            manifest.ciphertext_length,
        )
        decrypt_file(
            ciphertext,
            archive,
            manifest,
            signature,
            context,
            "device",
            self.engine.device_id,
            self.keys.encryption_key,
            Ed25519PublicKey.from_public_bytes(decode_key(writer.signing_public)),
        )
        return manifest.revision_id
