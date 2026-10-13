# Durable synchronization coordinator

`sync/engine.py` implements synchronous coordination for S5. The manager remains
responsible for authenticated owner actions, browser/process locks, maintenance,
private snapshot capture/encryption and S6 activation. An encrypted file passed to
`publish` must be the exact immutable snapshot produced under those safeguards.
The engine does not merge profiles or stop a browser on an unreachable computer.

## Public integration

Construct `SyncEngine(service, local_user_id, relay_client)` after explicitly
unlocking the device slot with `service.device_keys`. That method never reads the
authority/recovery slot. The client root/device must match the installed enrollment.
All controls use the stable local user ID; a renamed/recreated login is not the
same owner.

- `enable(local_id)` allocates a new opaque remote UUID, and is restricted to a
  verified owner installation. It never adopts relay-supplied genesis/history.
- `bind_paired(local_id, remote_id)` reads an already consumed, authority-signed
  pairing checkpoint from protected control storage. A supplied remote UUID alone
  cannot enroll a downloaded profile. Local IDs and remote UUIDs remain separate.
- `claim(local_id)` journals intent before requesting checkout. An accepted claim
  produces `apply_required`, not launch permission.
- `record_applied(local_id, exact_revision)` is called only after S6 has verified
  journalled activation and its current DB pointer/metadata. For owner genesis,
  the existing closed local profile may be explicitly recorded against `None`.
- `publish(local_id, manifest, signature_base64url, ciphertext_path)` refreshes
  signed membership and reconciles signed history before publication. It rejects
  stale recipients/counters/parents and retains the exact encrypted file on failure.
- `begin_handoff` durably blocks launch before browser close/capture. It requires a
  subsequent successful snapshot publication before `release` is permitted. The
  handoff flag survives reconciliation; an uncertain release never permits launch.
- `takeover(local_id, authority, warning_ack=True)` requires a holder already
  excluded by verified owner-signed membership and an explicit authority signature.
  It increments checkout generation, requires activation and cannot stop the old
  browser. There is no automatic timeout stealing.
- `invalidate(local_id)` preserves high-water and pending state but removes the
  installed-generation proof following application-data restoration.
- `pairing_checkpoints()` first reconciles every mapped profile and then returns
  protected checkpoints. HTTP clients must not supply replacement checkpoint lists.

`may_launch_from_control(service, user_id, local_id)` is a key-free, network-free
fence for all manager launch/mutation entry points. It requires an exact confirmed
installed head/generation, an active device, no pending transfer/handoff, no known
fork and no unfinished `apply/<local_id>` journal. The integrating core must also
validate the live local administrator identity, local permissions/process locks,
maintenance state and the S6 journal's activation target against the current app DB.
A journal marked complete alone cannot prove that the DB was not rolled back later.
The instance method `may_launch` delegates to this same fence.

## Authenticated history and observed high-water

The relay's checkpoint JSON is never an authority source. The engine replays a
bounded chain from genesis using retained owner-signed memberships and device-signed
transition receipts. Receipt verification binds exact relay origin, POST endpoint,
owner/device, raw-body hash, signature domain, predecessor checkpoint, membership
at that transition, profile, parent, generation and full recipient identities.
Commit manifests additionally require their own device signature. Takeover requires
a separate owner-authority signature and a revoked prior holder.

The resulting checkpoint must contain the independently installed checkpoint in
its history and exactly match the relay endpoint. Owner membership history must
pass through the independently pinned signed membership, without gaps or alternate
same-epoch documents. Current membership must never regress below any previously
observed validated descendant, even when the previous network operation failed.

Each authenticated receipt is retained under `event/<remote-profile>/<sequence>`.
Every subsequent reconciliation must include every retained receipt identically.
This matters when a new valid commit/release was received but a later checkpoint
request failed: returning an old valid prefix must not restore the old launch
permission. Known signed forks are sticky conflicts; returning the previous branch
does not automatically clear them. Candidate receipts and the original history are
preserved. Resolving a signed fork requires a separate explicit owner recovery
workflow; this module provides no unsafe last-write-wins escape hatch.

These signatures authenticate device consent, not relay consensus. A malicious
relay can conceal a newer membership or keep isolated clients on individually
valid branches. Previously unseen freshness requires independently trusted pairing
checkpoints; restored authority without them cannot silently adopt profile heads.
Offline work is allowed only for a device that already holds a durably confirmed,
installed checkout. Relay generations fence future publication, not offline Firefox.

## Crash and retry behavior

Protected local records include mapping, installation membership, historical
memberships, receipts, phase, installed head/generation and a pending operation.
Intent is durable before RPC/upload. A transport error leaves it in place. Lost
acknowledgements recover through exact signed request receipts in history; a 409
alone is never success. Local operation UUIDs identify journal entries; claim and
release replay identity additionally derives from the exact signed predecessor
and body, because the wire format does not contain a separate operation UUID there.

An upload retry reuses the same encrypted file and revision. Existing relay objects
must report the exact original writer/hash/length and `ready` state before commit
retry. An already committed pending revision is recovered from history. Unknown,
uploading, aborted or conflicting objects keep the original candidate locally;
changed plaintext must use a new encryption stream/revision after explicit recovery.

Membership enrollment/revocation produces signed successor documents locally;
the integrating authority service must publish them to the relay in order before
normal transfers. A relay behind the installed membership is blocked until this
explicit update completes. Arbitrary network errors never downgrade that pin.

Tests in `test_sync_engine.py` exercise actual signatures with an adversarial fake
relay and real durable control storage: forged receipts/checkpoints, old/forked
membership, pending checkout/release, lost ACKs, immutable upload retries, historical
truncation after observed progress, revoked holders, pairing trust and offline
activation gates. Real relay, browser and packaged platform acceptance remain
separate integration gates.

## Known membership and historical-head boundaries

A relay's unsigned checkpoint cannot project a newly advanced historical head onto
a newer membership epoch. Reconciliation retains the independently known profile
position (revision, generation and holder) and accepts advancement only when its
final signed transition is authorized under the current trusted membership. Older
intermediate transitions are permitted if a current-member signed successor
endorses their resulting position. An unchanged anchored historical head remains
readable after membership changes.

Candidate receipts may be retained during a failed reconciliation, but their mere
presence never promotes them to the profile's trusted head. The same gate applies
on retry and after reopening the engine. This prevents a revoked device and a
malicious relay from making a newly signed old-epoch commit or release appear
current after the recipient already knows the revocation.

For lost-device recovery, observe and independently retain the last desired profile
head before publishing revocation whenever possible. If a previously unknown old-
epoch head appears only after revocation, its creation time cannot be proven from
the relay; automatic takeover/activation must remain blocked until a current trusted
signature endorses it. Historical fork recovery remains a separate explicit path
and makes no assertion that the recovered data is latest.
