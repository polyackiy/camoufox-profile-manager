"""Explicit owner-authorized ciphertext retention, never a freshness proof."""

from __future__ import annotations

from typing import Annotated

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, field_validator, model_validator

from .identity import OwnerRoot, canonical_bytes, decode_key, digest
from .protocol import Counter, SnapshotManifest, WireModel, _uuid

GRACE_SECONDS = 7 * 24 * 60 * 60
_DOMAIN = b"camoufox-pm/sync/experimental-v1/retention\0"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RetainedRevision(WireModel):
    revision_id: str
    profile_id: str
    manifest_sha256: Hash
    _ids = field_validator("revision_id", "profile_id")(_uuid)


class RetentionBody(WireModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    owner_id: str
    root_digest: Hash
    decision_id: str
    issued_at: Counter
    not_before: Counter
    revisions: Annotated[tuple[RetainedRevision, ...], Field(min_length=1, max_length=64)]
    _ids = field_validator("owner_id", "decision_id")(_uuid)

    @field_validator("revisions", mode="before")
    @classmethod
    def array(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def ordered(self):
        ids = [item.revision_id for item in self.revisions]
        if ids != sorted(set(ids)) or self.not_before < self.issued_at + GRACE_SECONDS:
            raise ValueError("Invalid retention decision")
        return self


class SignedRetention(WireModel):
    body: RetentionBody
    signature: Annotated[str, Field(min_length=86, max_length=86)]


class CancellationBody(WireModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    owner_id: str
    root_digest: Hash
    decision_id: str
    decision_digest: Hash
    issued_at: Counter
    _ids = field_validator("owner_id", "decision_id")(_uuid)


class SignedCancellation(WireModel):
    body: CancellationBody
    signature: Annotated[str, Field(min_length=86, max_length=86)]


def retention_signing_bytes(body: RetentionBody) -> bytes:
    return _DOMAIN + b"schedule\0" + canonical_bytes(body)


def cancellation_signing_bytes(body: CancellationBody) -> bytes:
    return _DOMAIN + b"cancel\0" + canonical_bytes(body)


def verify_retention(root: OwnerRoot, decision: SignedRetention) -> None:
    if decision.body.owner_id != root.owner_id or decision.body.root_digest != digest(root):
        raise ValueError("Invalid retention decision")
    Ed25519PublicKey.from_public_bytes(decode_key(root.authority_public)).verify(
        decode_key(decision.signature, 64), retention_signing_bytes(decision.body)
    )


def verify_cancellation(
    root: OwnerRoot, cancellation: SignedCancellation, decision: SignedRetention
) -> None:
    body = cancellation.body
    if (
        body.owner_id != root.owner_id
        or body.root_digest != digest(root)
        or body.decision_id != decision.body.decision_id
        or body.decision_digest != digest(decision)
    ):
        raise ValueError("Invalid retention cancellation")
    Ed25519PublicKey.from_public_bytes(decode_key(root.authority_public)).verify(
        decode_key(cancellation.signature, 64), cancellation_signing_bytes(body)
    )


def verify_collected(
    root: OwnerRoot, evidence: dict, revision_id: str, expected_hash: str, expected_size: int
) -> SignedRetention:
    """Authenticate why old bytes are unavailable; never establish a new head."""
    if set(evidence) != {"retention", "manifest", "signature"}:
        raise ValueError("Invalid retention evidence")
    decision = SignedRetention.model_validate(evidence["retention"])
    verify_retention(root, decision)
    manifest = SnapshotManifest.model_validate(evidence["manifest"])
    selected = next((r for r in decision.body.revisions if r.revision_id == revision_id), None)
    if (
        selected is None
        or selected.manifest_sha256 != digest(manifest)
        or selected.profile_id != manifest.profile_id
        or manifest.owner_id != root.owner_id
        or manifest.revision_id != revision_id
        or manifest.ciphertext_sha256 != expected_hash
        or manifest.ciphertext_length != expected_size
    ):
        raise ValueError("Invalid retention evidence")
    return decision
