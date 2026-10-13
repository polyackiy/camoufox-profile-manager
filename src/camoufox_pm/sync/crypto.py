"""Authenticated file codec used by the opt-in synchronization runtime.

Callers supply independently authenticated context and writer/recipient keys. No
membership, freshness, key storage or archive safety is inferred from a signature.
Requires the optional ``sync`` dependencies; uses only public crypto APIs.
Destination parents must already be private and remain controlled by the caller.
If directory fsync fails after atomic publication, a complete verified output may
already exist: callers must reconcile it rather than blindly retry/overwrite.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import nacl.bindings as sodium
from cryptography.hazmat.primitives import hpke
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

from camoufox_pm.core.update_permissions import (
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)
from camoufox_pm.sync.protocol import (
    MAX_CIPHERTEXT_BYTES,
    SnapshotContext,
    SnapshotManifest,
    encryption_context_bytes,
    signing_bytes,
    validate_context,
)

CHUNK_BYTES = 1024 * 1024
MAX_PLAINTEXT_BYTES = 2 * 1024**3
HPKE_SUITE = hpke.Suite(hpke.KEM.X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.CHACHA20_POLY1305)
_MESSAGE = sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
_FINAL = sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL
_OVERHEAD = sodium.crypto_secretstream_xchacha20poly1305_ABYTES
_HEADER = sodium.crypto_secretstream_xchacha20poly1305_HEADERBYTES


class SnapshotCryptoError(ValueError):
    """Fixed-message rejection; never includes keys, paths or source contents."""


def _validated(manifest: SnapshotManifest) -> SnapshotManifest:
    return SnapshotManifest.model_validate(manifest.model_dump(mode="python"))


def recipient_info(manifest: SnapshotManifest, kind: str, recipient_id: str) -> bytes:
    """HPKE info binds the full stream context and the individual recipient."""
    manifest = _validated(manifest)
    if (kind, recipient_id) not in {(r.kind, r.recipient_id) for r in manifest.recipients}:
        raise SnapshotCryptoError("Snapshot operation rejected")
    identity = json.dumps([kind, recipient_id], separators=(",", ":")).encode("ascii")
    return encryption_context_bytes(manifest) + b"\x00recipient\x00" + identity


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


@contextmanager
def _source(path: Path, limit: int) -> Iterator[BinaryIO]:
    reject_reparse_paths(path)
    original = path.lstat()
    if not stat.S_ISREG(original.st_mode):
        raise SnapshotCryptoError("Snapshot operation rejected")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > limit
            or (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino)
        ):
            raise SnapshotCryptoError("Snapshot operation rejected")
        yield stream


@contextmanager
def _staging(destination: Path) -> Iterator[tuple[Path, BinaryIO]]:
    verify_private_path(destination.parent)
    reject_reparse_paths(destination)
    if destination.exists():
        raise SnapshotCryptoError("Snapshot operation rejected")
    temporary = destination.parent / (".sync-" + uuid4().hex + ".tmp")
    try:
        descriptor = create_private_file(temporary)
        with os.fdopen(descriptor, "wb") as stream:
            yield temporary, stream
    finally:
        temporary.unlink(missing_ok=True)


def _publish(temporary: Path, destination: Path) -> None:
    # Hard-link creation is atomic and fails if the destination exists. Never
    # fall back to replacing an existing file, including on Windows/filesystems
    # without hard links. Both names are within the verified private parent.
    verify_private_path(destination.parent)
    verify_private_path(temporary)
    reject_reparse_paths(destination)
    os.link(temporary, destination, follow_symlinks=False)
    if os.name != "nt":
        descriptor = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def encrypt_file(
    source: Path,
    destination: Path,
    template: SnapshotManifest,
    recipient_keys: Mapping[tuple[str, str], X25519PublicKey],
    writer_key: Ed25519PrivateKey,
) -> tuple[SnapshotManifest, bytes]:
    """Encrypt opaque bytes into a new private file and return signed metadata.

    Template envelopes/digest/length are valid placeholders, replaced atomically
    in the returned manifest. Caller must ensure the source is quiescent.
    """
    try:
        template = _validated(template)
        identities = {(r.kind, r.recipient_id) for r in template.recipients}
        if set(recipient_keys) != identities:
            raise ValueError("recipient set")
        key = sodium.crypto_secretstream_xchacha20poly1305_keygen()
        envelopes = []
        for recipient in template.recipients:
            wrapped = HPKE_SUITE.encrypt(
                key,
                recipient_keys[(recipient.kind, recipient.recipient_id)],
                info=recipient_info(template, recipient.kind, recipient.recipient_id),
            )
            if len(wrapped) != 80:
                raise ValueError("envelope length")
            envelopes.append(
                {
                    **recipient.model_dump(),
                    "enc": _b64(wrapped[:32]),
                    "wrapped_key": _b64(wrapped[32:]),
                }
            )
        state = sodium.crypto_secretstream_xchacha20poly1305_state()
        header = sodium.crypto_secretstream_xchacha20poly1305_init_push(state, key)
        aad = encryption_context_bytes(template)
        digest = hashlib.sha256()
        length = 0
        plaintext_length = 0
        with _source(source, MAX_PLAINTEXT_BYTES) as incoming, _staging(destination) as (temp, out):

            def write(data: bytes) -> None:
                nonlocal length
                length += len(data)
                if length > MAX_CIPHERTEXT_BYTES:
                    raise ValueError("ciphertext limit")
                digest.update(data)
                out.write(data)

            write(header)
            while True:
                chunk = incoming.read(CHUNK_BYTES)
                plaintext_length += len(chunk)
                if plaintext_length > MAX_PLAINTEXT_BYTES:
                    raise ValueError("plaintext limit")
                final = len(chunk) < CHUNK_BYTES
                encrypted = sodium.crypto_secretstream_xchacha20poly1305_push(
                    state, chunk, aad, _FINAL if final else _MESSAGE
                )
                write(len(encrypted).to_bytes(4, "big") + encrypted)
                if final:
                    break
            manifest = SnapshotManifest.model_validate(
                {
                    **template.model_dump(),
                    "recipients": envelopes,
                    "ciphertext_sha256": digest.hexdigest(),
                    "ciphertext_length": length,
                }
            )
            signature = writer_key.sign(signing_bytes(manifest))
            out.flush()
            os.fsync(out.fileno())
            out.close()
            _publish(temp, destination)
            return manifest, signature
    except Exception:
        raise SnapshotCryptoError("Snapshot operation rejected") from None


def decrypt_file(
    source: Path,
    destination: Path,
    manifest: SnapshotManifest,
    signature: bytes,
    expected: SnapshotContext,
    recipient_kind: str,
    recipient_id: str,
    recipient_key: X25519PrivateKey,
    trusted_writer_key: Ed25519PublicKey,
) -> None:
    """Verify trusted context, signature and complete stream before publication."""
    try:
        manifest = _validated(manifest)
        expected = SnapshotContext.model_validate(expected.model_dump(mode="python"))
        validate_context(manifest, expected)
        trusted_writer_key.verify(signature, signing_bytes(manifest))
        recipient = next(
            r
            for r in manifest.recipients
            if (r.kind, r.recipient_id) == (recipient_kind, recipient_id)
        )
        key = HPKE_SUITE.decrypt(
            _unb64(recipient.enc) + _unb64(recipient.wrapped_key),
            recipient_key,
            info=recipient_info(manifest, recipient_kind, recipient_id),
        )
        digest = hashlib.sha256()
        length = 0
        plaintext_length = 0
        aad = encryption_context_bytes(manifest)
        with (
            _source(source, MAX_CIPHERTEXT_BYTES) as incoming,
            _staging(destination) as (temp, out),
        ):

            def read(size: int) -> bytes:
                nonlocal length
                data = incoming.read(size)
                length += len(data)
                if length > manifest.ciphertext_length or len(data) != size:
                    raise ValueError("ciphertext length")
                digest.update(data)
                return data

            state = sodium.crypto_secretstream_xchacha20poly1305_state()
            sodium.crypto_secretstream_xchacha20poly1305_init_pull(state, read(_HEADER), key)
            while True:
                size = int.from_bytes(read(4), "big")
                if not _OVERHEAD <= size <= CHUNK_BYTES + _OVERHEAD:
                    raise ValueError("frame length")
                plaintext, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(
                    state, read(size), aad
                )
                plaintext_length += len(plaintext)
                if plaintext_length > MAX_PLAINTEXT_BYTES or tag not in (_MESSAGE, _FINAL):
                    raise ValueError("stream limits")
                if tag == _MESSAGE and len(plaintext) != CHUNK_BYTES:
                    raise ValueError("nonfinal frame length")
                out.write(plaintext)
                if tag == _FINAL:
                    break
            if (
                incoming.read(1)
                or length != manifest.ciphertext_length
                or digest.hexdigest() != manifest.ciphertext_sha256
            ):
                raise ValueError("ciphertext mismatch")
            out.flush()
            os.fsync(out.fileno())
            out.close()
            _publish(temp, destination)
    except Exception:
        raise SnapshotCryptoError("Snapshot operation rejected") from None
