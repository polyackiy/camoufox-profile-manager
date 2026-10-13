# Owner identity and pairing primitives

`sync/identity.py` implements the identity portion of S2. It does not store keys,
authenticate a local administrator, prove recovery, consume invitations or contact
a relay. Those operations belong to the durable owner service. The module uses
public Ed25519 and X25519 APIs from `cryptography`; no custom signature or key
agreement primitive is used.

## Public records

An `OwnerRoot` binds an opaque owner UUID, normalized relay origin, authority
signing public key and a separate recovery encryption identity/key. HTTP requires
an explicit development flag and a literal loopback address or `localhost`.
Credentials, paths, query strings and fragments are rejected. Changing the relay
origin changes the root digest and therefore requires an explicit new trust
ceremony; silently redirecting an existing owner is unsupported.

A `DeviceCertificate` contains an owner UUID, device UUID and separate device
signing/encryption public keys. The owner-signed `Membership` contains the complete
sorted active device list, root digest, epoch and previous signed-record digest.
Genesis has epoch one and no predecessor. Each successor must increment by exactly
one and match the exact previous digest; signatures do not justify skipping gaps.
Duplicate device IDs or keys and reuse of authority/recovery keys as device keys
are rejected. Certificates derive authority from their inclusion in verified
membership, not from self-supplied public keys.

`verify_membership` accepts genesis, one direct successor of a caller-trusted
previous record, or an exact independently trusted digest. Callers MUST load that
previous record/digest from protected durable high-water state. Passing a digest
computed from the incoming network record defeats freshness protection. The
function authenticates even supplied previous records, but it cannot determine
whether the caller selected its newest stored checkpoint. Forks and older epochs
must block operations until explicitly reconciled.

## Pairing

The new device prepares a `PairingRequest` binding the owner-root digest, invitation
UUID, exact device certificate, issue time and expiry (at most fifteen minutes).
The full SHA-256 `pairing_fingerprint` must be independently confirmed by both
sides. The root digest binds the relay origin and owner authority, so a relay key
substitution changes the fingerprint.

The authority approves membership first and signs a `PairingApproval` containing
the exact request digest, full signed membership plus its digest, and sorted
profile checkpoints. Checkpoints bind selected head, checkout generation and
holder. The trusted owner service supplies independently reconciled checkpoints;
the primitive cannot establish their recency. Only explicitly selected historical
profiles/key envelopes may subsequently be transferred. An empty checkpoint set
means no inherited profiles; it is not permission to trust relay-provided history.

`verify_pairing` checks expiry, root, fingerprint, exact request, membership,
device inclusion and approval signature. The receiver MUST atomically consume the
invitation and install trust/checkpoints before enabling any dependent operation.
The sender MUST check its durable invitation record, require authenticated owner
approval, and atomically consume or journal the invitation before releasing the
approval. Neither a UUID nor an expiring signature provides one-use semantics.
Repeated verification of a valid approval is intentionally possible at this layer.

## Secrets and serialization

`bootstrap` generates independent authority, recovery and device private keys.
`BootstrapSecrets.authority` projects an `AuthoritySecrets` object; `.device`
projects a `DeviceSecrets` object. Their explicit `to_bytes`/`from_bytes` formats
have different fixed prefixes and lengths. There is no combined bootstrap export.
A device vault slot contains no authority/recovery private material; an authority
recovery package contains no original device private keys or device identity.
Recovery must create and explicitly authorize a fresh device, not clone the lost
one. All secret container representations are redacted.

These exports are sensitive raw bytes in memory, not encrypted persistence
formats. The owner service must immediately encrypt them or put them into an
explicit protected credential store. Python cannot promise memory zeroization.
Do not log or return these bytes through ordinary status/API responses.

Public wire input is limited to 256 KiB, nesting depth eight, strict immutable
models, no unknown fields or duplicate JSON keys, canonical UUID/base64 forms and
bounded counters. Canonical bytes use sorted ASCII JSON. Record digests and
signatures have separate operation/type-specific domains beneath
`camoufox-pm/sync/experimental-v1/identity/`. Public crypto boundaries revalidate
records even if a caller bypassed normal Pydantic construction.

`tests/unit/test_sync_identity.py` covers owner/relay/key substitution, old epochs,
signed forks, gaps, revocation, expiry, authority misuse, secret projection,
malformed wire input and checkpoint signature tampering. Full one-use, key-store,
clean-machine recovery and crash behavior require owner-service integration tests.

## Sharing profiles with an already paired device

A profile created after device pairing needs a separate owner-signed profile code.
The relay cannot introduce a previously unknown profile as an independently trusted
checkpoint. The owner generates the code from reconciled protected local bindings,
selects an existing member device, and transfers it through the trusted channel.
No device keys or local accounts are re-created.

The code binds the pinned owner-root digest, target device UUID, a monotonically
increasing per-recipient sequence, invitation UUID, exact signed membership and at
most 64 profile checkpoints under a separate signature domain. Recipient acceptance
atomically retains the code, exact signed membership epoch, sequence high-water mark
and only previously unknown profile anchors. Existing local mappings, anchors and
checkpoints from initial pairing are never overwritten. Identical latest-code
replay is idempotent; older or different equal-sequence codes are rejected. Known
revocation, membership forks, wrong local administrator/device and a membership
observation newer than the code fail closed.

The exact membership is pinned so a relay cannot substitute a sibling authority-
signed membership with the same epoch. Imported profile heads remain historical
lower bounds; engine reconciliation must validate the full signed history before
claiming or activating a profile. A code does not confer launch permission.
