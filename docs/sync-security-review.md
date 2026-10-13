# Synchronization security review and validation record

This review covers the experimental protocol, opaque-file codec and reference
checkout state machine for [#83](https://github.com/polyackiy/camoufox-profile-manager/issues/83).
The original S1 record below is retained. The integrated S2–S8 implementation and
its additional review findings are recorded at the end of this document.

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

## Dependency advisory check

On 2026-10-13, review also checked the published
[cryptography advisories](https://github.com/pyca/cryptography/security/advisories)
and [PyNaCl changelog](https://github.com/pyca/pynacl/blob/main/CHANGELOG.rst).
The selected PyNaCl 1.6.2 includes the upstream libsodium fix for CVE-2025-69277.
The cryptography floor for the optional sync extra is 50.0.2. This targeted check
is not a complete dependency/SBOM audit or a promise that no vulnerabilities exist.

Source-built cryptography requires a separately patched OpenSSL installation,
as [upstream explicitly documents](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf).
The Intel native run above bundled the matching OpenSSL 3.6.3 library pair.
A successful roundtrip establishes packaging compatibility, not security of
arbitrary system libraries on downstream source-build hosts.

## S2/S3 implementation review, 2026-10-13

The next implementation adds cookie-only administrator enrollment, immutable local
account binding, protected native/encrypted vault adapters, journalled bootstrap,
single-use pairing transactions, isolated-process recovery verification, strict
portable snapshots and native Firefox profile locking. The relay, durable transfer
engine and user-facing synchronization flow are separate integration work; these
modules alone do not constitute completed synchronization.

Independent agent review of modules authored by other workers found and fixed a
ZIP64 preflight bypass: Python's ZIP reader could use ZIP64 directory values instead
of the previously checked ordinary end-of-directory fields. The snapshot format
now rejects ZIP64 before constructing `ZipFile`; the regression asserts that the
parser is never invoked. Fixed-size KDF parameters, key separation, stable-user
checks, invitation CAS and sanitized errors have negative tests.

No additional confirmed high-severity findings were reported in these reviewed
modules. This is an internal implementation review, not an external professional
cryptographic audit. Native credential-store and frozen windowed recovery checks
have been added to CI; their results must be recorded before claiming platform
acceptance. A control database cannot independently detect restoration of an entire
old disk image. Freshness reconciliation, browser entry-point enforcement and
journalled profile replacement remain mandatory integration gates.


## Integrated implementation review (2026-10-13)

Separate coding agents reviewed modules they did not author; findings were checked
against executable regressions. This remains internal review, not an external
cryptographic audit. The implementation is a preview; a stable-production security
claim still requires the external gate above.

| Finding | Resolution and regression evidence |
| --- | --- |
| ZIP64 directory preflight could allocate before limits | Reject ZIP64 before constructing ZipFile; snapshot parser tests |
| Successful drip-feed reads bypassed total transfer timeout | Monotonic end-to-end RPC/transfer deadlines; client tests |
| Aborted uploads released quota while partial/open files remained | Deterministic pending paths; live PID/birth identity reservation; relay tests |
| Unauthenticated RPC bodies consumed unbounded concurrent work | Signature preauthentication, bounded RPC slots and total body deadline |
| Newly observed signed history could be forgotten after a later failure | Persist every valid receipt/membership observation; older prefixes cannot restore launch permission |
| New device could claim an old snapshot without its envelope | Authenticate selected publication and recipient availability before claim |
| Unsupported browser/platform could claim before validation | Authenticate and validate compatibility before conditional claim; exact preflight checkpoint |
| Administrator revoked during long apply could still commit | Recheck live authorization immediately before publication and final activation |
| Ordinary unbound edit raced initial enrollment | Shared per-profile admission plus browser-operation leases; recheck binding under admission |
| Completed/failing background work starved later profiles | Filter completed queue records and durably rotate bounded attempts |
| Reconnecting an existing owner failed after membership advancement | Existing pinned owner conflict precedes genesis/capacity rules; exact membership reconciliation |
| Membership mutation followed a delayed read without fresh authorization | Repeat authorization immediately before outbound membership mutation |
| Recovery/copy failure could remove a pre-existing UUID-collision directory | Track exclusive staging ownership; collision sentinel regressions |
| A new profile invitation could lose its exact signed membership anchor | Atomically retain the exact membership and reject signed sibling histories |
| A relay could project newly received old-epoch receipts past an already trusted revocation | Require a current trusted epoch signature for an advanced head; release/commit rejection survives retries and new engine instances |
| Lost-device recovery was available only in the engine | Explicit owner-only API/UI acknowledgement; revoked holder, exact checkpoint preflight, journalled activation and lost-ack retry tests |

Synchronized profile markers exist in both the browser directory and a separate
private registry. Application rollback invalidates installed-generation proof
before replacing data and retains protected history. Schema 3 makes schema-aware
pre-sync managers refuse the current database. These fences cannot control raw
Firefox launches, arbitrary filesystem copies or a deliberately restored complete
old environment using software that predates the schema boundary.

The supported initial transport captures complete closed profiles, with exact
same-OS/architecture/browser compatibility. Unknown capabilities and external
extension-library mappings fail closed. Team membership, cross-OS transfer,
concurrent profile editing, delta synchronization and automatic authority transfer
are not implied by this implementation.

The native key-store/frozen crypto matrix and actual browser roundtrip matrix are
separate: simulated key stores in browser tests do not prove native key-store
behavior, and native crypto smokes do not prove browser state portability.

## Upload expiry follow-up

Uncommitted uploads have a durable expiry deadline and a bounded local maintenance
command. A persisted cleanup fence precedes file removal; quota is retained until
removal and directory fsync succeed. Committed history is retained. Recovery can
finish a previously fenced cleanup after a crash without using a backwards clock
to make new expiry decisions.

An explicit client retry accepts an expiry report only after reconciling signed
history before and after that report, checking the exact pending intent and the
unchanged locally held checkout. It archives the old intent and keeps its
ciphertext, then creates a fresh revision/key/stream. A missing response, changed
head, late signed commit, lost authorization or malformed expiry report cannot
silently reset ownership or replace the pending operation. Unit tests cover these
races; separate-agent review checks the new cleanup and retry boundaries.

## Completion review: resume, lifecycle and retention

Internal review on 2026-10-14 covered the new byte-offset upload protocol,
authenticated encrypted Trash/restore, owner-signed historical ciphertext cleanup,
and their manager authorization/UI paths. A separate coding agent reviewed code
it did not author; this remains an internal review rather than external audit.

Fixed findings and regression coverage:

- **Interrupted lifecycle publication:** persist exact manifest, signature and
  retained ciphertext intent before sending; initial Trash handoff and lifecycle
  intent share a control-store transaction. Crashes before the engine intent or
  after a committed publication resume the original revision.
- **Repeated restoration activation:** a retry may already have recorded the
  installed revision. Exact revision/generation plus retained local row/path
  checks allow completion without repeating activation or weakening the fence.
- **Retention clock skew:** the signed not-before date alone must not reject
  ordinary network delay or shorten grace. The relay persists a separate deadline
  at least seven days after first receipt; exact signed decisions are retryable.
- **Cancellation acknowledgement:** an original valid schedule is not evidence
  of successful cancellation. Require the exact root-signed cancellation in the
  reply; retain uncertain cancellation intent and display it as pending.
- **Cross-device restoration UI:** a locally trashed copy must retain an explicit
  authenticated way to retrieve an active successor restored elsewhere. The
  restoration check leaves launch blocked until verified installation; UI tests
  cover an active successor, an unchanged tombstone and pending-operation fences.
- **Crashed download leases:** reap confirmed-dead PID/birth identities independently
  of old-version collection, including current heads. Live or uncertain readers
  remain protected and cannot be evicted by a heartbeat timeout.

The reviewer reported no remaining actionable P1/P2 findings in the final source
review. Targeted real-relay workflow tests cover owner-only authorization, explicit
cleanup consent, current-head exclusion, idempotent schedule/cancel and dishonest
cancellation acknowledgements. Relay tests cover chunk overlap, retained offsets,
crash tails, legacy live-writer fencing, hash completion, lost acknowledgements,
collection fences, signed evidence and reader protection. Lifecycle tests cover
real two-device Trash/restore, old-client format rejection, local bypass attempts
and interruptions before/after each durable publication boundary.

A new native installed-manager acceptance workflow builds ordinary installers and
runs two isolated installed manager processes with their bundled UI and a real
HTTPS relay. Its ephemeral test CA is added only to disposable installed copies;
release artifacts and TLS validation are not weakened.
[Run 38471961607](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38471961607)
passed on Linux, Windows and macOS ARM/Intel at `5bd37f6`, including four real
browser launches, UI Trash/restore and retention schedule/cancel. See the
[support matrix](sync-support-matrix.md) for exact coverage and limits. Same-runner
testing does not prove every physical GPU/font environment.
