"""Owner-signed new-profile anchors for devices already paired independently.

Codes are transferred through the same trusted channel as pairing. They are
lower-bound historical checkpoints, not a globally fresh relay directory.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, field_validator

from .ceremonies import protected_checkpoints
from .control import ControlMutation
from .identity import (
    PairingApproval,
    PairingRequest,
    SignedMembership,
    TrustedCheckpoint,
    canonical_bytes,
    decode_key,
    digest,
    encode_key,
    pairing_fingerprint,
    verify_membership,
    verify_pairing,
)
from .protocol import Counter, WireModel, _uuid
from .service import SyncService

_DOMAIN = b"camoufox-pm/sync/experimental-v1/profile-invitation\0"
_ERROR = "Profile invitation is invalid, stale or unavailable"


class ProfileInvitationError(ValueError):
    """Safe public failure without profile or key material."""


class ProfileInvitationBody(WireModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    root_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    target_device_id: str
    sequence: Counter
    invitation_id: str
    membership: SignedMembership
    checkpoints: Annotated[tuple[TrustedCheckpoint, ...], Field(max_length=64)]

    _ids = field_validator("target_device_id", "invitation_id")(_uuid)

    @field_validator("checkpoints", mode="before")
    @classmethod
    def wire_array(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("checkpoints")
    @classmethod
    def unique_sorted(cls, value):
        identities = [item.profile_id for item in value]
        if identities != sorted(set(identities)):
            raise ValueError("Duplicate or unordered profile checkpoint")
        return value


class ProfileInvitation(WireModel):
    body: ProfileInvitationBody
    signature: Annotated[str, Field(min_length=86, max_length=86)]


def _authorize(callback: Callable[[], None]):
    if not callable(callback):
        raise ProfileInvitationError(_ERROR)
    callback()


def _verify(pack: ProfileInvitation, owner, *, accepting: bool) -> None:
    body = pack.body
    if body.root_digest != digest(owner.root) or body.target_device_id != owner.device_id:
        raise ProfileInvitationError(_ERROR)
    verify_membership(owner.root, body.membership, trusted_digest=digest(body.membership))
    local = next(
        (
            device
            for device in owner.membership.membership.devices
            if device.device_id == owner.device_id
        ),
        None,
    )
    incoming = next(
        (
            device
            for device in body.membership.membership.devices
            if device.device_id == owner.device_id
        ),
        None,
    )
    if incoming is None or local != incoming:
        raise ProfileInvitationError(_ERROR)
    if accepting and body.membership.membership.epoch < owner.membership.membership.epoch:
        raise ProfileInvitationError(_ERROR)
    if (
        body.membership.membership.epoch == owner.membership.membership.epoch
        and digest(body.membership) != owner.membership_digest
    ):
        raise ProfileInvitationError(_ERROR)
    Ed25519PublicKey.from_public_bytes(decode_key(owner.root.authority_public)).verify(
        decode_key(pack.signature, 64), _DOMAIN + canonical_bytes(body)
    )


def create_profile_code(
    service: SyncService,
    user_id: str,
    target_device_id: str,
    password: str | None = None,
    *,
    authorize: Callable[[], None],
) -> dict:
    """Sign at most 64 reconciled protected anchors; the caller cannot supply heads."""
    try:
        _authorize(authorize)
        installation, owner = service._read(user_id)
        target_device_id = _uuid(target_device_id)
        if (
            owner.phase != "ready"
            or not owner.authority
            or target_device_id == owner.device_id
            or target_device_id
            not in {device.device_id for device in owner.membership.membership.devices}
        ):
            raise ProfileInvitationError(_ERROR)
        authority, _ = service._keys(owner, password)
        checkpoints = protected_checkpoints(service)
        _authorize(authorize)
        key = "profiles-issued/" + target_device_id
        previous = service.control.get(key)
        if previous is not None and previous.payload.get("local_user_id") != user_id:
            raise ProfileInvitationError(_ERROR)
        sequence = previous.payload["sequence"] + 1 if previous else 1
        body = ProfileInvitationBody(
            root_digest=digest(owner.root),
            target_device_id=target_device_id,
            sequence=sequence,
            invitation_id=str(uuid4()),
            membership=owner.membership,
            checkpoints=checkpoints,
        )
        pack = ProfileInvitation(
            body=body,
            signature=encode_key(authority.authority_key.sign(_DOMAIN + canonical_bytes(body))),
        )
        _authorize(authorize)
        service.control.batch(
            (
                ControlMutation(
                    "installation", owner.model_dump(mode="json"), installation.revision
                ),
                ControlMutation(
                    key,
                    {
                        "local_user_id": user_id,
                        "sequence": sequence,
                        "invitation_id": body.invitation_id,
                    },
                    previous.revision if previous else None,
                ),
            )
        )
        return pack.model_dump(mode="json")
    except Exception:
        raise ProfileInvitationError(_ERROR) from None


def _paired_profiles(service, user_id, owner):
    result = set()
    for record in service.control.list("received/"):
        value = record.payload
        if value.get("phase") != "consumed" or value.get("local_user_id") != user_id:
            continue
        request = PairingRequest.model_validate(value["request"])
        if request.device.device_id != owner.device_id:
            continue
        approval = PairingApproval.model_validate(value["approval"])
        checkpoints = verify_pairing(
            owner.root,
            request,
            approval,
            approval.membership,
            now=request.issued_at,
            confirmed_fingerprint=pairing_fingerprint(request),
        )
        result.update(checkpoint.profile_id for checkpoint in checkpoints)
    return result


def accept_profile_code(
    service: SyncService,
    user_id: str,
    pack: ProfileInvitation | dict,
    *,
    authorize: Callable[[], None],
) -> dict:
    """Consume a code once; only previously unknown profile anchors are inserted."""
    try:
        _authorize(authorize)
        installation, owner = service._read(user_id)
        if owner.phase not in {"ready", "freshness_unknown"}:
            raise ProfileInvitationError(_ERROR)
        parsed = ProfileInvitation.model_validate(
            pack.model_dump() if isinstance(pack, ProfileInvitation) else pack
        )
        _verify(parsed, owner, accepting=True)
        if any(
            service.control.get(key) is not None
            for key in ("sync/revoked", "sync/membership-conflict")
        ):
            raise ProfileInvitationError(_ERROR)
        for record in service.control.list("membership/"):
            observed = SignedMembership.model_validate(record.payload)
            verify_membership(owner.root, observed, trusted_digest=digest(observed))
            if observed.membership.epoch > parsed.body.membership.membership.epoch or (
                observed.membership.epoch == parsed.body.membership.membership.epoch
                and digest(observed) != digest(parsed.body.membership)
            ):
                raise ProfileInvitationError(_ERROR)
        body = parsed.body
        key = "profiles-received/" + owner.device_id
        previous = service.control.get(key)
        if previous is not None:
            payload = previous.payload
            if payload.get("local_user_id") != user_id:
                raise ProfileInvitationError(_ERROR)
            if body.sequence <= payload["sequence"]:
                if (
                    body.sequence == payload["sequence"]
                    and payload["digest"] == digest(parsed)
                    and payload["invitation_id"] == body.invitation_id
                ):
                    _authorize(authorize)
                    return {"accepted": True, "added": 0, "sequence": body.sequence}
                raise ProfileInvitationError(_ERROR)
        evidence_key = "profiles-code/" + body.invitation_id
        if service.control.get(evidence_key) is not None:
            raise ProfileInvitationError(_ERROR)
        mutations = [
            ControlMutation("installation", owner.model_dump(mode="json"), installation.revision),
            ControlMutation(
                key,
                {
                    "local_user_id": user_id,
                    "sequence": body.sequence,
                    "invitation_id": body.invitation_id,
                    "digest": digest(parsed),
                },
                previous.revision if previous else None,
            ),
            ControlMutation(
                evidence_key, {"local_user_id": user_id, "pack": parsed.model_dump(mode="json")}
            ),
        ]
        member_key = "membership/" + str(body.membership.membership.epoch)
        member_record = service.control.get(member_key)
        if member_record is None:
            # Pin the exact independently authenticated epoch, not just its number.
            # Engine reconciliation must extend/agree with this digest before bind.
            mutations.append(ControlMutation(member_key, body.membership.model_dump(mode="json")))
        elif member_record.payload != body.membership.model_dump(mode="json"):
            raise ProfileInvitationError(_ERROR)
        added = 0
        paired_profiles = _paired_profiles(service, user_id, owner)
        for checkpoint in body.checkpoints:
            anchor_key = "trusted-profile/" + checkpoint.profile_id
            if (
                checkpoint.profile_id in paired_profiles
                or service.control.get("mapping/" + checkpoint.profile_id) is not None
                or service.control.get(anchor_key) is not None
            ):
                continue
            mutations.append(
                ControlMutation(
                    anchor_key,
                    {
                        "local_user_id": user_id,
                        "invitation_id": body.invitation_id,
                        "profile_id": checkpoint.profile_id,
                    },
                )
            )
            added += 1
        _authorize(authorize)
        service.control.batch(tuple(mutations))
        return {"accepted": True, "added": added, "sequence": body.sequence}
    except Exception:
        raise ProfileInvitationError(_ERROR) from None


def trusted_profile_checkpoints(service: SyncService, user_id: str) -> dict[str, dict]:
    """Read immutable received anchors for engine binding; never trust HTTP heads."""
    try:
        _, owner = service._read(user_id)
        if any(
            service.control.get(key) is not None
            for key in ("sync/revoked", "sync/membership-conflict")
        ):
            raise ProfileInvitationError(_ERROR)
        highwater = service.control.get("profiles-received/" + owner.device_id)
        result = {}
        for record in service.control.list("trusted-profile/"):
            value = record.payload
            if (
                value.get("local_user_id") != user_id
                or record.key != "trusted-profile/" + value["profile_id"]
            ):
                raise ProfileInvitationError(_ERROR)
            evidence = service.control.get("profiles-code/" + _uuid(value["invitation_id"]))
            if evidence is None or evidence.payload.get("local_user_id") != user_id:
                raise ProfileInvitationError(_ERROR)
            pack = ProfileInvitation.model_validate(evidence.payload["pack"])
            _verify(pack, owner, accepting=False)
            pinned = service.control.get("membership/" + str(pack.body.membership.membership.epoch))
            if pinned is None or pinned.payload != pack.body.membership.model_dump(mode="json"):
                raise ProfileInvitationError(_ERROR)
            if (
                pack.body.invitation_id != value["invitation_id"]
                or highwater is None
                or highwater.payload.get("local_user_id") != user_id
                or pack.body.sequence > highwater.payload["sequence"]
            ):
                raise ProfileInvitationError(_ERROR)
            checkpoint = next(
                (item for item in pack.body.checkpoints if item.profile_id == value["profile_id"]),
                None,
            )
            if checkpoint is None:
                raise ProfileInvitationError(_ERROR)
            result[checkpoint.profile_id] = {
                "owner_id": owner.root.owner_id,
                "membership_epoch": pack.body.membership.membership.epoch,
                **checkpoint.model_dump(mode="json"),
            }
        return result
    except Exception:
        raise ProfileInvitationError(_ERROR) from None
