"""Experimental bounded snapshot wire format; not a synchronization service.

Parsing and context checks do not authenticate a manifest. The caller must verify
its device signature and independently authenticated membership/checkpoint first.
"""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_MANIFEST_BYTES = 128 * 1024
MAX_CIPHERTEXT_BYTES = 2 * 1024**3 + 1024**2
MAX_COUNTER = 2**53 - 1
MAX_RECIPIENTS = 129
SUITE = "secretstream-xchacha20poly1305-hpke-x25519-sha256-chacha20poly1305-v1"
SIGNING_DOMAIN = b"camoufox-pm/sync/experimental-v1/manifest\x00"
ENCRYPTION_DOMAIN = b"camoufox-pm/sync/experimental-v1/snapshot\x00"

Counter = Annotated[int, Field(strict=True, ge=1, le=MAX_COUNTER)]


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("identity must be a canonical lowercase UUID")
    if UUID(value).int == 0:
        raise ValueError("nil identity is not allowed")
    return value


def _base64(value: str, length: int) -> str:
    """Require the unique unpadded URL-safe encoding, including unused tail bits."""
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid envelope encoding") from exc
    if len(raw) != length or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise ValueError("invalid envelope length or noncanonical encoding")
    return value


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RecipientEnvelope(WireModel):
    kind: Literal["device", "recovery"]
    recipient_id: str
    enc: Annotated[str, Field(max_length=43)]
    wrapped_key: Annotated[str, Field(max_length=64)]

    _identity = field_validator("recipient_id")(_uuid)

    @field_validator("enc")
    @classmethod
    def valid_enc(cls, value: str) -> str:
        return _base64(value, 32)

    @field_validator("wrapped_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        return _base64(value, 48)


class SnapshotManifest(WireModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)]
    suite: Literal["secretstream-xchacha20poly1305-hpke-x25519-sha256-chacha20poly1305-v1"]
    owner_id: str
    profile_id: str
    revision_id: str
    parent_revision_id: str | None
    writer_device_id: str
    membership_epoch: Counter
    checkout_generation: Counter
    ciphertext_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ciphertext_length: Annotated[int, Field(strict=True, ge=41, le=MAX_CIPHERTEXT_BYTES)]
    recipients: Annotated[
        tuple[RecipientEnvelope, ...], Field(min_length=2, max_length=MAX_RECIPIENTS)
    ]

    _identities = field_validator("owner_id", "profile_id", "revision_id", "writer_device_id")(
        _uuid
    )

    @field_validator("parent_revision_id")
    @classmethod
    def valid_parent(cls, value: str | None) -> str | None:
        return _uuid(value) if value is not None else None

    @field_validator("recipients", mode="before")
    @classmethod
    def wire_array(cls, value: Any) -> Any:
        # JSON arrays are lists; immutable tuples prevent mutation after validation.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def coherent(self) -> SnapshotManifest:
        if self.parent_revision_id == self.revision_id:
            raise ValueError("revision cannot be its own parent")
        identities = [(r.kind, r.recipient_id) for r in self.recipients]
        if identities != sorted(identities) or len(set(identities)) != len(identities):
            raise ValueError("recipients must be ordered and unique")
        if len({r.recipient_id for r in self.recipients}) != len(self.recipients):
            raise ValueError("recipient identity cannot have multiple kinds")
        if sum(r.kind == "recovery" for r in self.recipients) != 1:
            raise ValueError("exactly one recovery recipient is required")
        if ("device", self.writer_device_id) not in identities:
            raise ValueError("writer must be a recipient")
        return self


class SnapshotContext(WireModel):
    """Values from authenticated local state, never copied from incoming metadata."""

    owner_id: str
    profile_id: str
    revision_id: str
    parent_revision_id: str | None
    writer_device_id: str
    membership_epoch: Counter
    checkout_generation: Counter
    recipient_identities: tuple[tuple[Literal["device", "recovery"], str], ...]

    _identities = field_validator("owner_id", "profile_id", "revision_id", "writer_device_id")(
        _uuid
    )

    @field_validator("parent_revision_id")
    @classmethod
    def valid_parent(cls, value: str | None) -> str | None:
        return _uuid(value) if value is not None else None


def validate_context(manifest: SnapshotManifest, expected: SnapshotContext) -> None:
    for name in (
        "owner_id",
        "profile_id",
        "revision_id",
        "parent_revision_id",
        "writer_device_id",
        "membership_epoch",
        "checkout_generation",
    ):
        if getattr(manifest, name) != getattr(expected, name):
            raise ValueError(f"snapshot context mismatch: {name}")
    if (
        tuple((r.kind, r.recipient_id) for r in manifest.recipients)
        != expected.recipient_identities
    ):
        raise ValueError("snapshot recipient membership mismatch")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def canonical_bytes(manifest: SnapshotManifest) -> bytes:
    return _canonical(manifest.model_dump(mode="json"))


def signing_bytes(manifest: SnapshotManifest) -> bytes:
    return SIGNING_DOMAIN + canonical_bytes(manifest)


def encryption_context_bytes(manifest: SnapshotManifest) -> bytes:
    """Stable stream AAD/HPKE info: excludes values produced by encryption itself.

    Recipient identities remain bound here; their envelopes and the ciphertext
    digest/length are additionally bound by the final manifest signature.
    """
    values = manifest.model_dump(
        mode="json", exclude={"ciphertext_sha256", "ciphertext_length", "recipients"}
    )
    values["recipient_identities"] = [[r.kind, r.recipient_id] for r in manifest.recipients]
    return ENCRYPTION_DOMAIN + _canonical(values)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_number(value: str) -> Any:
    raise ValueError("only integer JSON numbers are allowed")


def parse_manifest(data: bytes) -> SnapshotManifest:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_MANIFEST_BYTES:
        raise ValueError("manifest byte limit exceeded")
    # Check nesting before invoking the recursive JSON parser, respecting strings.
    depth = 0
    quoted = escaped = False
    for char in data:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > 8:
                raise ValueError("manifest nesting limit exceeded")
        elif char in (93, 125):
            depth -= 1
    try:
        values = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid manifest JSON") from exc
    if not isinstance(values, dict):
        raise ValueError("manifest must be a JSON object")
    return SnapshotManifest.model_validate(values)
