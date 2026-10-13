# Self-hosted encrypted synchronization relay

`camoufox_pm.sync.relay.create_relay_app` creates an independent FastAPI ASGI
application. It does not import or grant access to the manager's local users,
profiles, API keys or browser processes. Use the `sync` installation extra.
Clients encrypt closed snapshots before upload. The relay never receives owner
private keys, recovery secrets, encryption keys or plaintext browser sessions.

## Deployment boundary

Run the relay under its own OS account, with a persistent local private directory
outside the manager data and rollback directories. The directory contains a
FULL-synchronous SQLite database and encrypted immutable objects. Use a local
filesystem with reliable SQLite locks, fsync and hard links; network shares are
not supported. Preserve the database and objects together when taking backups.
Restoring a relay backup can be stale: clients must retain independent high-water
state and reconcile rather than treating the restore as current authority.

Construct the application from operator configuration, for example:

```python
import os
from pathlib import Path
from pydantic import SecretStr
from camoufox_pm.sync.relay import create_relay_app

app = create_relay_app(
    Path(os.environ["CAMOUFOX_RELAY_DATA"]),
    relay_url="https://sync.example.org",
    registration_token=SecretStr(os.environ["CAMOUFOX_RELAY_REGISTRATION_TOKEN"]),
)
```

The registration token must be independently generated with at least 256 bits of
entropy. The adapter requires at least 32 characters, but character count alone
does not establish entropy. Supply it through a secret manager or protected
operator environment, never command-line arguments, repository files, URLs or
access logs. Rotate it by restarting with a new value. It authorizes creating new
owner namespaces only; it cannot replace an existing root or sign membership.

Run ASGI behind a TLS reverse proxy. Bind the upstream to loopback or a private
socket and trust forwarded scheme headers only from that proxy. Block direct
public access to the upstream. Configure request-header limits, per-IP rate
limits, connection limits and a request-body ceiling no larger than the chosen
blob limit. Disable proxy/request-body logging and caching. Application endpoints
reject HTTP unless explicit development mode uses a loopback HTTP origin **and**
the actual connecting client is loopback. Production relay origins must be HTTPS.
The current adapter exposes no public Swagger/OpenAPI endpoints.

A dedicated executable is available after installing the `sync` extra:

```sh
camoufox-sync-relay --generate-registration-token /srv/camoufox/token
camoufox-sync-relay --data /srv/camoufox/data --origin https://sync.example.org --registration-token-file /srv/camoufox/token
```

Create `/srv/camoufox` as a private directory owned by the relay service account
first. The first command creates a new protected token file and refuses to
overwrite one. The second binds to `127.0.0.1:9877`; publish it through the TLS
proxy. Enter the token in the manager's **Settings → Synchronization → Connect
relay** form on the owner device. Pair subsequent devices through the separate
fingerprint-confirmed invitation flow. The token never appears in command-line
arguments or relay output. `python -m camoufox_pm.sync.relay_cli` is equivalent.

For disposable local development only, pass `--development-loopback` and an
explicit `http://127.0.0.1:9877` origin. This mode cannot serve remote devices.

## Signed API

Registration is `POST /v1/register`, authenticated with `X-Registration-Token`.
Its exact JSON fields are `root`, `membership` and `signature`. The signature is
an owner-authority Ed25519 signature over
`registration_signing_bytes(root, membership)`. Registration requires genesis
membership, the configured relay origin and a previously unused owner UUID.
Neither registration tokens nor device keys can take over a registered owner.

Subsequent requests authenticate with all five headers:

- `X-Sync-Device`: approved device UUID.
- `X-Sync-Time`: integral Unix seconds, within 300 seconds of the relay clock.
- `X-Sync-Nonce`: fresh canonical UUID for every HTTP attempt.
- `X-Sync-Body-SHA256`: lowercase SHA-256 of the exact request body.
- `X-Sync-Signature`: canonical unpadded base64url Ed25519 signature.

`request_signing_bytes` binds origin, method, path, owner/device IDs, timestamp,
nonce and body hash. GET requests sign the empty body and reject actual content.
Query strings and duplicate authentication headers are forbidden. Nonces are
consumed durably before execution; a retry uses a new nonce and reconciles state.
Retries are not implicit permission to repeat a state-changing operation. A
durable clock high-water mark blocks requests after server clock rollback until
the clock catches up, preventing replay after old nonce records were pruned.

`POST /v1/owners/{owner_id}/rpc` accepts these exact operations:

| Operation | Additional fields | Result |
| --- | --- | --- |
| `membership` | none | `root`, current signed `membership` |
| `memberships` | `after_epoch` | ordered `memberships`, `last_epoch` |
| `update_membership` | `membership` | direct authority-signed successor |
| `checkpoint` | `profile_id` | current `checkpoint` |
| `claim` | `profile_id`, `expected` | new checkout and signed-request receipt |
| `release` | `profile_id`, `expected` | released checkout and receipt |
| `commit` | `profile_id`, `expected`, `manifest`, `signature` | advanced head and receipt |
| `manifest` | `revision_id` | committed `manifest`, writer `signature` |
| `history` | `profile_id`, `after_sequence` | ordered `events`, `last_sequence` |
| `blob_status` | `revision_id` | original writer’s upload state, hash and length |
| `discard_upload` | `revision_id` | terminal `aborted` tombstone |
| `takeover` | `profile_id`, `expected`, `operation_id`, `warning_ack`, `authority_signature` | newer checkout and receipt |

A checkpoint has exactly `owner_id`, `profile_id`, `membership_epoch`,
`checkout_generation`, `revision_id`, and `holder_device_id`. The entire expected
checkpoint must match, including JSON types. A new profile starts at generation
zero with no head or holder. Claim requires no holder and increments generation.
Release requires the current holder and exact head. Checkouts **never expire**
through heartbeats, disconnects or elapsed time.

Takeover requires the current holder to have been revoked in current membership,
a remaining approved device's authenticated request, and a separate authority
signature from `takeover_signing_bytes(owner_id, profile_id, expected,
operation_id, device_id)`. `warning_ack` must be exactly true. It increments the
generation, retains the old head and assigns the requesting device. An ordinary
paired device does not hold the authority key. The owner must explicitly unlock
it and acknowledge that an unreachable old browser can still run and retain
previously downloaded secrets. A successful takeover does not make local files
safe or authorize launch; validation and activation remain client responsibilities.

History receipts retain the exact signed request body, signature and binding
fields. Membership and history pages contain at most 128 entries and are also
limited to 512 KiB; continue from the returned cursor until an empty page. A short
page need not be the last page. Clients must validate transition proofs and known
high-water marks; a signed historical request cannot prove that no later request
exists. A malicious relay can freeze or fork valid history.

## Encrypted objects and quotas

`PUT /v1/owners/{owner_id}/blobs/{revision_id}` streams encrypted bytes with a
mandatory Content-Length and signed digest. It reserves owner quota atomically,
verifies length and digest, fsyncs private storage, and publishes without replacing
an existing object. Revision IDs are never reused, including aborted uploads.
`commit` additionally verifies writer signature, current membership and complete
recipient set, epoch, generation, holder, exact parent head and matching uploaded
object. Publication is an atomic metadata transaction.

`GET` on the same path requires current membership and serves only committed
objects. Clients must independently check length, hash, signature, context and
AEAD before applying anything. All enrolled devices share the personal owner's
sync namespace; team roles and granular remote export permissions are outside
this initial relay model.

Default limits are the protocol's roughly 2 GiB object ceiling, 10 GiB retained
ciphertext per owner, 100 owners, 1024 profiles per owner, 10000 revision IDs and
membership epochs per owner, 64 MiB each for membership history and transition
receipts, and 4096 unexpired nonces per owner. JSON input/output is limited to
512 KiB. At most four upload and four download streams run concurrently per ASGI process;
downloads have a thirty-minute total deadline. Small authenticated RPC requests
have a sixty-second deadline and eight concurrent slots. read
silence over 30 seconds or total upload time over thirty minutes fails closed.
Configure lower blob/storage limits when appropriate. Committed history is
retained, so reaching a quota blocks writes; there is no automatic age-based pruning. Explicit owner-signed retention
decisions are required for historical ciphertext collection.

An interrupted upload retains its reserved quota and cannot become a head.
`discard_upload` explicitly cancels an uncommitted upload owned by the requesting
device, reclaims its quota and preserves its revision-ID tombstone. An upload still owned by a live process cannot be discarded, preventing an open
file from continuing to grow after its quota was released. Pending filenames bind the owner and revision IDs, so cancellation also removes
partial files left by a crashed worker before releasing the quota reservation.
If either removal fails, the reservation remains. Never delete committed objects to resolve quota
without a separately planned retention procedure.

## Evidence and limits

Relay tests cover real encryption/transport/decryption, signature/body/endpoint
binding, nonce replay, clock rollback, atomic checkout races, restart persistence,
revocation, explicit authority takeover, quota cancellation, strict parsing and
production/development transport boundaries. They do not constitute an external
cryptographic audit or prove freshness against a compromised relay. End-to-end
browser quiescence, client recovery journals, device key storage, user-interface
integration and packaged acceptance remain separate release gates.

## Unpublished-upload expiry

Uncommitted uploads receive a durable seven-day grace period from reservation.
Run bounded maintenance under the relay's own OS account, for example from an
operator-controlled daily scheduler:

```sh
camoufox-sync-relay --data /srv/camoufox/data --expire-uploads --maintenance-limit 100
```

This local command needs filesystem authority, not a registration token; there
is no public cleanup endpoint. The limit is 1–1000 examined records per run.
Output contains aggregate counts only. Repeat maintenance while eligible records
remain; inspect failures rather than removing files manually. Until maintenance
runs, an overdue ready upload may still win a commit. Commit and expiry compete
through the same SQLite writer transaction.

Expiry first durably fences eligible objects as `expiring`, preserving their
quota. It skips workers whose PID and process creation time are still live, and
fails closed when process liveness cannot be established. It then removes the
exact pending/final files, flushes the directory, and changes the record to
`expired` with zero reserved bytes. A deletion failure or crash retains the fence
and reservation for a later sweep. Revision IDs remain permanently occupied.
`blob_status` reports `expiring` with its reserved length or `expired` with length
zero; the original writer and ciphertext hash remain. An explicit client retry
must reconcile before replacing an expired pending transfer with a freshly
encrypted revision; old ciphertext is retained locally. A resumable ciphertext upload continues only from its durable confirmed byte
offset; it never re-encrypts changed data into an existing revision.

Stop all old-version workers before upgrading the relay. Schema v4 migrates v1/v2/v3
transactionally and grants pre-existing unpublished uploads a full seven-day
period from migration; preserve a complete database/object backup first. Clock
rollback below the retained high-water mark stops new expiry decisions; already
fenced `expiring` cleanup can finish without trusting the clock. Restoring both
metadata and time state from an old backup cannot prove current time or freshness;
normal independent client history checks remain required.

This cleanup never selects committed revisions, including old ancestors, and
never deletes transition history. Profile Trash uses authenticated encrypted lifecycle snapshots. Committed
ciphertext retention is indefinite unless the owner explicitly signs a delayed
retention decision; the protocol history remains permanent.

Explicit authenticated cancellation uses the same crash-safe sequence with an
`aborting` fence and terminal `aborted` status. Only the original currently
enrolled writer may request it. Both transactions recheck the authenticated
device certificate. A retry or operator maintenance can finish an interrupted
cancellation; its retained quota is released only after successful deletion and
flush. Maintenance's `expired` count includes completed prior cancellations.
Neither `aborting` nor `expiring` permits commit or replacement upload.


## Resumable immutable ciphertext uploads

The current client uses `upload_begin` with the revision ID, total ciphertext
length and SHA-256. The enrolled original writer may repeat that exact request;
it returns the durable `offset` and fixed `chunk_bytes` (8 MiB). Changing the
writer, length or digest fails. The grace period is not extended by retry/status
requests. Legacy incomplete whole-body uploads restart at zero only after their
original worker is confirmed dead; already-ready legacy uploads remain usable.

`PUT /v1/owners/{owner}/uploads/{revision}/chunks/{offset}` signs the full URL path
(including offset) and the chunk's SHA-256. At most four bounded chunk requests
are buffered per process. An exact duplicate retained range succeeds; conflicting,
overlapping or noncontiguous bytes fail. A SQLite writer transaction serializes
file append, fsync and confirmed-offset persistence with other upload workers,
expiry and cancellation. After a crash, bytes beyond the confirmed offset are
truncated before another append. No partial object can become a profile head.

`upload_finish` verifies the complete length and SHA-256 before publishing the
immutable file. It can recover a crash after rename but before the ready state
was recorded. Full-file hashing currently holds the SQLite writer lock with a
30-second deadline; operators should allow for a temporary pause in other writes
when finalizing very large objects. Keep relay data on responsive local storage.
The client keeps its original encrypted file and resumes on explicit Retry,
including after a lost chunk acknowledgement. Plaintext is never sent or resumed.


## Explicit delayed retention of historical ciphertext

Only an owner-authority-signed `SignedRetention` decision can select historical
ciphertexts for collection. A decision binds the owner/root, unique decision ID,
issue time, not-before time, and up to 64 sorted unique revision/profile IDs with
the exact SHA-256 of each canonical manifest. `retention_schedule` submits it
through an enrolled device; it cannot select any current profile head. Scheduling
is idempotent for the exact same signed decision. Device credentials alone never
authorize historical deletion.

The server retains a durable deadline at least seven days after receipt and no
earlier than the signed not-before time. This allows delayed request retries
without shortening grace due to clock skew. A separate root-signed cancellation
binds the full decision digest and is accepted only before collection starts.
`retention_status` returns the signed decision, advisory state (`scheduled`,
`cancelled`, `collecting`, `collected`) and signed cancellation when present.
Clients verify signatures rather than trusting relay status as authority.

Operators execute only eligible signed decisions using:

```sh
camoufox-sync-relay --data /srv/camoufox/data --collect-retained --maintenance-limit 100
```

The sweep rechecks every signature/manifest binding and protects all current
heads. Persistent download leases bind PID plus process creation time; live or
uncertain readers block collection, without relying on heartbeat expiry. Dead
readers can be reclaimed. A durable `collecting` fence precedes filesystem
removal; only successful deletion and directory flush releases quota. A crash or
failed deletion remains retryable, including after a backwards clock once the
deletion decision is already fenced. Cancellation and collection use the same
SQLite writer lock, so exactly one wins. Cancellation after collection starts is
rejected; there is no mechanism to restore already-collected ciphertext remotely.
Local backup files are never touched.

Collection keeps manifests, writer signatures, transitions, membership history,
retention decisions and revision IDs. Download of collected ciphertext returns
410 with the original signed manifest and owner-signed retention proof. The
client verifies that proof against the independently pinned root and expected
revision/hash/length before reporting owner-authorized unavailability. This proof
never establishes a newer profile head or permits launching a stale profile.
A complete relay backup must include these tables as well as all retained files.
