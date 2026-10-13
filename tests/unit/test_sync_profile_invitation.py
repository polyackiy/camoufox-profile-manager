"""Existing-device profile discovery requires independently signed owner codes."""

import copy
from uuid import uuid4

import pytest

from camoufox_pm.sync.engine import Binding, Checkpoint
from camoufox_pm.sync.profile_invitation import (
    ProfileInvitationError,
    accept_profile_code,
    create_profile_code,
    trusted_profile_checkpoints,
)
from tests.unit.test_sync_ceremonies import PEER, USER, authorize, enroll_peer
from tests.unit.test_sync_ceremonies import pair as pair_fixture

pair = pair_fixture


@pytest.fixture
def invitation(pair):
    request, _, _ = enroll_peer(pair)
    _, owner = pair.owner_service._read(USER)
    remote = str(uuid4())
    checkpoint = Checkpoint(
        owner_id=owner.root.owner_id,
        profile_id=remote,
        membership_epoch=owner.membership.membership.epoch,
        checkout_generation=2,
        revision_id=str(uuid4()),
        holder_device_id=None,
    )
    binding = Binding(local_id="new12345", checkpoint=checkpoint, phase="released")
    pair.owner_service.control.create("profile/new12345", binding.model_dump(mode="json"))
    pair.owner_service.control.create("mapping/" + remote, {"local_id": "new12345"})
    pack = create_profile_code(
        pair.owner_service, USER, request.device.device_id, authorize=authorize
    )
    return pair, pack, remote


def test_existing_paired_device_receives_new_profile_without_new_keys(invitation):
    pair, pack, remote = invitation
    original_keys = dict(pair.peer_vault.values)
    original_installation = pair.peer_service._read(PEER)[1]
    result = accept_profile_code(pair.peer_service, PEER, pack, authorize=authorize)
    assert result == {"accepted": True, "added": 1, "sequence": 1}
    checkpoints = trusted_profile_checkpoints(pair.peer_service, PEER)
    assert checkpoints[remote]["revision_id"] == pack["body"]["checkpoints"][0]["revision_id"]
    assert pair.peer_service._read(PEER)[1] == original_installation
    assert pair.peer_vault.values == original_keys
    assert pair.peer_service.control.get("profile/new12345") is None
    assert accept_profile_code(pair.peer_service, PEER, pack, authorize=authorize)["added"] == 0


def test_sequence_replay_and_existing_anchor_cannot_be_overwritten(invitation):
    pair, first, remote = invitation
    accept_profile_code(pair.peer_service, PEER, first, authorize=authorize)
    original = pair.peer_service.control.get("trusted-profile/" + remote)
    second = create_profile_code(
        pair.owner_service, USER, first["body"]["target_device_id"], authorize=authorize
    )
    assert accept_profile_code(pair.peer_service, PEER, second, authorize=authorize)["added"] == 0
    assert pair.peer_service.control.get("trusted-profile/" + remote) == original
    with pytest.raises(ProfileInvitationError):
        accept_profile_code(pair.peer_service, PEER, first, authorize=authorize)
    assert remote in trusted_profile_checkpoints(pair.peer_service, PEER)


@pytest.mark.parametrize(
    "field", ["signature", "root", "target", "checkpoint", "sequence", "membership"]
)
def test_code_tamper_rejected_without_anchor_write(invitation, field):
    pair, pack, remote = invitation
    modified = copy.deepcopy(pack)
    if field == "signature":
        modified["signature"] = "A" * 86
    elif field == "root":
        modified["body"]["root_digest"] = "0" * 64
    elif field == "target":
        modified["body"]["target_device_id"] = str(uuid4())
    elif field == "checkpoint":
        modified["body"]["checkpoints"][0]["revision_id"] = str(uuid4())
    elif field == "sequence":
        modified["body"]["sequence"] += 1
    elif field == "membership":
        modified["body"]["membership"]["membership"]["epoch"] += 1
    with pytest.raises(ProfileInvitationError):
        accept_profile_code(pair.peer_service, PEER, modified, authorize=authorize)
    assert pair.peer_service.control.get("trusted-profile/" + remote) is None


def test_wrong_local_owner_and_missing_authorization_rejected(invitation):
    pair, pack, _ = invitation
    for user, callback in (("other", authorize), (PEER, None)):
        with pytest.raises(ProfileInvitationError):
            accept_profile_code(pair.peer_service, user, pack, authorize=callback)
    with pytest.raises(ProfileInvitationError):
        create_profile_code(
            pair.peer_service,
            PEER,
            pair.owner_service._read(USER)[1].device_id,
            authorize=authorize,
        )


def test_existing_binding_is_not_replaced(invitation):
    pair, pack, remote = invitation
    pair.peer_service.control.create("mapping/" + remote, {"local_id": "already1"})
    result = accept_profile_code(pair.peer_service, PEER, pack, authorize=authorize)
    assert result["added"] == 0
    assert pair.peer_service.control.get("mapping/" + remote).payload == {"local_id": "already1"}
    assert pair.peer_service.control.get("trusted-profile/" + remote) is None


@pytest.mark.parametrize("flag", ["sync/revoked", "sync/membership-conflict"])
def test_known_revocation_and_membership_fork_fail_closed(invitation, flag):
    pair, pack, remote = invitation
    pair.peer_service.control.create(flag, {"epoch": 3})
    with pytest.raises(ProfileInvitationError):
        accept_profile_code(pair.peer_service, PEER, pack, authorize=authorize)
    assert pair.peer_service.control.get("trusted-profile/" + remote) is None


def test_authorization_revoked_before_atomic_accept(invitation):
    pair, pack, remote = invitation
    calls = 0

    def session():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise PermissionError("revoked")

    with pytest.raises(ProfileInvitationError):
        accept_profile_code(pair.peer_service, PEER, pack, authorize=session)
    assert pair.peer_service.control.get("trusted-profile/" + remote) is None


def test_original_unbound_pairing_anchor_is_not_replaced(invitation):
    pair, pack, _ = invitation
    _, owner = pair.owner_service._read(USER)
    original = pair.checkpoint
    binding = Binding(
        local_id="old12345",
        phase="released",
        checkpoint=Checkpoint(
            owner_id=owner.root.owner_id,
            profile_id=original.profile_id,
            revision_id=str(uuid4()),
            checkout_generation=original.checkout_generation + 1,
            holder_device_id=None,
            membership_epoch=owner.membership.membership.epoch,
        ),
    )
    pair.owner_service.control.create("profile/old12345", binding.model_dump(mode="json"))
    pair.owner_service.control.create("mapping/" + original.profile_id, {"local_id": "old12345"})
    new = create_profile_code(
        pair.owner_service, USER, pack["body"]["target_device_id"], authorize=authorize
    )
    assert len(new["body"]["checkpoints"]) == 2
    result = accept_profile_code(pair.peer_service, PEER, new, authorize=authorize)
    assert result["added"] == 1
    assert pair.peer_service.control.get("trusted-profile/" + original.profile_id) is None


def test_newer_observed_membership_cannot_be_hidden_by_older_code(invitation):
    from camoufox_pm.sync.identity import approve_membership

    pair, pack, remote = invitation
    _, owner = pair.owner_service._read(USER)
    authority, _ = pair.owner_service._keys(owner, None)
    newer = approve_membership(
        owner.root,
        authority.authority_key,
        owner.membership.membership.devices,
        previous=owner.membership,
    )
    pair.peer_service.control.create("membership/3", newer.model_dump(mode="json"))
    with pytest.raises(ProfileInvitationError):
        accept_profile_code(pair.peer_service, PEER, pack, authorize=authorize)
    assert pair.peer_service.control.get("trusted-profile/" + remote) is None


def test_concurrent_accept_is_atomic(invitation):
    from concurrent.futures import ThreadPoolExecutor

    pair, pack, remote = invitation

    def accept():
        try:
            return accept_profile_code(pair.peer_service, PEER, pack, authorize=authorize)
        except ProfileInvitationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: accept(), range(2)))
    assert any(result is not None for result in results)
    assert len(pair.peer_service.control.list("trusted-profile/")) == 1
    assert remote in trusted_profile_checkpoints(pair.peer_service, PEER)


def test_invitation_pins_exact_signed_membership_atomically(invitation):
    pair, pack, remote = invitation
    epoch = pack["body"]["membership"]["membership"]["epoch"]
    accept_profile_code(pair.peer_service, PEER, pack, authorize=authorize)
    pinned = pair.peer_service.control.get("membership/" + str(epoch))
    assert pinned.payload == pack["body"]["membership"]
    assert remote in trusted_profile_checkpoints(pair.peer_service, PEER)
