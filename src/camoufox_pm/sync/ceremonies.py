"""Session-bound device pairing, relay registration and owner revocation.

Checkpoints are supplied only by an injected trusted local engine, never by
request data. Transport responses remain untrusted until compared to the pinned
owner root and exact signed membership. Tokens and private keys never enter the
control store.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from camoufox_pm.sync.client import RelayClient, RelayConflict
from camoufox_pm.sync.control import ControlMutation, ControlRecord
from camoufox_pm.sync.identity import (
    DeviceSecrets,
    OwnerRoot,
    PairingApproval,
    PairingRequest,
    SignedMembership,
    TrustedCheckpoint,
    approve_membership,
    digest,
    generate_device,
    pairing_fingerprint,
    verify_membership,
)
from camoufox_pm.sync.protocol import WireModel
from camoufox_pm.sync.service import SyncService
from camoufox_pm.sync.vault import EncryptedFileVault, OSVault

_ERROR = "Device ceremony is unavailable or requires recovery"


class CeremonyError(ValueError):
    """Sanitized ceremony failure; persisted intent remains available to resume."""


class PairingDraft(WireModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    local_user_id: str = Field(min_length=1, max_length=128)
    root: OwnerRoot
    request: PairingRequest
    device_label: str = Field(min_length=1, max_length=80)
    vault_kind: Literal["native", "encrypted_file"]
    phase: Literal["preparing", "pending", "consumed", "abandoned"]


class DeviceCeremonies:
    def __init__(
        self,
        service: SyncService,
        *,
        checkpoints: Callable[[], tuple[TrustedCheckpoint, ...]],
        client_factory: Callable[..., RelayClient] = RelayClient,
    ):
        self.service = service
        self.control = service.control
        self._checkpoints = checkpoints
        self._client_factory = client_factory

    @staticmethod
    def _authorize(authorize: Callable[[], None]) -> None:
        if not callable(authorize):
            raise CeremonyError(_ERROR)
        authorize()

    def _owner(self, user_id: str, authorize: Callable[[], None]):
        self._authorize(authorize)
        record, state = self.service._read(user_id)
        if state.phase != "ready" or not state.authority:
            raise CeremonyError(_ERROR)
        return record, state

    def _draft(self, user_id: str):
        record = self.control.get("ceremony/pairing")
        if record is None:
            raise CeremonyError(_ERROR)
        draft = PairingDraft.model_validate(record.payload)
        if draft.local_user_id != user_id or draft.request.root_digest != digest(draft.root):
            raise CeremonyError(_ERROR)
        return record, draft

    def _draft_vault(self, draft: PairingDraft, password: str | None):
        if draft.vault_kind == "native":
            return OSVault(draft.root.owner_id)
        if password is None:
            raise CeremonyError(_ERROR)
        return EncryptedFileVault(
            self.service.directory / ("keys-" + draft.root.owner_id), password
        )

    def export_owner_code(self, user_id: str, *, authorize: Callable[[], None]) -> dict:
        try:
            record, state = self._owner(user_id, authorize)
            self._authorize(authorize)
            if self.control.get("installation") != record:
                raise CeremonyError(_ERROR)
            return state.root.model_dump(mode="json")
        except Exception:
            raise CeremonyError(_ERROR) from None

    def prepare_pairing(
        self,
        user_id: str,
        root: OwnerRoot,
        device_label: str,
        vault_kind: Literal["native", "encrypted_file"],
        password: str | None = None,
        *,
        now: int | None = None,
        authorize: Callable[[], None],
    ) -> dict:
        try:
            self._authorize(authorize)
            installed = self.control.get("installation")
            if (
                installed is not None
                and self.service._state(installed, user_id, abandoned=True).phase != "abandoned"
            ):
                raise CeremonyError(_ERROR)
            root = OwnerRoot.model_validate(root.model_dump())
            now = int(time.time()) if now is None else now
            if type(now) is not int or now < 0:
                raise CeremonyError(_ERROR)
            existing = self.control.get("ceremony/pairing")
            if existing is not None:
                _, draft = self._draft(user_id)
            if existing is not None and draft.phase != "abandoned":
                if (
                    draft.root != root
                    or draft.device_label != device_label.strip()
                    or draft.vault_kind != vault_kind
                    or draft.phase not in {"preparing", "pending"}
                    or not draft.request.issued_at <= now < draft.request.expires_at
                ):
                    raise CeremonyError(_ERROR)
                keys = DeviceSecrets.from_bytes(
                    self._draft_vault(draft, password).get(draft.request.device.device_id) or b""
                )
                if (
                    keys.certificate(root.owner_id, draft.request.device.device_id)
                    != draft.request.device
                ):
                    raise CeremonyError(_ERROR)
                self._authorize(authorize)
                if draft.phase == "preparing":
                    draft = draft.model_copy(update={"phase": "pending"})
                    self.control.update(
                        existing.key, draft.model_dump(mode="json"), existing.revision
                    )
                return {
                    "request": draft.request.model_dump(mode="json"),
                    "fingerprint": pairing_fingerprint(draft.request),
                    "phase": draft.phase,
                }
            device = generate_device()
            request = PairingRequest(
                root_digest=digest(root),
                invitation_id=str(uuid4()),
                device=device.certificate(root.owner_id, str(uuid4())),
                issued_at=now,
                expires_at=now + 900,
            )
            draft = PairingDraft(
                local_user_id=user_id,
                root=root,
                request=request,
                device_label=device_label.strip(),
                vault_kind=vault_kind,
                phase="preparing",
            )
            vault = self._draft_vault(draft, password)
            self._authorize(authorize)
            saved = (
                self.control.update(
                    "ceremony/pairing", draft.model_dump(mode="json"), existing.revision
                )
                if existing
                else self.control.create("ceremony/pairing", draft.model_dump(mode="json"))
            )
            vault.put(request.device.device_id, device.to_bytes())
            if vault.get(request.device.device_id) != device.to_bytes():
                raise CeremonyError(_ERROR)
            self._authorize(authorize)
            draft = draft.model_copy(update={"phase": "pending"})
            self.control.update(saved.key, draft.model_dump(mode="json"), saved.revision)
            return {
                "request": request.model_dump(mode="json"),
                "fingerprint": pairing_fingerprint(request),
                "phase": "pending",
            }
        except Exception:
            raise CeremonyError(_ERROR) from None

    def abandon_pairing(
        self, user_id: str, password: str | None = None, *, authorize: Callable[[], None]
    ) -> None:
        try:
            self._authorize(authorize)
            record, draft = self._draft(user_id)
            installation = self.control.get("installation")
            if draft.phase not in {"preparing", "pending"} or (
                installation is not None
                and self.service._state(installation, user_id, abandoned=True).phase != "abandoned"
            ):
                raise CeremonyError(_ERROR)
            self._authorize(authorize)
            abandoned = draft.model_copy(update={"phase": "abandoned"})
            self.control.batch(
                (
                    ControlMutation(record.key, abandoned.model_dump(mode="json"), record.revision),
                    ControlMutation(
                        "abandoned-pairing/" + draft.request.invitation_id,
                        abandoned.model_dump(mode="json"),
                    ),
                )
            )
            # Tombstone is authoritative even if a locked native vault refuses cleanup.
            try:
                self._draft_vault(draft, password).delete(draft.request.device.device_id)
            except Exception:
                pass
        except Exception:
            raise CeremonyError(_ERROR) from None

    def connect_relay(
        self,
        user_id: str,
        registration_token: str,
        password: str | None = None,
        *,
        authorize: Callable[[], None],
    ) -> dict:
        try:
            installation, state = self._owner(user_id, authorize)
            authority, device = self.service._keys(state, password)
            self._authorize(authorize)
            key = "ceremony/relay"
            old = self.control.get(key)
            payload = {
                "local_user_id": user_id,
                "root_digest": digest(state.root),
                "phase": "pending",
            }
            if old is not None and (
                old.payload.get("local_user_id") != user_id
                or old.payload.get("root_digest") != digest(state.root)
            ):
                raise CeremonyError(_ERROR)
            record = (
                self.control.update(key, payload, old.revision)
                if old
                else self.control.create(key, payload)
            )
            with self._client_factory(state.root, state.device_id, device) as client:
                try:
                    result = client.register(authority, state.membership, registration_token)
                    if result != {
                        "owner_id": state.root.owner_id,
                        "membership_digest": digest(state.membership),
                    }:
                        raise CeremonyError(_ERROR)
                except RelayConflict:
                    pass
                self._authorize(authorize)
                current = client.rpc({"operation": "membership"})
                root = OwnerRoot.model_validate(current["root"])
                membership = SignedMembership.model_validate(current["membership"])
                if root != state.root:
                    raise CeremonyError(_ERROR)
                verify_membership(root, membership, trusted_digest=state.membership_digest)
            self._authorize(authorize)
            self.control.batch(
                (
                    ControlMutation(
                        "installation", state.model_dump(mode="json"), installation.revision
                    ),
                    ControlMutation(key, dict(payload, phase="connected"), record.revision),
                )
            )
            return {"connected": True, "relay_url": state.root.relay_url}
        except Exception:
            raise CeremonyError(_ERROR) from None

    def _connected(self, user_id: str, root: OwnerRoot) -> None:
        connection = self.control.get("ceremony/relay")
        if connection is None or connection.payload != {
            "local_user_id": user_id,
            "root_digest": digest(root),
            "phase": "connected",
        }:
            raise CeremonyError(_ERROR)

    def _publish_pending(
        self, user_id: str, password: str | None, authorize: Callable[[], None]
    ) -> SignedMembership:
        _, state = self._owner(user_id, authorize)
        record = self.control.get("ceremony/membership")
        if record is None or record.payload.get("local_user_id") != user_id:
            raise CeremonyError(_ERROR)
        p = record.payload
        successor = SignedMembership.model_validate(p["membership"])
        verify_membership(state.root, successor, trusted_digest=digest(successor))
        if p["phase"] == "published":
            if state.membership_digest != digest(successor):
                raise CeremonyError(_ERROR)
            return successor
        if p["phase"] != "pending" or state.membership_digest not in {
            p["previous_digest"],
            digest(successor),
        }:
            raise CeremonyError(_ERROR)
        device = self.service.device_keys(user_id, password, authorize=authorize)
        self._authorize(authorize)
        with self._client_factory(state.root, state.device_id, device) as client:
            current = client.rpc({"operation": "membership"})
            if OwnerRoot.model_validate(current["root"]) != state.root:
                raise CeremonyError(_ERROR)
            remote = SignedMembership.model_validate(current["membership"])
            if digest(remote) == p["previous_digest"]:
                verify_membership(state.root, remote, trusted_digest=p["previous_digest"])
                verify_membership(state.root, successor, previous=remote)
                # The preceding read can block: session authority may have been
                # revoked while waiting, before this externally visible mutation.
                self._authorize(authorize)
                response = client.rpc(
                    {
                        "operation": "update_membership",
                        "membership": successor.model_dump(mode="json"),
                    }
                )
                remote = SignedMembership.model_validate(response["membership"])
            verify_membership(state.root, remote, trusted_digest=digest(successor))
        self._authorize(authorize)
        installation, latest = self.service._read(user_id)
        pending = self.control.get(record.key)
        if pending != record or latest.membership_digest not in {
            p["previous_digest"],
            digest(successor),
        }:
            raise CeremonyError(_ERROR)
        updated = latest.model_copy(
            update={"membership": successor, "membership_digest": digest(successor)}
        )
        self.control.batch(
            (
                ControlMutation(
                    "installation", updated.model_dump(mode="json"), installation.revision
                ),
                ControlMutation(record.key, dict(p, phase="published"), record.revision),
            )
        )
        return successor

    def approve_request(
        self,
        user_id: str,
        request: PairingRequest,
        confirmed_fingerprint: str,
        password: str | None = None,
        *,
        now: int | None = None,
        authorize: Callable[[], None],
    ) -> PairingApproval:
        try:
            installation, state = self._owner(user_id, authorize)
            self._connected(user_id, state.root)
            request = PairingRequest.model_validate(request.model_dump())
            now = int(time.time()) if now is None else now
            if (
                type(now) is not int
                or request.root_digest != digest(state.root)
                or request.device.owner_id != state.root.owner_id
                or confirmed_fingerprint != pairing_fingerprint(request)
            ):
                raise CeremonyError(_ERROR)
            invitation_key = "invitation/" + request.invitation_id
            invitation = self.control.get(invitation_key)
            pending = self.control.get("ceremony/membership")
            if invitation is not None:
                if invitation.payload.get("local_user_id") != user_id or invitation.payload.get(
                    "request"
                ) != request.model_dump(mode="json"):
                    raise CeremonyError(_ERROR)
                if invitation.payload.get("phase") == "consumed":
                    approval = PairingApproval.model_validate(invitation.payload["approval"])
                    if (
                        pending is None
                        or pending.payload.get("invitation_id") != request.invitation_id
                    ):
                        raise CeremonyError(_ERROR)
                    self._publish_pending(user_id, password, authorize)
                    return approval
            if not request.issued_at <= now < request.expires_at:
                raise CeremonyError(_ERROR)
            if pending is not None and pending.payload.get("phase") == "pending":
                if pending.payload.get("invitation_id") != request.invitation_id:
                    raise CeremonyError(_ERROR)
                checkpoints = tuple(
                    TrustedCheckpoint.model_validate(c) for c in pending.payload["checkpoints"]
                )
                approved_at = pending.payload["approved_at"]
            else:
                authority, _ = self.service._keys(state, password)
                checkpoints = tuple(self._checkpoints())
                if len(checkpoints) > 64:
                    raise CeremonyError(_ERROR)
                checkpoints = tuple(
                    TrustedCheckpoint.model_validate(c.model_dump()) for c in checkpoints
                )
                successor = approve_membership(
                    state.root,
                    authority.authority_key,
                    state.membership.membership.devices + (request.device,),
                    previous=state.membership,
                )
                p = {
                    "local_user_id": user_id,
                    "phase": "pending",
                    "kind": "pairing",
                    "previous_digest": state.membership_digest,
                    "membership": successor.model_dump(mode="json"),
                    "invitation_id": request.invitation_id,
                    "approved_at": now,
                    "checkpoints": [c.model_dump(mode="json") for c in checkpoints],
                }
                self._authorize(authorize)
                mutations = [
                    ControlMutation(
                        "installation", state.model_dump(mode="json"), installation.revision
                    ),
                    ControlMutation(
                        "ceremony/membership", p, pending.revision if pending else None
                    ),
                ]
                if invitation is None:
                    mutations.append(
                        ControlMutation(
                            invitation_key,
                            {
                                "phase": "pending",
                                "local_user_id": user_id,
                                "request": request.model_dump(mode="json"),
                            },
                        )
                    )
                self.control.batch(mutations)
                approved_at = now
            approval = self.service.approve_pairing_invitation(
                user_id,
                request,
                confirmed_fingerprint,
                password,
                now=approved_at,
                checkpoints=checkpoints,
                authorize=authorize,
            )
            self._publish_pending(user_id, password, authorize)
            return approval
        except Exception:
            raise CeremonyError(_ERROR) from None

    def accept_approval(
        self,
        user_id: str,
        approval: PairingApproval,
        confirmed_fingerprint: str,
        password: str | None = None,
        *,
        now: int | None = None,
        authorize: Callable[[], None],
    ) -> dict:
        try:
            self._authorize(authorize)
            record, draft = self._draft(user_id)
            if draft.phase not in {"pending", "consumed"}:
                raise CeremonyError(_ERROR)
            device = DeviceSecrets.from_bytes(
                self._draft_vault(draft, password).get(draft.request.device.device_id) or b""
            )
            if (
                device.certificate(draft.root.owner_id, draft.request.device.device_id)
                != draft.request.device
            ):
                raise CeremonyError(_ERROR)
            self._authorize(authorize)
            received = self.control.get("received/" + draft.request.invitation_id)
            if received is not None and received.payload.get("phase") == "consumed":
                if received.payload.get("approval") != approval.model_dump(
                    mode="json"
                ) or confirmed_fingerprint != pairing_fingerprint(draft.request):
                    raise CeremonyError(_ERROR)
                result = self.service.status(user_id, authorize=authorize)
            else:
                result = self.service.consume_pairing_approval(
                    user_id,
                    draft.root,
                    draft.request,
                    approval,
                    device,
                    draft.device_label,
                    draft.vault_kind,
                    password,
                    now=int(time.time()) if now is None else now,
                    confirmed_fingerprint=confirmed_fingerprint,
                    authorize=authorize,
                )
            self._authorize(authorize)
            if draft.phase != "consumed":
                self.control.update(
                    record.key,
                    draft.model_copy(update={"phase": "consumed"}).model_dump(mode="json"),
                    record.revision,
                )
            return result
        except Exception:
            raise CeremonyError(_ERROR) from None

    def list_devices(self, user_id: str, *, authorize: Callable[[], None]) -> dict:
        try:
            self._authorize(authorize)
            record, state = self.service._read(user_id)
            devices = [
                {
                    "device_id": d.device_id,
                    "signing_public": d.signing_public,
                    "encryption_public": d.encryption_public,
                    "current": d.device_id == state.device_id,
                    "label": state.device_label if d.device_id == state.device_id else None,
                }
                for d in state.membership.membership.devices
            ]
            self._authorize(authorize)
            if self.control.get("installation") != record:
                raise CeremonyError(_ERROR)
            return {"membership_epoch": state.membership.membership.epoch, "devices": devices}
        except Exception:
            raise CeremonyError(_ERROR) from None

    def _anchor_before_revocation(self, user_id, state, device, authorize):
        """Observe signed heads before raising the epoch floor; never delay an
        urgent revocation indefinitely or treat an unavailable head as verified.
        """
        from .engine import SyncEngine

        records = self.control.list("profile/")
        if not records:
            return None
        deadline = time.monotonic() + 15
        attempted = anchored = 0
        with self._client_factory(state.root, state.device_id, device) as client:
            ceremony = self

            class ReadOnlyClient:
                root, device_id = client.root, client.device_id

                def rpc(self, payload):
                    ceremony._authorize(authorize)
                    if time.monotonic() >= deadline:
                        raise CeremonyError("Profile checkpoint scan budget exhausted")
                    if payload.get("operation") not in {
                        "membership",
                        "memberships",
                        "history",
                        "checkpoint",
                    }:
                        raise CeremonyError(_ERROR)
                    response = client.rpc(payload)
                    ceremony._authorize(authorize)
                    return response

            engine = SyncEngine(self.service, user_id, ReadOnlyClient())
            for record in records[:64]:
                if time.monotonic() >= deadline:
                    break
                self._authorize(authorize)
                attempted += 1
                try:
                    local_id = record.key.removeprefix("profile/")
                    engine.reconcile(local_id)
                    anchored += 1
                except Exception:
                    # Reconciliation itself retains uncertainty/fork evidence.
                    # Revoking a lost device must remain possible nonetheless.
                    pass
                self._authorize(authorize)
        return {
            "attempted": attempted,
            "anchored": anchored,
            "unavailable": len(records) - anchored,
        }

    @staticmethod
    def _revocation_result(epoch, device_id, scan=None):
        result = {"membership_epoch": epoch, "revoked_device_id": device_id}
        if scan is not None:
            result["checkpoint_scan"] = scan
            if scan.get("unavailable", 0):
                result["warning"] = (
                    "Some profile heads could not be verified before revocation; those profiles may require historical-copy recovery."
                )
        return result

    def revoke_device(
        self,
        user_id: str,
        device_id: str,
        password: str | None = None,
        *,
        authorize: Callable[[], None],
    ) -> dict:
        try:
            installation, state = self._owner(user_id, authorize)
            self._connected(user_id, state.root)
            pending = self.control.get("ceremony/membership")
            if (
                pending is not None
                and pending.payload.get("phase") == "published"
                and pending.payload.get("kind") == "revocation"
                and pending.payload.get("revoked_device_id") == device_id
                and pending.payload.get("local_user_id") == user_id
                and pending.payload.get("membership") == state.membership.model_dump(mode="json")
            ):
                self._authorize(authorize)
                return self._revocation_result(
                    state.membership.membership.epoch,
                    device_id,
                    pending.payload.get("checkpoint_scan"),
                )
            if pending is not None and pending.payload.get("phase") == "pending":
                if (
                    pending.payload.get("kind") != "revocation"
                    or pending.payload.get("revoked_device_id") != device_id
                ):
                    raise CeremonyError(_ERROR)
            else:
                if device_id == state.device_id or device_id not in {
                    d.device_id for d in state.membership.membership.devices
                }:
                    raise CeremonyError(_ERROR)
                authority, device = self.service._keys(state, password)
                scan = self._anchor_before_revocation(user_id, state, device, authorize)
                installation, state = self._owner(user_id, authorize)
                if device_id not in {d.device_id for d in state.membership.membership.devices}:
                    return self._revocation_result(
                        state.membership.membership.epoch, device_id, scan
                    )
                successor = approve_membership(
                    state.root,
                    authority.authority_key,
                    tuple(
                        d for d in state.membership.membership.devices if d.device_id != device_id
                    ),
                    previous=state.membership,
                )
                p = {
                    "local_user_id": user_id,
                    "phase": "pending",
                    "kind": "revocation",
                    "previous_digest": state.membership_digest,
                    "membership": successor.model_dump(mode="json"),
                    "revoked_device_id": device_id,
                    "checkpoint_scan": scan,
                }
                self._authorize(authorize)
                self.control.batch(
                    (
                        ControlMutation(
                            "installation", state.model_dump(mode="json"), installation.revision
                        ),
                        ControlMutation(
                            "ceremony/membership", p, pending.revision if pending else None
                        ),
                    )
                )
            membership = self._publish_pending(user_id, password, authorize)
            finished = self.control.get("ceremony/membership")
            return self._revocation_result(
                membership.membership.epoch,
                device_id,
                finished.payload.get("checkpoint_scan") if finished else None,
            )
        except Exception:
            raise CeremonyError(_ERROR) from None

    def resume_membership(
        self, user_id: str, password: str | None = None, *, authorize: Callable[[], None]
    ) -> dict:
        try:
            self._owner(user_id, authorize)
            pending = self.control.get("ceremony/membership")
            if pending is None or pending.payload.get("local_user_id") != user_id:
                raise CeremonyError(_ERROR)
            if pending.payload.get("kind") == "pairing":
                invitation = self.control.get("invitation/" + pending.payload["invitation_id"])
                if invitation is None:
                    raise CeremonyError(_ERROR)
                if invitation.payload.get("phase") == "pending":
                    # The exact request/fingerprint decision was already durably
                    # authorized before interruption. Resume that decision at
                    # its recorded approval time, never accept a fresh stale request.
                    request = PairingRequest.model_validate(invitation.payload["request"])
                    checkpoints = tuple(
                        TrustedCheckpoint.model_validate(c) for c in pending.payload["checkpoints"]
                    )
                    approval = self.service.approve_pairing_invitation(
                        user_id,
                        request,
                        pairing_fingerprint(request),
                        password,
                        now=pending.payload["approved_at"],
                        checkpoints=checkpoints,
                        authorize=authorize,
                    )
                    if approval.membership.model_dump(mode="json") != pending.payload["membership"]:
                        raise CeremonyError(_ERROR)
                elif invitation.payload.get("phase") != "consumed":
                    raise CeremonyError(_ERROR)
            membership = self._publish_pending(user_id, password, authorize)
            result = {"membership_epoch": membership.membership.epoch, "phase": "published"}
            if (
                pending.payload.get("kind") == "revocation"
                and pending.payload.get("checkpoint_scan") is not None
            ):
                detail = self._revocation_result(
                    membership.membership.epoch,
                    pending.payload["revoked_device_id"],
                    pending.payload["checkpoint_scan"],
                )
                result.update(
                    {
                        key: value
                        for key, value in detail.items()
                        if key in {"checkpoint_scan", "warning"}
                    }
                )
            return result
        except Exception:
            raise CeremonyError(_ERROR) from None


def protected_checkpoints(service: SyncService) -> tuple[TrustedCheckpoint, ...]:
    """Export only independently retained local anchors, without keys or networking.

    These are anti-rollback lower bounds, not a claim of globally fresh relay state.
    Recipients still verify signed history extending every transferred anchor.
    """
    from camoufox_pm.sync.engine import Binding

    try:
        installation = service.control.get("installation")
        if installation is None:
            raise CeremonyError(_ERROR)
        _, owner = service._read(installation.payload["local_user_id"])
        if owner.phase != "ready" or not owner.authority:
            raise CeremonyError(_ERROR)
        flags = {
            key: service.control.get(key)
            for key in ("sync/revoked", "sync/membership-conflict", "sync/restore-fence")
        }
        if flags["sync/revoked"] is not None or flags["sync/membership-conflict"] is not None:
            raise CeremonyError(_ERROR)
        fence = flags["sync/restore-fence"]
        if fence is not None and fence.payload.get("phase") != "complete":
            raise CeremonyError(_ERROR)
        observations = service.control.list("membership/")
        for record in observations:
            member = SignedMembership.model_validate(record.payload)
            verify_membership(owner.root, member, trusted_digest=digest(member))
            epoch = member.membership.epoch
            if (
                record.key != "membership/" + str(epoch)
                or epoch > owner.membership.membership.epoch
                or (
                    epoch == owner.membership.membership.epoch
                    and digest(member) != owner.membership_digest
                )
            ):
                raise CeremonyError(_ERROR)
        records = service.control.list("profile/")
        result = []
        checked: list[tuple[str, ControlRecord | None]] = []
        seen = set()
        for record in records:
            state = Binding.model_validate(record.payload)
            cp = state.checkpoint
            if (
                record.key != "profile/" + state.local_id
                or cp.owner_id != owner.root.owner_id
                or cp.membership_epoch != owner.membership.membership.epoch
                or state.phase not in {"held", "released", "held_elsewhere"}
                or state.pending
                or state.forked
                or state.handoff
                or cp.profile_id in seen
            ):
                raise CeremonyError(_ERROR)
            seen.add(cp.profile_id)
            mapping = service.control.get("mapping/" + cp.profile_id)
            applied = service.control.get("apply/" + state.local_id)
            if mapping is None or mapping.payload != {"local_id": state.local_id}:
                raise CeremonyError(_ERROR)
            if applied is not None and applied.payload.get("phase") not in {"complete", "aborted"}:
                raise CeremonyError(_ERROR)
            checked.extend(((mapping.key, mapping), ("apply/" + state.local_id, applied)))
            result.append(
                TrustedCheckpoint(
                    profile_id=cp.profile_id,
                    revision_id=cp.revision_id,
                    checkout_generation=cp.checkout_generation,
                    holder_device_id=cp.holder_device_id,
                )
            )
        if (
            service.control.get("installation") != installation
            or service.control.list("membership/") != observations
            or service.control.list("profile/") != records
            or any(service.control.get(key) != value for key, value in (*flags.items(), *checked))
        ):
            raise CeremonyError(_ERROR)
        return tuple(sorted(result, key=lambda checkpoint: checkpoint.profile_id))
    except Exception:
        raise CeremonyError(_ERROR) from None
