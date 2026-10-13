# Stage four: encrypted synchronization between devices

Tracking: [stage-four epic #82](https://github.com/polyackiy/camoufox-profile-manager/issues/82).

Status: experimental synchronization implementation, 2026-10-13. Owner enrollment
and synchronization of each profile require explicit opt-in. Production enablement
remains gated on S8 evidence and external review. Stage three's local authorization
contract remains in [stage-three.md](stage-three.md).

## Product decision

Start with one owner moving selected **closed browser profiles** between their
own trusted computers. Offer an open-source, self-hostable relay; no mandatory
vendor account or proprietary storage. Synchronization is opt-in per profile and
is an export of its browser sessions and secrets to every enrolled device. Require
an authenticated administrator plus explicit local owner enrollment; anonymous
loopback mode and the legacy administrator API key alone cannot enroll devices,
recover keys or enable synchronization. Operators/viewers cannot enable it through
an export grant. Do not infer that a local username identifies a remote owner.

The first release transfers complete, immutable snapshots. It does not merge live
Firefox databases, synchronize the manager's SQLite file, provide concurrent
browser use, or replicate users, grants, passwords, API keys, schedules, activity
history, the installation, or updater checkpoints. Team sharing needs a separate
key/access design and remains outside this MVP. Local-only profiles keep working
without the relay or an account.

## Trust boundaries and promises

| Component/adversary | Boundary |
| --- | --- |
| Trusted owner devices | See plaintext, hold device secrets, and can export the owner's sessions. A compromised OS/browser defeats confidentiality on that device. |
| Relay and object storage | Store ciphertext and routing metadata. Must not receive plaintext names, cookies, passwords, encryption keys or recovery secrets. Availability and correct coordination depend on the relay. |
| Network attacker | TLS with normal certificate validation plus authenticated payloads; no insecure remote HTTP fallback. Local development HTTP must be explicit and loopback-only. |
| Malicious relay | Can deny, delay, delete, replay and fork views. Cannot forge authorized device signatures or decrypt correctly encrypted snapshots. A previously observed older head/epoch is rejected. |
| Revoked/lost device | Cannot receive new keys from compliant peers after they observe revocation. Already downloaded keys, cookies and profile copies cannot be recalled. |
| Local manager users | Existing API roles continue to apply locally. Remote snapshot content never imports an administrator account, grant or schedule. Host administrators remain outside this boundary. |

Encryption does not hide object sizes, timing, device/network addresses, transfer
volume or opaque profile relationships. No anonymous-metadata or traffic-analysis
resistance is promised. Relay logs must redact authorization and invitation
secrets. TLS is still necessary for authentication and routing metadata.

A local high-water mark detects rollback only relative to state that device has
already trusted. A new device or a device restored from an old backup cannot prove
that the relay supplied the newest history without an independent recent trusted
checkpoint. Pairing must transfer that checkpoint directly from an existing
trusted device. Recovery without one enters an explicit unverified-freshness state;
no automatic launch, upload or destructive reconciliation. A malicious relay can
keep isolated clients on different valid histories indefinitely. Detect a fork
when devices compare checkpoints; stop synchronization and preserve both branches.
Do not advertise globally consistent history against a Byzantine relay.

## Keys, enrollment and recovery

Use a reviewed library-backed envelope format; do not implement cryptographic
primitives. Before production, freeze a versioned protocol and library selection
with test vectors and independent security review. Proposed baseline:

- Separate owner authority signing key, per-device signing/encryption keys, and
  random per-snapshot data keys. Device certificates bind owner ID, device ID,
  public keys and privileges; signed membership records form a monotonic epoch
  chain. Only the owner authority approves enrollment/revocation. Ordinary device
  upload keys cannot enroll another device.
- Encrypt each archive with libsodium's authenticated `secretstream` API; require
  its final tag, authenticate the manifest binding, and reject missing/reordered
  data. It provides stream integrity, not protocol freshness. See the
  [upstream secretstream contract](https://doc.libsodium.org/secret-key_cryptography/secretstream).
- Wrap each random snapshot key for the currently authorized device encryption
  keys and a separate owner recovery encryption key, using a maintained RFC 9180
  implementation with a fixed approved suite. Sign a canonical, domain-separated
  manifest including all recipient envelopes and ciphertext digest. HPKE itself
  does not supply application replay/downgrade prevention; those checks are
  mandatory in our manifest/history protocol. See
  [RFC 9180, section 9.7](https://www.rfc-editor.org/rfc/rfc9180.html#section-9.7).
- Store device secrets in the OS credential store where supported. Headless
  installations use an explicit encrypted, permission-restricted key file and
  unlock flow; never silently fall back to plaintext. Account passwords, local
  `secret.key`, relay tokens and device encryption keys are distinct.

Enrollment requires confirmation on an already trusted owner device, verification
of a QR/full public-key fingerprint through a separate trusted channel, and a
short-lived, signed pairing request and single-use approval transferred through
an independently trusted channel. Do not trust a key merely because the
relay delivered it. Bind approval to the exact new device public keys, relay
identity and owner identity; expire and consume the invitation atomically. The
new device receives owner public trust, the current membership/head checkpoint and
only the explicitly selected historical key envelopes. Other devices do not
receive owner authority private material merely by enrolling.

Create and verify an offline recovery package before enabling sync. It contains
the authority recovery material and recovery decryption key, protected by a
high-entropy recovery secret kept outside the relay. Optional passphrase protection
must use a reviewed, bounded Argon2id implementation with measured desktop
parameters; [RFC 9106](https://www.rfc-editor.org/rfc/rfc9106.html) is the reference,
not a reason to invent a password cipher. Explain that losing every device key
and recovery secret loses access; a relay password reset cannot decrypt data.
Test restoration on a fresh installation, not just export of the recovery package.

Revocation increments the signed membership epoch and revokes relay credentials.
Every compliant writer refreshes membership online before publishing and creates
fresh data keys/envelopes that omit the revoked device. Reject publications under
old epochs. A malicious relay can hide a newer membership epoch from an isolated writer;
revocation protects future keys only after that writer observes the signed change.
Do not promise immediate cryptographic revocation across a forked history.
Offline work stays local until reconciliation. Do not rewrap only an
old shared key and call that revocation. Existing ciphertext remains readable to
anyone who retained its key; recovery-root compromise requires a new owner trust
root and explicit re-enrollment. Revoking a browser device may also require the
owner to terminate website sessions and rotate exposed proxy/site credentials.

## Snapshot and relay protocol

A global opaque sync profile ID maps to a distinct local profile ID on each device.
Never use local filesystem paths or a display name as remote object identity.
Keep existing plaintext manual export format v1 unchanged. Add a separate sync
container with a signed outer manifest and encrypted inner metadata/archive.
The outer manifest binds protocol version, owner/profile IDs, parent revision,
revision ID, membership epoch, writer device, checkout generation, ciphertext hash
and byte length, and recipient envelopes. Names, proxy details, fingerprint,
file inventory, manager/schema/browser compatibility and origin platform belong
inside authenticated encryption. Define canonical serialization, domain tags,
size limits and unknown-version rejection before implementing signatures.

Relay operations require scoped device credentials and proof of the enrolled
signing key, have tenant/object authorization on every lookup, and are bounded by
request size, rate and storage quotas. An object-store URL is not authorization to
commit a revision. Use a transactional metadata database for checkout, head CAS
(compare-and-swap), membership epoch and idempotency records; optional S3-compatible
storage holds immutable ciphertext. Ship a simple local-disk backend first. The
relay is a separate minimal service, not a remotely exposed manager API.

Upload into a temporary immutable object, resume only identical ciphertext bytes,
verify its hash/length, then atomically publish the manifest/head with the expected
parent, epoch and checkout generation. Retrying the same operation ID is
idempotent. A failed CAS retains a conflict candidate; no last-write-wins overwrite.
Generate a new encryption stream/key when recreating an archive: never resume by
re-encrypting changed plaintext into an existing ciphertext stream. Download may
resume ciphertext at byte ranges, but must authenticate the complete stream from
its start before accepting the result. Do not expose partial decrypted archives.

Keep explicit bounded retention, tombstones and delayed garbage collection.
Unpublished uploads expire separately. Deletion propagates as an authenticated
revision; it moves the local profile to recoverable Trash only after reconciliation
and confirmation where local changes exist. No automatic remote permanent purge
or local backup deletion. Relay backup/restore preserves the entire metadata/object
set; restoring an old relay backup must trigger client rollback checks.

## One active device, offline work and conflicts

The MVP uses **durable checkout and explicit handoff**, not automatic timeout
stealing. An online device checks out the latest accepted head with a monotonically
increasing generation. It can then work locally offline; every other compliant
device refuses to launch that synchronized profile until handoff. Checkouts do not
expire merely because heartbeats stop. Sleeping laptops are not proof of release.

Handoff first blocks new launches locally, closes the browser, proves local
quiescence, commits a snapshot, then atomically releases checkout for that exact
head/generation. The sender persists its released state before it permits any
subsequent launch; a crash or uncertain reply requires online reconciliation.
The recipient claims checkout, obtains and validates the latest snapshot, and
only then permits launch. Online reconciliation is required to start a profile
that has no durable locally held checkout. A crash while holding checkout retains
ownership and recoverable local state.

Relay generations fence **publication**, not a running Firefox process on an
unreachable computer. The existing SQLite leases coordinate processes sharing one
local database and are not a distributed lock between separately synchronized
managers. The current browser heartbeat can log database failures and keep the
browser running; it is not proof of remote fencing or fail-closed ownership.
Local leases, browser process checks and sync checkout must all pass
before launch, profile mutation, import, restore, scheduled execution or snapshot
activation. A device with uncertain local ownership cannot launch through a CLI,
API, scheduler or stale manager process. Test all entry points.

Lost-device takeover is an explicit owner recovery operation, never a timer. It
revokes the old device, increments generation/epoch, records an audit event, and
warns that the previous browser may still be running. Preserve already received candidates and local unsent data as quarantined
conflicts; never promote them automatically. Revoked credentials cannot upload
new objects or publish revisions. No software protocol here
can stop an offline browser or prevent an OS administrator launching a copied
profile. Thus exclusive browser use is a cooperative guarantee under a correct
relay and compliant clients, not an absolute security guarantee.

Conflicts preserve both complete versions and offer owner-controlled selection or
an explicitly separate local copy. Do not merge cookies, SQLite/WAL files,
fingerprint state or proxy credentials. Launch stays blocked until reconciliation;
creating a second usable identity copy requires an explicit duplicate warning.
No client wall-clock timestamp selects a winner.

## Durable local apply and existing integration points

`core/profile_operations.py` and existing profile leases provide the local
exclusive operation boundary; extend it with sync checkout and an operation
journal. Reuse the *strict* archive path in `core/backups.py`, not a best-effort
export that skips unreadable files. The current general import allocates a fresh
local profile ID and clears local associations; sync needs its own explicit
identity mapping and validated metadata reconciliation rather than blindly calling
that import for every revision. Browser process/lock validation must prove a
closed profile; a missing lock marker alone is insufficient. Never copy a live
SQLite database directory through Dropbox/NFS/cloud-drive mirroring; SQLite
specifically documents network filesystem locking risks in
[its network deployment guidance](https://www.sqlite.org/useovernet.html).

Apply state machine: `download ciphertext -> authenticate -> validate into private
staging -> compatibility check -> acquire exclusive local operation and block
launches/mutations -> checkpoint current profile ->
activate directory and mapped metadata -> verify -> record applied revision ->
release maintenance`. Database and directory renames are not one atomic
transaction: a durable fsynced journal, ordered writes and restart recovery must
resolve every interruption. Until recovery finishes, all profile access that could
mutate or launch remains blocked. Never replace a user's healthy profile before
full validation. Keep the former data until explicit safe retention cleanup.

Archive validation must reject traversal, absolute paths, duplicate/case-colliding
names, symlinks/reparse points, reserved platform names, bombs, oversized manifests
and unknown schema versions. Existing archive checks are useful building blocks,
not evidence that the new protocol already meets all these requirements. Bound
memory, disk usage, number of entries and decompressed bytes before activation.
Decrypted staging receives private permissions, is never served over HTTP, and is
cleaned on cancellation/restart. Deletion is not a promise of physical secure erase
on SSDs. Locally decrypted browser profiles remain sensitive files.

Proxy secrets inside the encrypted snapshot are decrypted only after validation
and re-encrypted using the destination manager's local crypto service. Do not copy
`secret.key` or machine-specific paths. Imported profile metadata is allowlisted;
referenced groups/extensions need explicit mapping and validated portable content,
not trust in another machine's arbitrary library IDs or executable paths. Existing
local roles/grants stay local and are never enlarged by remote data.

Pin exact browser build/profile compatibility metadata, schema and required
extension capabilities. Initially allow only a tested same-OS/architecture/browser
matrix; block incompatible combinations with actionable guidance. Cross-OS profile
portability, saved credentials, extensions and fingerprint behavior require real
browser acceptance before widening support. Never downgrade or auto-migrate the
only snapshot. Keep the old revision and take a local checkpoint before an approved
migration. Sync operations must join and drain through the existing maintenance gate outside
the database, so offline updater work never races a transfer/apply worker.
Coordinate the sync journal with update/recovery maintenance; restoring
an older manager checkpoint must mark sync freshness unknown and reconcile online
before launching or publishing. Keep device ownership/high-water state in a dedicated protected control store
outside application-data rollback, coordinated with the maintenance gate. Missing
or restored control state is uncertainty, never permission to launch offline.
Do not roll back trusted sync high-water marks silently with application data.

## Delivery plan and release gates

Implement the following in dependency order, each as a separate reviewed issue/PR.
No production enablement until the final gate; experimental work stays opt-in.

| Work package | Depends on | Acceptance |
| --- | --- | --- |
| [S1](https://github.com/polyackiy/camoufox-profile-manager/issues/83) Protocol and threat-model specification | This proposal | Canonical wire format, trust/state machines, limits, threat tests and library/native packaging proof; independent crypto review before production. |
| [S2](https://github.com/polyackiy/camoufox-profile-manager/issues/84) Device identity, pairing and recovery | S1 | Both-device approval, key-substitution/replayed-invite rejection, OS key storage, clean-machine recovery, revocation/old-epoch tests and honest recovery freshness UI. |
| [S3](https://github.com/polyackiy/camoufox-profile-manager/issues/85) Encrypted closed-profile snapshots | S1 | Strict quiescent snapshot, authenticated stream/envelopes, corruption/truncation/reorder/unsafe-archive tests, secret-free metadata/logs and bounded resource use. |
| [S4](https://github.com/polyackiy/camoufox-profile-manager/issues/86) Self-hosted relay | S1 | Transactional CAS/checkout/membership, scoped authentication, resumable immutable blobs, quotas, idempotency, isolation and documented TLS/deployment/backup recovery. |
| [S5](https://github.com/polyackiy/camoufox-profile-manager/issues/87) Checkout, handoff and conflict engine | S2, S3, S4 | Two-device race, offline holder, sleep/clock skew, stale writer and lost-device takeover tests; every launch/mutation path enforces checkout. |
| [S6](https://github.com/polyackiy/camoufox-profile-manager/issues/88) Transactional apply and recovery | S3, S5 | Fault injection at every durable transition, full-disk/power-loss/cancellation retry, unchanged old profile on failed validation, update/rollback reconciliation. |
| [S7](https://github.com/polyackiy/camoufox-profile-manager/issues/89) Owner UI and onboarding | S2, S5, S6 | Clear local-only/held elsewhere/uploading/conflict/recovery states, secret handling, keyboard accessibility, revocation and recovery confirmations; no role/export-grant bypass. |
| [S8](https://github.com/polyackiy/camoufox-profile-manager/issues/90) Adversarial and platform acceptance | S1–S7 | Linux/Windows/macOS packaged two-device tests for supported combinations, malicious relay replay/fork tests, fresh device recovery, security review findings resolved and self-host operator guide. |

S8 must publish an exact support matrix and demonstrated guarantees. Include tests
for relay rollback after a client update, two disconnected clients seeing different
valid branches, revoked device returning, recovery with no trustworthy head, and
an old local application attempting to launch a synchronized profile. Format/schema
minimums must make unsupported older managers fail closed; that does not control
raw browser launches outside the manager.

Measure complete-profile transfer size/time before adding delta sync. Later work
may introduce encrypted chunk deduplication only with a separate metadata-leakage
analysis. Team sharing, organization key custody and per-member/per-profile
revocation, remote managed browser execution, and cross-device schedule ownership
are separate follow-up stages, not implicit extensions of local stage-three roles.

## S1 implementation references

The experimental foundation is specified in [sync-protocol.md](sync-protocol.md)
and [sync-state-machine.md](sync-state-machine.md). The
[owner bootstrap and compatibility contract](sync-trust-contract.md) defines the
required S2/S3 behavior. Implementation now includes protected enrollment, pairing,
closed snapshots, resumable relay uploads, durable checkout, transactional apply,
authenticated recoverable Trash, owner-approved delayed retention and owner UI. See the
[security review and threat-to-test record](sync-security-review.md) for library
choices, native proof, findings and remaining gates. Operational documentation is in
[sync-relay.md](sync-relay.md), [sync-engine.md](sync-engine.md),
[sync-apply.md](sync-apply.md) and [historical recovery](sync-historical-recovery.md).
