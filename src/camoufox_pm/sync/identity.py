"""Owner-authorized identity records. Persistence and invitation consumption are external.

Keys supplied by a relay are never trust roots. Verify against an independently
installed OwnerRoot and durable membership high-water mark before using devices.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
from dataclasses import dataclass, field
from typing import Annotated, Any, TypeVar
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import Field, field_validator, model_validator

from camoufox_pm.sync.protocol import Counter, WireModel, _base64, _uuid

MAX_IDENTITY_BYTES = 256 * 1024
DOMAIN = b"camoufox-pm/sync/experimental-v1/identity/"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PublicKey = Annotated[str, Field(max_length=43)]
T = TypeVar("T", bound=WireModel)


def encode_key(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_key(encoded: str, length: int = 32) -> bytes:
    _base64(encoded, length)
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def normalize_relay_url(value: str, *, allow_loopback_http: bool = False) -> str:
    """Restrict relays to origins: no credentials, paths, query or fragments."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        raise ValueError("invalid relay origin")
    parts = urlsplit(value)
    if (
        parts.username is not None
        or parts.password is not None
        or "?" in value
        or "#" in value
        or parts.path not in ("", "/")
    ):
        raise ValueError("relay must be an origin")
    host = parts.hostname
    if not host or parts.scheme not in ("https", "http"):
        raise ValueError("invalid relay origin")
    try:
        addr = ipaddress.ip_address(host)
        loopback = addr.is_loopback
        host = f"[{addr.compressed}]" if addr.version == 6 else addr.compressed
    except ValueError:
        loopback = host.lower() == "localhost"
        host = host.encode("idna").decode("ascii").lower()
        if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in host):
            raise ValueError("invalid relay host") from None
    if parts.scheme != "https" and not (allow_loopback_http and loopback):
        raise ValueError("HTTPS relay required")
    port = parts.port
    if port == 0:
        raise ValueError("invalid relay port")
    suffix = f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else ""
    return f"{parts.scheme}://{host}{suffix}"


class OwnerRoot(WireModel):
    version: Annotated[int, Field(ge=1, le=1)] = 1
    owner_id: str
    relay_url: Annotated[str, Field(max_length=2048)]
    development_loopback: bool = False
    authority_public: PublicKey
    recovery_id: str
    recovery_public: PublicKey

    _ids = field_validator("owner_id", "recovery_id")(_uuid)

    @field_validator("authority_public", "recovery_public")
    @classmethod
    def public_key(cls, value: str) -> str:
        return _base64(value, 32)

    @model_validator(mode="after")
    def valid_origin(self) -> OwnerRoot:
        if (
            normalize_relay_url(self.relay_url, allow_loopback_http=self.development_loopback)
            != self.relay_url
        ):
            raise ValueError("relay origin must be normalized")
        return self


class DeviceCertificate(WireModel):
    owner_id: str
    device_id: str
    signing_public: PublicKey
    encryption_public: PublicKey

    _ids = field_validator("owner_id", "device_id")(_uuid)

    @field_validator("signing_public", "encryption_public")
    @classmethod
    def public_key(cls, value: str) -> str:
        return _base64(value, 32)


class Membership(WireModel):
    root_digest: Digest
    epoch: Counter
    previous_digest: Digest | None
    devices: Annotated[tuple[DeviceCertificate, ...], Field(min_length=1, max_length=128)]

    @field_validator("devices", mode="before")
    @classmethod
    def array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique_devices(self) -> Membership:
        ids = tuple(d.device_id for d in self.devices)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("devices must be unique and sorted")
        for attr in ("signing_public", "encryption_public"):
            if len({getattr(d, attr) for d in self.devices}) != len(ids):
                raise ValueError("device keys must be unique")
        if (self.epoch == 1) != (self.previous_digest is None):
            raise ValueError("invalid membership chain")
        return self


class SignedMembership(WireModel):
    membership: Membership
    signature: Annotated[str, Field(max_length=86)]

    @field_validator("signature")
    @classmethod
    def valid_signature(cls, value: str) -> str:
        return _base64(value, 64)


class TrustedCheckpoint(WireModel):
    profile_id: str
    revision_id: str | None
    checkout_generation: Annotated[int, Field(ge=0, le=2**53 - 1)]
    holder_device_id: str | None

    _id = field_validator("profile_id")(_uuid)

    @field_validator("revision_id", "holder_device_id")
    @classmethod
    def optional_id(cls, value: str | None) -> str | None:
        return _uuid(value) if value is not None else None


class PairingRequest(WireModel):
    root_digest: Digest
    invitation_id: str
    device: DeviceCertificate
    issued_at: Annotated[int, Field(ge=0, le=2**53 - 1)]
    expires_at: Annotated[int, Field(ge=0, le=2**53 - 1)]

    _id = field_validator("invitation_id")(_uuid)

    @model_validator(mode="after")
    def bounded_expiry(self) -> PairingRequest:
        if not 0 < self.expires_at - self.issued_at <= 900:
            raise ValueError("pairing lifetime must be at most fifteen minutes")
        return self


class PairingApproval(WireModel):
    request_digest: Digest
    membership_digest: Digest
    membership: SignedMembership
    checkpoints: Annotated[tuple[TrustedCheckpoint, ...], Field(max_length=1024)]
    signature: Annotated[str, Field(max_length=86)]

    @field_validator("signature")
    @classmethod
    def valid_signature(cls, value: str) -> str:
        return _base64(value, 64)

    @field_validator("checkpoints", mode="before")
    @classmethod
    def array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique_profiles(self) -> PairingApproval:
        ids = tuple(c.profile_id for c in self.checkpoints)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("checkpoints must be unique and sorted")
        return self


def canonical_bytes(record: WireModel) -> bytes:
    # Revalidate even model_construct/model_copy inputs at every public boundary.
    checked = type(record).model_validate(record.model_dump())
    raw = json.dumps(
        checked.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    if len(raw) > MAX_IDENTITY_BYTES:
        raise ValueError("identity record too large")
    return raw


def parse_record(raw: bytes, model: type[T]) -> T:
    if not isinstance(raw, bytes) or len(raw) > MAX_IDENTITY_BYTES:
        raise ValueError("identity record too large")

    depth = 0
    quoted = escaped = False
    for char in raw:
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
                raise ValueError("identity nesting limit exceeded")
        elif char in (93, 125):
            depth -= 1

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate identity field")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        return model.model_validate(value)
    except (RecursionError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid identity record") from exc


def digest(record: WireModel) -> str:
    return hashlib.sha256(
        DOMAIN + type(record).__name__.encode() + b"\0" + canonical_bytes(record)
    ).hexdigest()


def _signing_bytes(kind: str, record: WireModel) -> bytes:
    return DOMAIN + kind.encode("ascii") + b"\0" + canonical_bytes(record)


def verify_membership(
    root: OwnerRoot,
    signed: SignedMembership,
    *,
    previous: SignedMembership | None = None,
    trusted_digest: str | None = None,
) -> Membership:
    """Verify genesis, exact trusted checkpoint, or one direct successor. No gaps."""
    canonical_bytes(root)
    canonical_bytes(signed)
    record = signed.membership
    if record.root_digest != digest(root) or any(
        d.owner_id != root.owner_id for d in record.devices
    ):
        raise ValueError("membership owner mismatch")
    if any(
        d.signing_public == root.authority_public
        or d.encryption_public == root.recovery_public
        or d.device_id == root.recovery_id
        for d in record.devices
    ):
        raise ValueError("authority and device keys must be separate")
    Ed25519PublicKey.from_public_bytes(decode_key(root.authority_public)).verify(
        decode_key(signed.signature, 64), _signing_bytes("membership", record)
    )
    if previous is not None:
        # The previous record must itself be authenticated, even if its ancestors are
        # held by the durable caller rather than supplied again here.
        verify_membership(root, previous, trusted_digest=digest(previous))
        if record.epoch != previous.membership.epoch + 1 or record.previous_digest != digest(
            previous
        ):
            raise ValueError("stale or forked membership")
        retained = {device.device_id: device for device in previous.membership.devices}
        if any(
            device.device_id in retained and retained[device.device_id] != device
            for device in record.devices
        ):
            raise ValueError("retained device identity cannot change keys")
    elif trusted_digest is not None:
        if digest(signed) != trusted_digest:
            raise ValueError("membership differs from trusted checkpoint")
    elif record.epoch != 1:
        raise ValueError("membership requires trusted predecessor")
    return record


def approve_membership(
    root: OwnerRoot,
    authority_key: Ed25519PrivateKey,
    devices: tuple[DeviceCertificate, ...],
    *,
    previous: SignedMembership | None = None,
) -> SignedMembership:
    if encode_key(authority_key.public_key().public_bytes_raw()) != root.authority_public:
        raise ValueError("wrong owner authority")
    if previous is not None:
        verify_membership(root, previous, trusted_digest=digest(previous))
    record = Membership(
        root_digest=digest(root),
        epoch=1 if previous is None else previous.membership.epoch + 1,
        previous_digest=None if previous is None else digest(previous),
        devices=tuple(sorted(devices, key=lambda d: d.device_id)),
    )
    signed = SignedMembership(
        membership=record,
        signature=encode_key(authority_key.sign(_signing_bytes("membership", record))),
    )
    verify_membership(root, signed, previous=previous)
    return signed


@dataclass(frozen=True, repr=False)
class DeviceSecrets:
    signing_key: Ed25519PrivateKey = field(repr=False)
    encryption_key: X25519PrivateKey = field(repr=False)

    def __repr__(self) -> str:
        return "DeviceSecrets(<redacted>)"

    def to_bytes(self) -> bytes:
        """Sensitive device-only export. Encrypt before persisting."""
        return (
            b"CPMDEV1\0"
            + self.signing_key.private_bytes_raw()
            + self.encryption_key.private_bytes_raw()
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> DeviceSecrets:
        if not isinstance(raw, bytes) or len(raw) != 72 or raw[:8] != b"CPMDEV1\0":
            raise ValueError("invalid protected device record")
        return cls(
            Ed25519PrivateKey.from_private_bytes(raw[8:40]),
            X25519PrivateKey.from_private_bytes(raw[40:72]),
        )

    def certificate(self, owner_id: str, device_id: str) -> DeviceCertificate:
        return DeviceCertificate(
            owner_id=owner_id,
            device_id=device_id,
            signing_public=encode_key(self.signing_key.public_key().public_bytes_raw()),
            encryption_public=encode_key(self.encryption_key.public_key().public_bytes_raw()),
        )


def generate_device() -> DeviceSecrets:
    return DeviceSecrets(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())


@dataclass(frozen=True, repr=False)
class BootstrapSecrets:
    authority_key: Ed25519PrivateKey = field(repr=False)
    recovery_key: X25519PrivateKey = field(repr=False)
    device: DeviceSecrets = field(repr=False)

    def __repr__(self) -> str:
        return "BootstrapSecrets(<redacted>)"

    @property
    def authority(self) -> AuthoritySecrets:
        return AuthoritySecrets(self.authority_key, self.recovery_key)


@dataclass(frozen=True, repr=False)
class AuthoritySecrets:
    authority_key: Ed25519PrivateKey = field(repr=False)
    recovery_key: X25519PrivateKey = field(repr=False)

    def __repr__(self) -> str:
        return "AuthoritySecrets(<redacted>)"

    def to_bytes(self) -> bytes:
        """Sensitive authority-only export. Encrypt before persisting."""
        return (
            b"CPMAUT1\0"
            + self.authority_key.private_bytes_raw()
            + self.recovery_key.private_bytes_raw()
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> AuthoritySecrets:
        if not isinstance(raw, bytes) or len(raw) != 72 or raw[:8] != b"CPMAUT1\0":
            raise ValueError("invalid protected authority record")
        return cls(
            Ed25519PrivateKey.from_private_bytes(raw[8:40]),
            X25519PrivateKey.from_private_bytes(raw[40:72]),
        )


def bootstrap(
    relay_url: str, *, allow_loopback_http: bool = False
) -> tuple[OwnerRoot, BootstrapSecrets, SignedMembership]:
    secrets = BootstrapSecrets(
        Ed25519PrivateKey.generate(), X25519PrivateKey.generate(), generate_device()
    )
    root = OwnerRoot(
        owner_id=str(uuid4()),
        relay_url=normalize_relay_url(relay_url, allow_loopback_http=allow_loopback_http),
        development_loopback=allow_loopback_http,
        authority_public=encode_key(secrets.authority_key.public_key().public_bytes_raw()),
        recovery_id=str(uuid4()),
        recovery_public=encode_key(secrets.recovery_key.public_key().public_bytes_raw()),
    )
    membership = approve_membership(
        root, secrets.authority_key, (secrets.device.certificate(root.owner_id, str(uuid4())),)
    )
    return root, secrets, membership


def pairing_fingerprint(request: PairingRequest) -> str:
    return digest(request)


def _approval_bytes(approval: PairingApproval) -> bytes:
    # Fixed placeholder avoids signing a circular signature field.
    unsigned = approval.model_copy(update={"signature": encode_key(bytes(64))})
    return _signing_bytes("pairing-approval", unsigned)


def _validate_request(
    root: OwnerRoot, request: PairingRequest, now: int, confirmed_fingerprint: str
) -> None:
    canonical_bytes(request)
    if type(now) is not int or not request.issued_at <= now < request.expires_at:
        raise ValueError("pairing invitation expired or not yet valid")
    if request.root_digest != digest(root) or request.device.owner_id != root.owner_id:
        raise ValueError("pairing owner or relay mismatch")
    if confirmed_fingerprint != pairing_fingerprint(request):
        raise ValueError("pairing fingerprint mismatch")


def approve_pairing(
    root: OwnerRoot,
    authority_key: Ed25519PrivateKey,
    request: PairingRequest,
    membership: SignedMembership,
    checkpoints: tuple[TrustedCheckpoint, ...],
    *,
    now: int,
    confirmed_fingerprint: str,
) -> PairingApproval:
    _validate_request(root, request, now, confirmed_fingerprint)
    verify_membership(root, membership, trusted_digest=digest(membership))
    if request.device not in membership.membership.devices:
        raise ValueError("device not approved by membership")
    if encode_key(authority_key.public_key().public_bytes_raw()) != root.authority_public:
        raise ValueError("wrong owner authority")
    approval = PairingApproval(
        request_digest=digest(request),
        membership_digest=digest(membership),
        membership=membership,
        checkpoints=checkpoints,
        signature=encode_key(bytes(64)),
    )
    return approval.model_copy(
        update={"signature": encode_key(authority_key.sign(_approval_bytes(approval)))}
    )


def verify_pairing(
    root: OwnerRoot,
    request: PairingRequest,
    approval: PairingApproval,
    membership: SignedMembership,
    *,
    now: int,
    confirmed_fingerprint: str,
) -> tuple[TrustedCheckpoint, ...]:
    """After verification caller MUST atomically consume invitation and install trust.

    The root and fingerprint must be independently confirmed on both devices.
    A valid approval alone does not guarantee freshness or one-use behavior.
    """
    _validate_request(root, request, now, confirmed_fingerprint)
    canonical_bytes(approval)
    if (
        approval.request_digest != digest(request)
        or approval.membership_digest != digest(membership)
        or approval.membership != membership
    ):
        raise ValueError("pairing approval mismatch")
    Ed25519PublicKey.from_public_bytes(decode_key(root.authority_public)).verify(
        decode_key(approval.signature, 64), _approval_bytes(approval)
    )
    verify_membership(root, membership, trusted_digest=approval.membership_digest)
    if request.device not in membership.membership.devices:
        raise ValueError("paired device absent from membership")
    return approval.checkpoints
