# Independent HPKE fixture

`hpke_interop` generates a deterministic known-answer with Cloudflare CIRCL
v1.6.5. Its fixed seeds and public test private key are **only test data**.
The suite is RFC 9180 X25519 / HKDF-SHA256 / ChaCha20-Poly1305, base mode,
with empty AAD and the full protocol recipient context as `info`.

Run `uv run --extra sync python scripts/verify_sync_interop.py` with Go 1.25
available. CI generates the vector using CIRCL and decrypts it using the public
`cryptography` API; substituted `info` must fail. The committed `hpke-circl-v1.json` is also exercised by Python unit tests without
Go. CI regenerates it and requires exact equality. The generated JSON is published
as the `sync-hpke-known-answer` workflow artifact. Native codec packaging is
checked separately on Linux, Windows and both macOS architectures.
