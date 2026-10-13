"""Durable owner enrollment ceremonies; only authenticated session owners may call.

The API must resolve a live administrator to their immutable local account ID.
This module additionally binds all ceremonies to that ID. Keys never enter the
control database. An interrupted enrollment remains blocked until resumed or
explicitly abandoned; it never silently creates a replacement identity.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from camoufox_pm.sync.control import ControlMutation, ControlRecord, ControlStore
from camoufox_pm.sync.identity import (
    AuthoritySecrets,
    DeviceCertificate,
    DeviceSecrets,
    OwnerRoot,
    PairingApproval,
    PairingRequest,
    SignedMembership,
    TrustedCheckpoint,
    approve_membership,
    approve_pairing,
    bootstrap,
    digest,
    encode_key,
    verify_membership,
    verify_pairing,
)
from camoufox_pm.sync.protocol import WireModel
from camoufox_pm.sync.recovery import create_recovery, verify_recovery_isolated
from camoufox_pm.sync.vault import EncryptedFileVault, OSVault


class EnrollmentError(ValueError):
    """Safe error for an unavailable or unauthorized enrollment ceremony."""


class Enrollment(WireModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    local_user_id: str = Field(min_length=1, max_length=128)
    device_label: str = Field(min_length=1, max_length=80)
    root: OwnerRoot
    membership: SignedMembership
    membership_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_id: str
    vault_kind: Literal["native", "encrypted_file"]
    phase: Literal["preparing", "recovery_required", "ready", "abandoned", "freshness_unknown"]
    authority: bool = True


class SyncService:
    """One installation, one explicit owner; no unlocked secrets retained on disk."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.control = ControlStore(directory)

    def _state(self, record: ControlRecord, user_id: str, *, abandoned: bool = False) -> Enrollment:
        try:
            state = Enrollment.model_validate(record.payload)
            verify_membership(state.root, state.membership, trusted_digest=state.membership_digest)
            if (
                state.local_user_id != user_id
                or (state.phase == "abandoned" and not abandoned)
                or state.device_id not in {d.device_id for d in state.membership.membership.devices}
            ):
                raise ValueError
            return state
        except Exception:
            raise EnrollmentError("Sync enrollment is unavailable for this account") from None

    def _read(self, user_id: str) -> tuple[ControlRecord, Enrollment]:
        record = self.control.get("installation")
        if record is None:
            raise EnrollmentError("Sync is not enrolled")
        return record, self._state(record, user_id)

    def status(self, user_id: str, *, authorize: Callable[[], None] | None = None) -> dict:
        self._authorize(authorize)
        record = self.control.get("installation")
        if record is None:
            return {"phase": "not_enrolled"}
        state = self._state(record, user_id, abandoned=True)
        self._authorize(authorize)
        if state.phase == "abandoned":
            return {"phase": "not_enrolled"}
        return {
            "phase": state.phase,
            "owner_id": state.root.owner_id,
            "device_id": state.device_id,
            "device_label": state.device_label,
            "relay_url": state.root.relay_url,
            "vault_kind": state.vault_kind,
            "authority": state.authority,
            "membership_epoch": state.membership.membership.epoch,
        }

    def _vault(self, state: Enrollment, password: str | None):
        if state.vault_kind == "native":
            return OSVault(state.root.owner_id)
        if password is None:
            raise EnrollmentError("Unlock the encrypted key store")
        return EncryptedFileVault(self.directory / ("keys-" + state.root.owner_id), password)

    def _keys(
        self, state: Enrollment, password: str | None
    ) -> tuple[AuthoritySecrets, DeviceSecrets]:
        try:
            vault = self._vault(state, password)
            authority = AuthoritySecrets.from_bytes(vault.get(state.root.owner_id) or b"")
            device = DeviceSecrets.from_bytes(vault.get(state.device_id) or b"")
            if (
                encode_key(authority.authority_key.public_key().public_bytes_raw())
                != state.root.authority_public
                or encode_key(authority.recovery_key.public_key().public_bytes_raw())
                != state.root.recovery_public
                or device.certificate(state.root.owner_id, state.device_id)
                not in state.membership.membership.devices
            ):
                raise ValueError
            return authority, device
        except Exception:
            raise EnrollmentError("Protected sync keys are locked, missing or invalid") from None

    def device_keys(
        self,
        user_id: str,
        password: str | None = None,
        *,
        authorize: Callable[[], None] | None = None,
    ) -> DeviceSecrets:
        """Unlock routine device credentials without reading authority/recovery slots."""
        record, state = self._read(user_id)
        if state.phase not in {"ready", "freshness_unknown"}:
            raise EnrollmentError("Device enrollment is incomplete")
        try:
            vault = self._vault(state, password)
            device = DeviceSecrets.from_bytes(vault.get(state.device_id) or b"")
            if (
                device.certificate(state.root.owner_id, state.device_id)
                not in state.membership.membership.devices
            ):
                raise ValueError
        except Exception:
            raise EnrollmentError("Protected device keys are locked, missing or invalid") from None
        self._authorize(authorize)
        current = self.control.get("installation")
        if current is None or current.revision != record.revision:
            raise EnrollmentError("Device enrollment changed during unlock")
        return device

    @staticmethod
    def _authorize(authorize: Callable[[], None] | None) -> None:
        if authorize is not None:
            authorize()

    def begin_enrollment(
        self,
        user_id: str,
        relay_url: str,
        device_label: str,
        vault_kind: Literal["native", "encrypted_file"],
        password: str | None = None,
        *,
        allow_loopback_http: bool = False,
        authorize: Callable[[], None] | None = None,
    ) -> dict:
        old = self.control.get("installation")
        if old is not None:
            previous = self._state(old, user_id, abandoned=True)
            if previous.phase != "abandoned":
                raise EnrollmentError("An enrollment already exists; resume it")
        root, secrets, membership = bootstrap(relay_url, allow_loopback_http=allow_loopback_http)
        state = Enrollment(
            local_user_id=user_id,
            device_label=device_label.strip(),
            root=root,
            membership=membership,
            membership_digest=digest(membership),
            device_id=membership.membership.devices[0].device_id,
            vault_kind=vault_kind,
            phase="preparing",
        )
        # Constructing the vault may reject an unsupported backend/password before
        # reserving the installation. Actual writes occur only after the journal.
        vault = self._vault(state, password)
        payload = state.model_dump(mode="json")
        self._authorize(authorize)
        record = (
            self.control.create("installation", payload)
            if old is None
            else self.control.update("installation", payload, expected_revision=old.revision)
        )
        try:
            vault.put(root.owner_id, secrets.authority.to_bytes())
            vault.put(state.device_id, secrets.device.to_bytes())
            self._keys(state, password)
        except Exception:
            raise EnrollmentError(
                "Enrollment interrupted; resume or abandon it explicitly"
            ) from None
        self._authorize(authorize)
        state = state.model_copy(update={"phase": "recovery_required"})
        self.control.update(
            "installation", state.model_dump(mode="json"), expected_revision=record.revision
        )
        return self.status(user_id, authorize=authorize)

    def recovery_package(
        self,
        user_id: str,
        password: str | None = None,
        *,
        authorize: Callable[[], None] | None = None,
    ) -> tuple[bytes, str]:
        record, state = self._read(user_id)
        if not state.authority:
            raise EnrollmentError("Only the owner authority can export recovery")
        authority, _ = self._keys(state, password)
        self._authorize(authorize)
        if state.phase == "preparing":
            state = state.model_copy(update={"phase": "recovery_required"})
            self.control.update(
                "installation", state.model_dump(mode="json"), expected_revision=record.revision
            )
        package = create_recovery(state.root, authority)
        self._authorize(authorize)
        # Never export after a concurrent abandonment or membership update.
        current = self.control.get("installation")
        expected_revision = record.revision + (1 if record.payload["phase"] == "preparing" else 0)
        if current is None or current.revision != expected_revision:
            raise EnrollmentError("Enrollment changed during recovery export")
        return package

    def confirm_recovery(
        self,
        user_id: str,
        package: bytes,
        secret: str,
        password: str | None = None,
        *,
        authorize: Callable[[], None] | None = None,
    ) -> dict:
        record, state = self._read(user_id)
        if state.phase != "recovery_required":
            raise EnrollmentError("Recovery confirmation is not pending")
        self._keys(state, password)
        verify_recovery_isolated(package, secret, state.root)
        self._authorize(authorize)
        state = state.model_copy(update={"phase": "ready"})
        self.control.update(
            "installation", state.model_dump(mode="json"), expected_revision=record.revision
        )
        return self.status(user_id, authorize=authorize)

    def abandon_enrollment(
        self, user_id: str, *, authorize: Callable[[], None] | None = None
    ) -> None:
        record, state = self._read(user_id)
        if state.phase not in {"preparing", "recovery_required"}:
            raise EnrollmentError("An active owner cannot be discarded by setup")
        # Retain a tombstone; old encrypted/native slots cannot be used by a new
        # owner namespace. Cleanup is best effort, never rollback this decision.
        self._authorize(authorize)
        state = state.model_copy(update={"phase": "abandoned"})
        self.control.batch(
            (
                ControlMutation("installation", state.model_dump(mode="json"), record.revision),
                ControlMutation("abandoned/" + state.root.owner_id, state.model_dump(mode="json")),
            )
        )

    def create_pairing_invitation(
        self,
        user_id: str,
        device: DeviceCertificate,
        *,
        now: int,
        lifetime: int = 300,
        authorize: Callable[[], None] | None = None,
    ) -> PairingRequest:
        record, state = self._read(user_id)
        if state.phase != "ready" or not state.authority:
            raise EnrollmentError("A verified owner authority is required")
        if device.owner_id != state.root.owner_id:
            raise EnrollmentError("Pairing owner mismatch")
        if type(now) is not int or type(lifetime) is not int or not 1 <= lifetime <= 900:
            raise EnrollmentError("Invalid invitation lifetime")
        request = PairingRequest(
            root_digest=digest(state.root),
            invitation_id=str(uuid4()),
            device=device,
            issued_at=now,
            expires_at=now + lifetime,
        )
        pending = self.control.list("invitation/")
        if (
            sum(
                item.payload.get("phase") == "pending"
                and item.payload.get("request", {}).get("expires_at", 0) > now
                for item in pending
            )
            >= 128
        ):
            raise EnrollmentError("Too many pending pairing invitations")
        self._authorize(authorize)
        self.control.batch(
            (
                ControlMutation("installation", state.model_dump(mode="json"), record.revision),
                ControlMutation(
                    "invitation/" + request.invitation_id,
                    {
                        "phase": "pending",
                        "local_user_id": user_id,
                        "request": request.model_dump(mode="json"),
                    },
                ),
            )
        )
        return request

    def approve_pairing_invitation(
        self,
        user_id: str,
        request: PairingRequest,
        confirmed_fingerprint: str,
        password: str | None = None,
        *,
        now: int,
        checkpoints: tuple[TrustedCheckpoint, ...] = (),
        authorize: Callable[[], None] | None = None,
    ) -> PairingApproval:
        record, state = self._read(user_id)
        if state.phase != "ready" or not state.authority:
            raise EnrollmentError("A verified owner authority is required")
        invitation = self.control.get("invitation/" + request.invitation_id)
        if invitation is None or invitation.payload != {
            "phase": "pending",
            "local_user_id": user_id,
            "request": request.model_dump(mode="json"),
        }:
            raise EnrollmentError("Pairing invitation is unavailable or already consumed")
        authority, _ = self._keys(state, password)
        membership = approve_membership(
            state.root,
            authority.authority_key,
            state.membership.membership.devices + (request.device,),
            previous=state.membership,
        )
        approval = approve_pairing(
            state.root,
            authority.authority_key,
            request,
            membership,
            checkpoints,
            now=now,
            confirmed_fingerprint=confirmed_fingerprint,
        )
        new_state = state.model_copy(
            update={"membership": membership, "membership_digest": digest(membership)}
        )
        self._authorize(authorize)
        self.control.batch(
            (
                ControlMutation("installation", new_state.model_dump(mode="json"), record.revision),
                ControlMutation(
                    invitation.key,
                    {
                        "phase": "consumed",
                        "local_user_id": user_id,
                        "request": request.model_dump(mode="json"),
                        "approval": approval.model_dump(mode="json"),
                    },
                    invitation.revision,
                ),
            )
        )
        return approval

    def consume_pairing_approval(
        self,
        user_id: str,
        root: OwnerRoot,
        request: PairingRequest,
        approval: PairingApproval,
        device: DeviceSecrets,
        device_label: str,
        vault_kind: Literal["native", "encrypted_file"],
        password: str | None = None,
        *,
        now: int,
        confirmed_fingerprint: str,
        authorize: Callable[[], None] | None = None,
    ) -> dict:
        """Explicit recipient confirmation; no authority secrets are transferred.

        A fresh recipient remains freshness_unknown until profile reconciliation.
        A crash after journal creation can be retried with the same ceremony/keys;
        a different approval cannot replace the reserved identity.
        """
        checkpoints = verify_pairing(
            root,
            request,
            approval,
            approval.membership,
            now=now,
            confirmed_fingerprint=confirmed_fingerprint,
        )
        if device.certificate(root.owner_id, request.device.device_id) != request.device:
            raise EnrollmentError("Pairing device keys do not match")
        state = Enrollment(
            local_user_id=user_id,
            device_label=device_label.strip(),
            root=root,
            membership=approval.membership,
            membership_digest=digest(approval.membership),
            device_id=request.device.device_id,
            vault_kind=vault_kind,
            phase="preparing",
            authority=False,
        )
        old = self.control.get("installation")
        pair_key = "received/" + request.invitation_id
        pair_payload = {
            "phase": "preparing",
            "request": request.model_dump(mode="json"),
            "approval": approval.model_dump(mode="json"),
            "local_user_id": user_id,
        }
        pending = self.control.get(pair_key)
        vault = self._vault(state, password)
        self._authorize(authorize)
        if old is None:
            created = self.control.batch(
                (
                    ControlMutation("installation", state.model_dump(mode="json")),
                    ControlMutation(pair_key, pair_payload),
                )
            )
            old, pending = created
        elif self._state(old, user_id, abandoned=True).phase == "abandoned":
            created = self.control.batch(
                (
                    ControlMutation("installation", state.model_dump(mode="json"), old.revision),
                    ControlMutation(pair_key, pair_payload),
                )
            )
            old, pending = created
        elif (
            self._state(old, user_id) != state or pending is None or pending.payload != pair_payload
        ):
            raise EnrollmentError("Pairing is already consumed or another enrollment exists")
        assert pending is not None
        vault.put(state.device_id, device.to_bytes())
        if vault.get(state.device_id) != device.to_bytes():
            raise EnrollmentError("Pairing device keys could not be verified")
        self._authorize(authorize)
        ready = state.model_copy(update={"phase": "freshness_unknown"})
        self.control.batch(
            (
                ControlMutation("installation", ready.model_dump(mode="json"), old.revision),
                ControlMutation(
                    pair_key,
                    {
                        **pair_payload,
                        "phase": "consumed",
                        "checkpoints": [c.model_dump(mode="json") for c in checkpoints],
                    },
                    pending.revision,
                ),
            )
        )
        return self.status(user_id, authorize=authorize)
