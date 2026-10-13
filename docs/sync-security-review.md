# S1 security review and validation record

This review covers the experimental protocol, opaque-file codec and reference
checkout state machine for [#83](https://github.com/polyackiy/camoufox-profile-manager/issues/83).
It does not enable device synchronization. Enrollment, custody of device keys,
closed-profile capture, relay authorization, durable apply and UI remain S2–S8.

## Review method and findings

Review date: 2026-10-13. Two coding agents independently reviewed modules they did
not author; the coordinating agent reviewed the implementation and integration.
This is an internal code review, **not an independent external security audit**.
The production gate in S8 still requires external crypto/security review.

Two high-priority findings were reproduced and addressed with regression tests:

1. **Selected revision substitution.** Parent, writer, epoch and generation do not
   distinguish two signed sibling candidates. `SnapshotContext` must include the
   exact selected revision from trusted state. The codec rejects an abandoned
   sibling before publishing plaintext, even when its signature is valid.
2. **Observed authority rollback.** Invalidating a phase while forgetting a newly
   observed epoch/generation permits subsequent reconciliation with older
   authority. Observed authority high-water evidence must survive invalidation;
   an older checkpoint cannot restore offline work permission.

Source opening also rejects special files before opening and uses nonblocking,
no-follow flags where available, followed by regular-file and inode checks. This
avoids hanging on FIFOs, including a regular-file-to-FIFO swap. Only full-sized
nonfinal frames are accepted, bounding per-frame work for adversarial inputs.

## Threat-to-test mapping

| Threat | Executable evidence |
| --- | --- |
| Ambiguous JSON, duplicate fields, alternate encodings, unknown versions, excess nesting/size | `test_sync_protocol.py` strict parser and canonical byte vectors |
| Cross-owner/profile/revision/writer/parent/epoch/generation or recipient substitution | Protocol context tests and actual sibling-ciphertext regression in `test_sync_crypto.py` |
| Wrong signing/recipient keys, modified or truncated ciphertext, missing final tag, reordered/trailing frames | Real HPKE/Ed25519/secretstream negative tests in `test_sync_crypto.py` |
| Plaintext exposure through partial activation or output overwrite | Private staging, failed-authentication cleanup, symlink/permission and destination-race tests |
| Blocking special files and excessive framing work | FIFO/source and bounded-frame tests |
| Stale ownership, uncertain handoff, rollback, revocation, competing heads | `test_sync_state.py` immutable reference transitions and high-water regressions |
| Same implementation reproducing its own crypto mistake | CIRCL-generated HPKE known-answer decrypted by the public Python HPKE API, including wrong-context rejection |
| Missing native crypto libraries in a packaged app | `sync-protocol.yml` freezes and runs the actual codec outside the checkout on Linux, Windows, macOS ARM and Intel |

Full browser integration and power-loss behavior are not established by these
unit tests. A frozen codec smoke is not a signed installer acceptance test.

## Library and container contract

The optional `sync` extra uses `cryptography>=50.0.2,<51` and
`PyNaCl>=1.6.2,<1.7`; exact versions and distribution hashes are in `uv.lock`.
Use the public [cryptography HPKE API](https://cryptography.io/en/stable/hazmat/primitives/hpke/)
for X25519 / HKDF-SHA256 / ChaCha20-Poly1305, Ed25519 for manifest signatures,
and [PyNaCl secretstream bindings](https://github.com/pyca/pynacl/blob/main/src/nacl/bindings/crypto_secretstream.py)
for libsodium XChaCha20-Poly1305 streams. There are no custom crypto primitives.
HPKE base mode does not authenticate the sender: trusted writer signature and
membership checks are mandatory. Context binding uses HPKE `info` (the public
one-shot API does not expose AAD).

The opaque ciphertext is a 24-byte secretstream header, followed by frames:
`uint32_big_endian(ciphertext_length) || authenticated_message`. Each message has
17 bytes of overhead. Nonfinal messages use `TAG_MESSAGE` and exactly 1 MiB of
plaintext; the encoder ends with a partial (possibly empty) `TAG_FINAL` message.
The decoder accepts a final message of at most 1 MiB. PUSH/REKEY tags, absent final
tag and bytes after final are rejected. Each message uses the complete protocol
encryption context as AAD. Plaintext is limited to 2 GiB independently of the
signed ciphertext limit. The empty encoded file is 45 bytes.

Per-recipient HPKE info is `encryption_context_bytes || 0x00 || "recipient" ||
0x00 || compact_JSON([kind, recipient_id])`. The 80-byte HPKE result splits into
32-byte `enc` and 48-byte `wrapped_key`. The signature covers the completed
manifest including every envelope, digest and length. All external manifests
must enter via `parse_manifest`; crypto entry points also revalidate models.

Decryption writes only into a private staging file and exposes the destination
name after full signature, context, stream, digest and length verification. A
hard link provides atomic no-overwrite publication; unsupported filesystems fail
closed. A directory-fsync error after publication can leave a complete output:
callers must reconcile it rather than overwrite or assume no output exists.

## Remaining decisions and limits

- `sync` is optional and has no application routes, automatic transfer, profile
  mutation or production toggle. Opaque bytes are not yet a safe profile archive.
- S2 must establish trusted owner/device keys, OS key storage, independent pairing
  checkpoints, recovery and authenticated membership. A manifest cannot authenticate
  its own signer or prove freshness. Python does not guarantee key-memory erasure.
- S3 must define and enforce encrypted archive metadata/schema, safe extraction,
  exact browser compatibility and quiescence. No cross-platform profile support
  is claimed by native codec tests.
- S4 must specify authenticated relay transactions, idempotency and quota limits;
  S5/S6 must durably persist the reference transitions, control-store high-water
  marks and journals outside application-data rollback, and enforce all entry
  points. Reference dataclasses alone provide no durable authority.
- The trusted local process/private-parent assumption excludes an attacker with
  the same OS account. Staging deletion does not promise physical secure erase.
- A malicious relay can withhold or fork valid histories. New-device recovery
  without an independently trusted checkpoint remains blocked. Revocation cannot
  recall downloaded data or stop an unreachable browser. No timeout steals a
  checkout. See [the complete threat model](stage-four-sync.md).

## CI evidence

[Native codec and independent interoperability run](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38447336253)
passed on Linux, Windows, macOS ARM and Intel. This includes source and frozen
execution, committed CIRCL-vector regeneration and wrong-context rejection.
[Update/recovery checks](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38447336218)
also passed on all four targets with the updated dependency lock. The
[owner bootstrap contract](sync-trust-contract.md) is a reviewed target for S2/S3,
not an implemented key-management guarantee.
