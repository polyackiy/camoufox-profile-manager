"""Verify CIRCL-produced HPKE ciphertext through our public Python HPKE suite."""

import json
import subprocess
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from smoke_sync_crypto import fixture

from camoufox_pm.sync.crypto import HPKE_SUITE, recipient_info

root = Path(__file__).resolve().parents[1]
manifest, _, _ = fixture()
recipient = manifest.recipients[0]
info = recipient_info(manifest, recipient.kind, recipient.recipient_id)
result = subprocess.check_output(
    ["go", "run", "-mod=readonly", ".", info.hex()],
    cwd=root / "tests/vectors/sync/hpke_interop",
)
vector = json.loads(result)
assert vector == json.loads((root / "tests/vectors/sync/hpke-circl-v1.json").read_text())
assert bytes.fromhex(vector["info"]) == info and vector["aad"] == ""
key = X25519PrivateKey.from_private_bytes(bytes.fromhex(vector["recipient_secret"]))
assert HPKE_SUITE.decrypt(bytes.fromhex(vector["wrapped_key"]), key, info=info) == bytes.fromhex(
    vector["plaintext"]
)
try:
    HPKE_SUITE.decrypt(bytes.fromhex(vector["wrapped_key"]), key, info=info + b"wrong")
except (ValueError, InvalidTag):
    pass
else:
    raise AssertionError("HPKE accepted substituted context")
(root / "sync-hpke-vector.json").write_text(json.dumps(vector, indent=2) + "\n")
print("CIRCL -> cryptography HPKE known-answer and context rejection passed")
