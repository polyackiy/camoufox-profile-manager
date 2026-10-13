"""Adversarial owner identity and pairing tests; no network or persistent secrets."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from camoufox_pm.sync.identity import (
    MAX_IDENTITY_BYTES,
    AuthoritySecrets,
    DeviceSecrets,
    OwnerRoot,
    PairingRequest,
    SignedMembership,
    TrustedCheckpoint,
    approve_membership,
    approve_pairing,
    bootstrap,
    canonical_bytes,
    digest,
    generate_device,
    normalize_relay_url,
    pairing_fingerprint,
    parse_record,
    verify_membership,
    verify_pairing,
)


def ceremony():
    root, secrets, first = bootstrap("https://relay.example")
    new_device = generate_device().certificate(root.owner_id, str(uuid4()))
    second = approve_membership(
        root, secrets.authority_key, first.membership.devices + (new_device,), previous=first
    )
    request = PairingRequest(
        root_digest=digest(root),
        invitation_id=str(uuid4()),
        device=new_device,
        issued_at=100,
        expires_at=300,
    )
    checkpoint = TrustedCheckpoint(
        profile_id=str(uuid4()),
        revision_id=str(uuid4()),
        checkout_generation=1,
        holder_device_id=first.membership.devices[0].device_id,
    )
    approval = approve_pairing(
        root,
        secrets.authority_key,
        request,
        second,
        (checkpoint,),
        now=150,
        confirmed_fingerprint=pairing_fingerprint(request),
    )
    return root, secrets, first, second, request, approval


def test_random_bootstrap_separated_and_redacted():
    root, secrets, first = bootstrap("https://relay.example")
    other, _, _ = bootstrap("https://relay.example")
    assert root != other
    assert verify_membership(root, first) == first.membership
    assert root.authority_public != first.membership.devices[0].signing_public
    assert root.recovery_public != first.membership.devices[0].encryption_public
    assert "redacted" in repr(secrets) and "redacted" in repr(secrets.device)
    assert "redacted" in repr(secrets.authority)
    device_bytes = secrets.device.to_bytes()
    authority_bytes = secrets.authority.to_bytes()
    assert secrets.authority_key.private_bytes_raw() not in device_bytes
    assert secrets.device.signing_key.private_bytes_raw() not in authority_bytes
    assert AuthoritySecrets.from_bytes(authority_bytes).to_bytes() == authority_bytes
    assert DeviceSecrets.from_bytes(device_bytes).to_bytes() == device_bytes
    with pytest.raises(ValueError):
        DeviceSecrets.from_bytes(authority_bytes)
    with pytest.raises(ValueError):
        AuthoritySecrets.from_bytes(device_bytes)


@pytest.mark.parametrize(
    "url",
    [
        "http://relay.example",
        "https://user:pass@relay.example",
        "https://relay.example/path",
        "https://relay.example?x=1",
        "https://relay.example#x",
        "file:///tmp",
        "https://relay.example:0",
        "https://relay.example\\evil",
        "https://relay.example\n",
    ],
)
def test_bad_relay_origins(url):
    with pytest.raises(ValueError):
        bootstrap(url)


def test_relay_normalization_and_explicit_loopback():
    assert normalize_relay_url("https://RELAY.example:443/") == "https://relay.example"
    assert normalize_relay_url("https://[::1]:8443") == "https://[::1]:8443"
    with pytest.raises(ValueError):
        bootstrap("http://127.0.0.1:8000")
    root, _, _ = bootstrap("http://127.0.0.1:8000", allow_loopback_http=True)
    assert root.development_loopback
    with pytest.raises(ValueError):
        bootstrap("http://remote.example", allow_loopback_http=True)


def test_owner_or_relay_substitution():
    root, _, first, _, _, _ = ceremony()
    for update in ({"owner_id": str(uuid4())}, {"relay_url": "https://attacker.example"}):
        with pytest.raises(ValueError):
            verify_membership(root.model_copy(update=update), first)


def test_old_epoch_fork_gap_and_tamper():
    root, secrets, first, second, _, _ = ceremony()
    assert verify_membership(root, second, previous=first) == second.membership
    with pytest.raises(ValueError):
        verify_membership(root, second)
    with pytest.raises(ValueError):
        verify_membership(root, first, previous=second)
    with pytest.raises(ValueError):
        verify_membership(root, first, trusted_digest=digest(second))
    alternate_device = generate_device().certificate(root.owner_id, str(uuid4()))
    fork = approve_membership(
        root, secrets.authority_key, first.membership.devices + (alternate_device,), previous=first
    )
    with pytest.raises(ValueError):
        verify_membership(root, fork, trusted_digest=digest(second))
    third = approve_membership(
        root, secrets.authority_key, second.membership.devices, previous=second
    )
    with pytest.raises(ValueError):
        verify_membership(root, third, previous=first)
    tampered = second.model_copy(
        update={"membership": second.membership.model_copy(update={"epoch": 3})}
    )
    with pytest.raises(InvalidSignature):
        verify_membership(root, tampered, previous=first)


def test_revocation_excludes_removed_device_and_old_epoch():
    root, secrets, first, second, request, _ = ceremony()
    revoked = approve_membership(
        root, secrets.authority_key, first.membership.devices, previous=second
    )
    assert request.device not in verify_membership(root, revoked, previous=second).devices
    with pytest.raises(ValueError):
        verify_membership(root, second, trusted_digest=digest(revoked))
    with pytest.raises(ValueError):
        approve_pairing(
            root,
            secrets.authority_key,
            request,
            revoked,
            (),
            now=150,
            confirmed_fingerprint=pairing_fingerprint(request),
        )


def test_device_cannot_issue_membership():
    root, secrets, first = bootstrap("https://relay.example")
    with pytest.raises(ValueError):
        approve_membership(
            root, secrets.device.signing_key, first.membership.devices, previous=first
        )
    with pytest.raises(ValueError):
        approve_membership(root, Ed25519PrivateKey.generate(), first.membership.devices)


def test_duplicate_keys_and_authority_key_reuse():
    root, secrets, first = bootstrap("https://relay.example")
    cert = first.membership.devices[0]
    for new in (
        cert.model_copy(update={"device_id": str(uuid4())}),
        cert.model_copy(update={"signing_public": root.authority_public}),
        cert.model_copy(update={"encryption_public": root.recovery_public}),
    ):
        with pytest.raises(ValueError):
            approve_membership(root, secrets.authority_key, (cert, new))


def test_pairing_authenticates_membership_keys_and_checkpoint():
    root, _, _, second, request, approval = ceremony()
    assert (
        verify_pairing(
            root,
            request,
            approval,
            second,
            now=150,
            confirmed_fingerprint=pairing_fingerprint(request),
        )
        == approval.checkpoints
    )
    assert approval.membership == second
    assert parse_record(canonical_bytes(approval.membership), SignedMembership) == second
    tampered = approval.model_copy(update={"checkpoints": ()})
    with pytest.raises(InvalidSignature):
        verify_pairing(
            root,
            request,
            tampered,
            second,
            now=150,
            confirmed_fingerprint=pairing_fingerprint(request),
        )


@pytest.mark.parametrize("now", [99, 300, 301, True])
def test_pairing_expiry(now):
    root, _, _, second, request, approval = ceremony()
    with pytest.raises(ValueError):
        verify_pairing(
            root,
            request,
            approval,
            second,
            now=now,
            confirmed_fingerprint=pairing_fingerprint(request),
        )


def test_pairing_request_substitution_and_fingerprint():
    root, _, _, second, request, approval = ceremony()
    with pytest.raises(ValueError):
        verify_pairing(root, request, approval, second, now=150, confirmed_fingerprint="0" * 64)
    for changed in (
        request.model_copy(update={"invitation_id": str(uuid4())}),
        request.model_copy(
            update={
                "device": generate_device().certificate(root.owner_id, request.device.device_id)
            }
        ),
    ):
        with pytest.raises(ValueError):
            verify_pairing(
                root,
                changed,
                approval,
                second,
                now=150,
                confirmed_fingerprint=pairing_fingerprint(changed),
            )


def test_parser_strictness_bounds_and_construct_revalidation():
    root, _, _ = bootstrap("https://relay.example")
    assert parse_record(canonical_bytes(root), OwnerRoot) == root
    raw = json.loads(canonical_bytes(root))
    for change in (
        {"version": True},
        {"version": 2},
        {"extra": 1},
        {"authority_public": "!" * 43},
        {"owner_id": "x"},
    ):
        with pytest.raises(ValueError):
            parse_record(json.dumps(raw | change).encode(), OwnerRoot)
    with pytest.raises(ValueError):
        parse_record(b'{"version":1,"version":1}', OwnerRoot)
    with pytest.raises(ValueError):
        parse_record(b" " * (MAX_IDENTITY_BYTES + 1), OwnerRoot)
    with pytest.raises(ValueError):
        parse_record(b"[" * 1000 + b"]" * 1000, OwnerRoot)
    with pytest.raises(ValueError):
        canonical_bytes(root.model_copy(update={"version": True}))


def test_retained_device_uuid_cannot_replace_its_keys():
    root, keys, previous = bootstrap("https://relay.example")
    replacement = generate_device().certificate(
        root.owner_id, previous.membership.devices[0].device_id
    )
    with pytest.raises(ValueError, match="cannot change keys"):
        approve_membership(root, keys.authority_key, (replacement,), previous=previous)
