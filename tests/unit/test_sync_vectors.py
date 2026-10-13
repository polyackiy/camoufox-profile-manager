"""Known answer from an independent implementation; all keys are public test data."""

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography.hazmat.primitives.hpke")
pytest.importorskip("nacl.bindings")

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from camoufox_pm.sync.crypto import HPKE_SUITE


def test_independent_circl_hpke_known_answer():
    vector = json.loads((Path(__file__).parents[1] / "vectors/sync/hpke-circl-v1.json").read_text())
    key = X25519PrivateKey.from_private_bytes(bytes.fromhex(vector["recipient_secret"]))
    assert key.public_key().public_bytes_raw().hex() == vector["recipient_public"]
    ciphertext = bytes.fromhex(vector["wrapped_key"])
    info = bytes.fromhex(vector["info"])
    assert HPKE_SUITE.decrypt(ciphertext, key, info=info).hex() == vector["plaintext"]
    with pytest.raises((ValueError, InvalidTag)):
        HPKE_SUITE.decrypt(ciphertext, key, info=info + b"wrong")
