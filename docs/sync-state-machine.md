# Sync checkout reference state machine (S1)

This is the executable **protocol reference**, not a shipped sync engine. It is
implemented in `camoufox_pm.sync.state` and tested in
`tests/unit/test_sync_state.py`. Durable storage, relay transactions, signatures,
browser process supervision and application entry-point integration belong to
[S5 #87](https://github.com/polyackiy/camoufox-profile-manager/issues/87) and
[S6 #88](https://github.com/polyackiy/camoufox-profile-manager/issues/88). These
functions do not authorize an actual browser launch today.

## Inputs and trust

`Checkpoint` binds owner ID, global profile ID, signed membership epoch, checkout
generation, revision head and holder device ID. Local usernames and roles are not
remote identities. Cryptographic validation of membership, signatures, owner/device
bindings, revision ancestry and RPC operation IDs is a **separate precondition**.
The Python dataclasses perform structural checks, not signature verification.

`State` contains the local enrolled device ID, the last trusted checkpoint and a
phase. `observed_checkpoint` separately retains authenticated authority high-water
evidence, even while the applied checkpoint is unchanged and freshness is unknown.
`observed_heads` retains unreconciled head evidence across repeated observations;
a later relay reply cannot silently erase a conflicting branch. Reconciliation
requires separately verified ancestry covering every retained head and clears this
evidence only after successful reconciliation. S5 must bound queued observations
and quarantine/stop intake under resource pressure, never discard security evidence
to continue operating. Returned values are immutable. S5 must commit them in a protected durable
control store before acting on their permissions. A constructor is not proof that
anything was persisted. The caller must coordinate state changes with all local
processes; copying an old in-memory `State` is not an authorization mechanism.

`reconcile` accepts only a checkpoint established by authenticated online
coordination and independently trustworthy freshness. A signed relay response
alone is insufficient: it can be a valid but stale branch. Initial enrollment
transfers a recent checkpoint from a trusted owner device through the verified
pairing channel. Recovery without such evidence stays `freshness_unknown`.
For a changed head, `ancestry` is the inclusive chain already verified by the
protocol layer; arbitrary strings cannot be treated as a history proof.
A conflicting branch requires explicit owner recovery, preservation of both
versions and a separately reviewed trust-reset procedure; `reconcile` does not
silently replace a known head with a non-descendant.

## Permission and transition vectors

| Input / event | Required result |
| --- | --- |
| No durable control record, restored application backup, uncertain ownership | `freshness_unknown`; launch, mutation and publication blocked |
| Current verified checkpoint held by this device | `held_locally`; offline work eligible, subject to local operation locks |
| Current checkpoint held by another device | `held_elsewhere`; blocked regardless of elapsed time |
| Handoff starts | Persist `handoff_pending` **before** any commit/release RPC; block new local work |
| Same checkpoint observed during pending handoff | Remain pending; a repeated relay reply does not cancel durable intent |
| Snapshot commit acknowledgement matches exact old head, epoch, generation and holder | Advance head; pending handoff remains pending |
| Release acknowledgement matches exact committed checkpoint | Persist `released`; remain blocked |
| Network error, crash or uncertain release response | Preserve pending intent; reconcile online before any local launch |
| Same owner/profile but lower epoch or generation | Reject rollback; retain trusted checkpoint |
| New epoch or generation observed | `freshness_unknown`; independently reconcile authority and ownership |
| Different head/holder at equal epoch/generation without history proof | `conflict`; keep old checkpoint and preserve both full versions |
| New holder without strictly larger checkout generation | Reject |
| Verified revocation | `revoked`; no observation, restore or reconciliation re-enables this enrolled identity |
| Different owner or profile | Reject, even if the signature is otherwise valid |

There is no heartbeat expiry, timestamp winner, timeout checkout steal or automatic
conflict overwrite. Generation fences publication at a correct relay; it cannot
stop an old Firefox process on an unreachable device. A malicious relay can fork
valid histories and prevent clients learning about revocation. These guards do not
provide global consensus or instantaneous revocation.

## S5 adapter contract

1. Require authenticated local administrator plus explicit owner enrollment for
   enabling synchronization; neither an anonymous local session nor the legacy
   API key establishes an owner identity.
2. Guard API, CLI, scheduler, profile mutation, import/restore and stale-process
   entry points using a fresh durable control-store read and the same exclusive
   local operation boundary. Never cache `may_work_offline` across transitions.
3. To hand off, persist/fsync `begin_handoff` intent with a stable operation ID,
   block launches, stop the browser and independently prove quiescence. Prepare
   and authenticate the closed snapshot. Refresh membership online before
   publishing with exact parent/epoch/generation CAS. `publication_ack` may only
   follow an authenticated successful reply bound to that persisted operation.
4. Release exact committed head/generation atomically at the relay. Persist
   `release_ack` before reporting success. Crash injection must cover both sides
   of each fsync/RPC, including remote release followed by lost acknowledgement.
5. The recipient claims a strictly newer generation. Download, authenticate,
   validate and activate the snapshot through S6 before durably marking the
   checkout usable. A relay grant by itself does not mean files are safe to launch.
6. Membership change or lost-device takeover quarantines pending candidates and
   unsent local work. Revocation requires new authority and the existing takeover
   warning: the lost browser may still run. Already downloaded secrets cannot be
   revoked retroactively.

## S6 recovery contract

The independent control store and apply journal must survive manager data rollback.
Missing or inconsistent records call `invalidate`, retaining high-water marks when
available. Do not derive ownership from restored profile metadata. All mutation and
launch paths remain blocked until the journal has recovered, the activated files
match the accepted revision, compatibility checks pass and fresh ownership is
confirmed. Offline work is eligible only after those durable requirements finish.

The 45 reference vectors cover immutable values, offline permission phases,
clock-independent checkout, CAS mismatch, pending release, rollback, missing
control state, takeover, branch divergence, ancestry, revocation and owner/profile
binding. They are not substitutes for the real two-device race, crash-injection,
malicious-relay and platform tests required before S8 production enablement.
