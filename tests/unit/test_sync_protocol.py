"""Untrusted wire input and authenticated-context mismatch regressions."""

import base64
import hashlib
import json

import pytest

from camoufox_pm.sync.protocol import (
    ENCRYPTION_DOMAIN,
    MAX_MANIFEST_BYTES,
    SIGNING_DOMAIN,
    SUITE,
    SnapshotContext,
    canonical_bytes,
    encryption_context_bytes,
    parse_manifest,
    signing_bytes,
    validate_context,
)


def identity(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def wire():
    return {
        "version": 1,
        "suite": SUITE,
        "owner_id": identity(1),
        "profile_id": identity(2),
        "revision_id": identity(3),
        "parent_revision_id": None,
        "writer_device_id": identity(4),
        "membership_epoch": 1,
        "checkout_generation": 2,
        "ciphertext_sha256": "ab" * 32,
        "ciphertext_length": 100,
        "recipients": [
            {
                "kind": kind,
                "recipient_id": identity(n),
                "enc": base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode(),
                "wrapped_key": base64.urlsafe_b64encode(bytes(48)).decode(),
            }
            for kind, n in (("device", 4), ("recovery", 5))
        ],
    }


def parse(value):
    return parse_manifest(json.dumps(value).encode())


def context():
    values = wire()
    return SnapshotContext(
        **{
            k: values[k]
            for k in (
                "owner_id",
                "profile_id",
                "revision_id",
                "parent_revision_id",
                "writer_device_id",
                "membership_epoch",
                "checkout_generation",
            )
        },
        recipient_identities=(("device", identity(4)), ("recovery", identity(5))),
    )


def test_canonical_roundtrip_and_domain_separation():
    manifest = parse(wire())
    expected = json.dumps(wire(), sort_keys=True, separators=(",", ":")).encode()
    assert canonical_bytes(manifest) == expected
    assert parse_manifest(expected) == manifest
    assert signing_bytes(manifest) == SIGNING_DOMAIN + expected
    assert encryption_context_bytes(manifest).startswith(ENCRYPTION_DOMAIN)
    assert SIGNING_DOMAIN != ENCRYPTION_DOMAIN
    validate_context(manifest, context())
    with pytest.raises(ValueError):
        manifest.membership_epoch = 9


def test_fixed_canonical_context_vectors():
    manifest = parse(wire())
    assert hashlib.sha256(signing_bytes(manifest)).hexdigest() == (
        "f9b3b66a2d8da2ea36852d3d2bd0f376f62bf86a23196c5a88cd973bd6b64f12"
    )
    assert hashlib.sha256(encryption_context_bytes(manifest)).hexdigest() == (
        "a304a1e5c8d069cb002e02f0b0e07bcf5c08fe3143f9cf764a982a6c6560eb09"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", True),
        ("version", "1"),
        ("suite", "unknown"),
        ("membership_epoch", 0),
        ("membership_epoch", True),
        ("membership_epoch", 2**53),
        ("checkout_generation", -1),
        ("ciphertext_length", 40),
        ("ciphertext_length", 2**40),
        ("ciphertext_sha256", "AB" * 32),
        ("ciphertext_sha256", "ab" * 31),
        ("owner_id", "../secret"),
        ("owner_id", "00000000-0000-0000-0000-000000000000"),
        ("profile_id", "00000000000040008000000000000002"),
        ("revision_id", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        ("parent_revision_id", identity(3)),
        ("unexpected", "secret"),
    ],
)
def test_strict_fields(field, value):
    values = wire()
    values[field] = value
    with pytest.raises(ValueError):
        parse(values)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version":1,"version":2}',
        b'{"version":1.0}',
        b'{"version":1e0}',
        b'{"version":NaN}',
        b'{"version":Infinity}',
        b"[]",
        b"null",
        b"{} {}",
        b"\xff",
        b"[" * 9 + b"]" * 9,
        b" " * (MAX_MANIFEST_BYTES + 1),
        b"",
    ],
    ids=[
        "duplicate",
        "float",
        "exponent",
        "nan",
        "infinity",
        "array",
        "null",
        "trailing",
        "utf8",
        "depth",
        "oversize",
        "empty",
    ],
)
def test_malformed_or_unbounded_json(raw):
    with pytest.raises(ValueError):
        parse_manifest(raw)


def test_nested_duplicate_member_rejected():
    raw = json.dumps(wire()).replace('"kind": "device"', '"kind": "device", "kind": "device"')
    with pytest.raises(ValueError, match="duplicate"):
        parse_manifest(raw.encode())


@pytest.mark.parametrize(
    "mutation",
    ["reverse", "duplicate", "no_recovery", "no_writer", "extra", "padding", "tail_bits", "many"],
)
def test_recipient_constraints(mutation):
    values = wire()
    recipients = values["recipients"]
    if mutation == "reverse":
        recipients.reverse()
    elif mutation == "duplicate":
        recipients.insert(0, recipients[0].copy())
    elif mutation == "no_recovery":
        recipients[1]["kind"] = "device"
    elif mutation == "no_writer":
        recipients[0]["recipient_id"] = identity(6)
    elif mutation == "extra":
        recipients[0]["secret"] = "not allowed"
    elif mutation == "padding":
        recipients[0]["enc"] += "="
    elif mutation == "tail_bits":
        recipients[0]["enc"] = "A" * 42 + "B"
    else:
        values["recipients"] = [
            dict(recipients[0], recipient_id=identity(n)) for n in range(4, 134)
        ] + [recipients[1]]
    with pytest.raises(ValueError):
        parse(values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", identity(9)),
        ("profile_id", identity(9)),
        ("revision_id", identity(9)),
        ("parent_revision_id", identity(9)),
        ("writer_device_id", identity(9)),
        ("membership_epoch", 2),
        ("checkout_generation", 3),
        ("recipient_identities", (("device", identity(4)), ("recovery", identity(9)))),
    ],
)
def test_authenticated_context_must_match_exactly(field, value):
    expected = context().model_copy(update={field: value})
    with pytest.raises(ValueError, match="mismatch"):
        validate_context(parse(wire()), expected)


def test_sibling_revision_cannot_replace_selected_snapshot():
    sibling = wire()
    sibling["revision_id"] = identity(8)
    # Both manifests have the same parent, writer, epoch, generation and recipients.
    # A valid signature would not establish which revision was selected by checkout.
    with pytest.raises(ValueError, match="revision_id"):
        validate_context(parse(sibling), context())


def test_selected_revision_is_required_in_trusted_context():
    values = context().model_dump()
    del values["revision_id"]
    with pytest.raises(ValueError, match="revision_id"):
        SnapshotContext.model_validate(values)


def test_encryption_context_excludes_outputs_but_signatures_bind_them():
    original = parse(wire())
    values = wire()
    values["ciphertext_sha256"] = "cd" * 32
    values["ciphertext_length"] = 200
    values["recipients"][0]["wrapped_key"] = base64.urlsafe_b64encode(bytes([1]) * 48).decode()
    changed = parse(values)
    assert encryption_context_bytes(changed) == encryption_context_bytes(original)
    assert signing_bytes(changed) != signing_bytes(original)
    values["recipients"][1]["recipient_id"] = identity(6)
    assert encryption_context_bytes(parse(values)) != encryption_context_bytes(original)
