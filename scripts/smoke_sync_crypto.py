"""Synthetic crypto roundtrip, also executed from frozen binaries in native CI."""

from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from camoufox_pm.core.update_permissions import create_private_directory
from camoufox_pm.sync.crypto import decrypt_file, encrypt_file
from camoufox_pm.sync.protocol import SUITE, RecipientEnvelope, SnapshotContext, SnapshotManifest

assert "camoufox" not in sys.modules, "Crypto helpers initialized the browser stack"


def fixture():
    writer = "11111111-1111-4111-8111-111111111111"
    recovery = "22222222-2222-4222-8222-222222222222"
    identities = (("device", writer), ("recovery", recovery))
    private = {identity: X25519PrivateKey.generate() for identity in identities}
    manifest = SnapshotManifest(
        version=1,
        suite=SUITE,
        owner_id="33333333-3333-4333-8333-333333333333",
        profile_id="44444444-4444-4444-8444-444444444444",
        revision_id="55555555-5555-4555-8555-555555555555",
        parent_revision_id=None,
        writer_device_id=writer,
        membership_epoch=1,
        checkout_generation=1,
        ciphertext_sha256="0" * 64,
        ciphertext_length=45,
        recipients=tuple(
            RecipientEnvelope(
                kind=kind,
                recipient_id=identity,
                enc=base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode(),
                wrapped_key=base64.urlsafe_b64encode(bytes(48)).rstrip(b"=").decode(),
            )
            for kind, identity in identities
        ),
    )
    context = SnapshotContext(
        owner_id=manifest.owner_id,
        profile_id=manifest.profile_id,
        revision_id=manifest.revision_id,
        parent_revision_id=None,
        writer_device_id=writer,
        membership_epoch=1,
        checkout_generation=1,
        recipient_identities=identities,
    )
    return manifest, context, private


def main():
    manifest, context, private = fixture()
    signer = Ed25519PrivateKey.generate()
    with tempfile.TemporaryDirectory(prefix="sync-native-") as directory:
        root = Path(directory).resolve() / "private"
        create_private_directory(root)
        source = root / "source"
        payload = b"synthetic sync fixture\x00" * 100000
        source.write_bytes(payload)
        sealed, signature = encrypt_file(
            source,
            root / "cipher",
            manifest,
            {identity: key.public_key() for identity, key in private.items()},
            signer,
        )
        for index, ((kind, identity), key) in enumerate(private.items()):
            target = root / f"clear-{index}"
            decrypt_file(
                root / "cipher",
                target,
                sealed,
                signature,
                context,
                kind,
                identity,
                key,
                signer.public_key(),
            )
            assert target.read_bytes() == payload
        damaged = bytearray((root / "cipher").read_bytes())
        damaged[-1] ^= 1
        (root / "damaged").write_bytes(damaged)
        kind, identity = next(iter(private))
        try:
            decrypt_file(
                root / "damaged",
                root / "must-not-exist",
                sealed,
                signature,
                context,
                kind,
                identity,
                private[(kind, identity)],
                signer.public_key(),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Tampered ciphertext was accepted")
        assert not (root / "must-not-exist").exists()
    print("Native sync crypto: device/recovery roundtrip and tamper rejection passed")


if __name__ == "__main__":
    main()
