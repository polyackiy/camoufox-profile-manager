"""Experimental codec rejection/publication boundaries using real crypto."""

import base64
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("cryptography.hazmat.primitives.hpke")
pytest.importorskip("nacl.bindings")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from camoufox_pm.core.update_permissions import protect_path
from camoufox_pm.sync.crypto import (
    CHUNK_BYTES,
    SnapshotCryptoError,
    decrypt_file,
    encrypt_file,
)
from camoufox_pm.sync.protocol import SUITE, SnapshotContext, SnapshotManifest, signing_bytes


@pytest.fixture
def codec(tmp_path):
    protect_path(tmp_path)
    owner, profile, revision, writer, recovery = (str(uuid4()) for _ in range(5))
    recipients = [("device", writer), ("recovery", recovery)]
    private = {identity: X25519PrivateKey.generate() for identity in recipients}
    signer = Ed25519PrivateKey.generate()
    manifest = SnapshotManifest(
        version=1,
        suite=SUITE,
        owner_id=owner,
        profile_id=profile,
        revision_id=revision,
        parent_revision_id=None,
        writer_device_id=writer,
        membership_epoch=1,
        checkout_generation=1,
        ciphertext_sha256="00" * 32,
        ciphertext_length=45,
        recipients=tuple(
            {
                "kind": kind,
                "recipient_id": rid,
                "enc": base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode(),
                "wrapped_key": base64.urlsafe_b64encode(bytes(48)).decode(),
            }
            for kind, rid in recipients
        ),
    )
    expected = SnapshotContext(
        **{
            k: getattr(manifest, k)
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
        recipient_identities=tuple(recipients),
    )
    return tmp_path, manifest, expected, private, signer


def encrypted(codec, data=b"sensitive-session-cookies"):
    root, template, expected, private, signer = codec
    source, cipher = root / "source", root / "encrypted"
    source.write_bytes(data)
    manifest, signature = encrypt_file(
        source, cipher, template, {k: v.public_key() for k, v in private.items()}, signer
    )
    return manifest, signature


def decrypt(codec, manifest, signature, **changes):
    root, _, expected, private, signer = codec
    identity = next(iter(private))
    args = {
        "source": root / "encrypted",
        "destination": root / "restored",
        "manifest": manifest,
        "signature": signature,
        "expected": expected,
        "recipient_kind": identity[0],
        "recipient_id": identity[1],
        "recipient_key": private[identity],
        "trusted_writer_key": signer.public_key(),
    }
    args.update(changes)
    decrypt_file(**args)


@pytest.mark.parametrize("size", [0, 1, CHUNK_BYTES - 1, CHUNK_BYTES, CHUNK_BYTES + 19])
def test_roundtrip(codec, size):
    data = b"p" * size
    manifest, signature = encrypted(codec, data)
    decrypt(codec, manifest, signature)
    assert (codec[0] / "restored").read_bytes() == data
    assert not list(codec[0].glob(".sync-*"))


def test_recovery_recipient(codec):
    manifest, signature = encrypted(codec)
    identity = list(codec[3])[1]
    decrypt(
        codec,
        manifest,
        signature,
        recipient_kind=identity[0],
        recipient_id=identity[1],
        recipient_key=codec[3][identity],
    )
    assert (codec[0] / "restored").read_bytes() == b"sensitive-session-cookies"


@pytest.mark.parametrize(
    "failure",
    [
        "signature",
        "writer",
        "recipient",
        "key",
        "context",
        "hash",
        "invalid_model",
        "recipient_set",
    ],
)
def test_bad_authentication_never_publishes(codec, failure):
    manifest, signature = encrypted(codec)
    changes = {}
    if failure == "signature":
        signature = bytes(64)
    elif failure == "writer":
        changes["trusted_writer_key"] = Ed25519PrivateKey.generate().public_key()
    elif failure == "recipient":
        changes["recipient_id"] = str(uuid4())
    elif failure == "key":
        changes["recipient_key"] = X25519PrivateKey.generate()
    elif failure == "context":
        changes["expected"] = codec[2].model_copy(update={"membership_epoch": 2})
    elif failure == "hash":
        manifest = manifest.model_copy(update={"ciphertext_sha256": "00" * 32})
        signature = codec[4].sign(signing_bytes(manifest))
    elif failure == "invalid_model":
        manifest = manifest.model_copy(update={"checkout_generation": 0})
    else:
        changes["expected"] = codec[2].model_copy(update={"recipient_identities": ()})
    with pytest.raises(SnapshotCryptoError, match="^Snapshot operation rejected$"):
        decrypt(codec, manifest, signature, **changes)
    assert not (codec[0] / "restored").exists()
    assert not list(codec[0].glob(".sync-*"))


@pytest.mark.parametrize(
    "damage", ["truncate", "append", "flip", "no_final", "huge_frame", "reorder"]
)
def test_stream_damage_even_with_valid_signature_never_publishes(codec, damage):
    manifest, signature = encrypted(codec, b"p" * (CHUNK_BYTES + 7))
    path = codec[0] / "encrypted"
    data = path.read_bytes()
    if damage == "truncate":
        data = data[:-1]
    elif damage == "append":
        data += b"tail"
    elif damage == "flip":
        data = data[:50] + bytes([data[50] ^ 1]) + data[51:]
    elif damage == "no_final":
        # Remove the entire FINAL frame, leaving an otherwise authentic stream.
        data = data[: 28 + int.from_bytes(data[24:28], "big")]
    elif damage == "huge_frame":
        data = data[:24] + b"\xff" * 4 + data[28:]
    else:
        first_end = 28 + int.from_bytes(data[24:28], "big")
        second_end = first_end + 4 + int.from_bytes(data[first_end : first_end + 4], "big")
        data = data[:24] + data[first_end:second_end] + data[24:first_end] + data[second_end:]
    path.write_bytes(data)
    manifest = manifest.model_copy(
        update={
            "ciphertext_length": len(data),
            "ciphertext_sha256": hashlib.sha256(data).hexdigest(),
        }
    )
    signature = codec[4].sign(signing_bytes(manifest))
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, manifest, signature)
    assert not (codec[0] / "restored").exists()
    assert not list(codec[0].glob(".sync-*"))


def test_existing_output_preserved(codec):
    manifest, signature = encrypted(codec)
    output = codec[0] / "restored"
    output.write_bytes(b"previous-version")
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, manifest, signature)
    assert output.read_bytes() == b"previous-version"


def test_encrypt_rejects_extra_recipient(codec):
    root, template, _, private, signer = codec
    source = root / "source"
    source.write_bytes(b"private")
    keys = {k: v.public_key() for k, v in private.items()}
    keys[("device", str(uuid4()))] = X25519PrivateKey.generate().public_key()
    with pytest.raises(SnapshotCryptoError):
        encrypt_file(source, root / "encrypted", template, keys, signer)
    assert not (root / "encrypted").exists()


def test_publish_race_preserves_existing_output(codec, monkeypatch):
    import camoufox_pm.sync.crypto as crypto

    manifest, signature = encrypted(codec)
    original = crypto._publish

    def race(temp: Path, output: Path):
        output.write_bytes(b"won-race")
        original(temp, output)

    monkeypatch.setattr(crypto, "_publish", race)
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, manifest, signature)
    assert (codec[0] / "restored").read_bytes() == b"won-race"
    assert not list(codec[0].glob(".sync-*"))


@pytest.mark.parametrize("tag", [0, 1, 2])
def test_authenticated_invalid_tag_or_short_nonfinal_frame(codec, tag):
    import nacl.bindings as sodium

    from camoufox_pm.sync.crypto import HPKE_SUITE, recipient_info
    from camoufox_pm.sync.protocol import encryption_context_bytes

    manifest, _ = encrypted(codec)
    recipient = manifest.recipients[0]
    key = HPKE_SUITE.decrypt(
        base64.urlsafe_b64decode(recipient.enc + "=")
        + base64.urlsafe_b64decode(recipient.wrapped_key),
        codec[3][(recipient.kind, recipient.recipient_id)],
        info=recipient_info(manifest, recipient.kind, recipient.recipient_id),
    )
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    header = sodium.crypto_secretstream_xchacha20poly1305_init_push(state, key)
    frame = sodium.crypto_secretstream_xchacha20poly1305_push(
        state, b"private", encryption_context_bytes(manifest), tag
    )
    data = header + len(frame).to_bytes(4, "big") + frame
    (codec[0] / "encrypted").write_bytes(data)
    manifest = manifest.model_copy(
        update={
            "ciphertext_length": len(data),
            "ciphertext_sha256": hashlib.sha256(data).hexdigest(),
        }
    )
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, manifest, codec[4].sign(signing_bytes(manifest)))
    assert not (codec[0] / "restored").exists()


def test_plaintext_limit_and_cleanup(codec, monkeypatch):
    import camoufox_pm.sync.crypto as crypto

    manifest, signature = encrypted(codec)
    monkeypatch.setattr(crypto, "MAX_PLAINTEXT_BYTES", 3)
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, manifest, signature)
    assert not (codec[0] / "restored").exists()
    assert not list(codec[0].glob(".sync-*"))
    with pytest.raises(SnapshotCryptoError):
        encrypt_file(
            codec[0] / "source",
            codec[0] / "another",
            codec[1],
            {k: v.public_key() for k, v in codec[3].items()},
            codec[4],
        )
    assert not (codec[0] / "another").exists()


def test_ciphertext_limit(codec, monkeypatch):
    import camoufox_pm.sync.crypto as crypto

    manifest, signature = encrypted(codec)
    monkeypatch.setattr(crypto, "MAX_CIPHERTEXT_BYTES", 45)
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, manifest, signature)
    assert not (codec[0] / "restored").exists()


def test_symlink_destination_preserves_target(codec):
    manifest, signature = encrypted(codec)
    target = codec[0] / "target"
    target.write_bytes(b"preserve")
    try:
        (codec[0] / "restored").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, manifest, signature)
    assert target.read_bytes() == b"preserve"


def test_reject_nonregular_source_before_open(codec):
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable")
    source = codec[0] / "fifo"
    os.mkfifo(source)
    with pytest.raises(SnapshotCryptoError):
        encrypt_file(
            source,
            codec[0] / "encrypted",
            codec[1],
            {k: v.public_key() for k, v in codec[3].items()},
            codec[4],
        )


def test_unprotected_destination_parent(codec):
    import os

    if os.name == "nt":
        pytest.skip("POSIX permission regression")
    manifest, signature = encrypted(codec)
    codec[0].chmod(0o755)
    try:
        with pytest.raises(SnapshotCryptoError):
            decrypt(codec, manifest, signature)
        assert not (codec[0] / "restored").exists()
    finally:
        codec[0].chmod(0o700)


def test_signed_sibling_revision_cannot_replace_selected_revision(codec):
    root, template, expected, private, signer = codec
    selected, selected_signature = encrypted(codec, b"selected-revision")
    sibling_template = template.model_copy(update={"revision_id": str(uuid4())})
    sibling_source = root / "sibling-source"
    sibling_source.write_bytes(b"unselected-sibling")
    sibling_path = root / "sibling-encrypted"
    sibling, sibling_signature = encrypt_file(
        sibling_source,
        sibling_path,
        sibling_template,
        {k: v.public_key() for k, v in private.items()},
        signer,
    )
    # Both siblings are authentic, have the same writer/parent/membership and
    # generation, and decrypt independently. Only the locally selected revision
    # is acceptable when a relay supplies a snapshot for this operation.
    assert sibling.revision_id != selected.revision_id
    signer.public_key().verify(sibling_signature, signing_bytes(sibling))
    with pytest.raises(SnapshotCryptoError):
        decrypt(codec, sibling, sibling_signature, source=sibling_path)
    assert not (root / "restored").exists()
    assert not list(root.glob(".sync-*"))
    decrypt(
        codec,
        sibling,
        sibling_signature,
        source=sibling_path,
        destination=root / "sibling-restored",
        expected=expected.model_copy(update={"revision_id": sibling.revision_id}),
    )
    assert (root / "sibling-restored").read_bytes() == b"unselected-sibling"
    decrypt(codec, selected, selected_signature)
    assert (root / "restored").read_bytes() == b"selected-revision"


def test_source_swapped_to_fifo_is_nonblocking_and_rejected(codec, monkeypatch):
    import os

    import camoufox_pm.sync.crypto as crypto

    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("POSIX nonblocking FIFO unavailable")
    source = codec[0] / "source"
    source.write_bytes(b"before-swap")
    original_open = os.open
    swapped = False

    def swap_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == source and not swapped:
            # Assert before replacing/opening to keep this regression safe even
            # if a future implementation accidentally removes O_NONBLOCK.
            assert flags & os.O_NONBLOCK
            swapped = True
            source.unlink()
            os.mkfifo(source)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(crypto.os, "open", swap_open)
    with pytest.raises(SnapshotCryptoError):
        encrypt_file(
            source,
            codec[0] / "encrypted",
            codec[1],
            {k: v.public_key() for k, v in codec[3].items()},
            codec[4],
        )
    assert swapped
    assert not (codec[0] / "encrypted").exists()
