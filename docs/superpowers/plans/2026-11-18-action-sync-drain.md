# Action synchronization admission and drain

**Goal:** Implement the first manager-owned lifecycle block of #162 and keep it
internal until native transport, reviewed tab mappings and authority are wired.

**Architecture:** `core/action_sync.py` owns one reviewed cohort per browser
manager. It receives only authenticated `CaptureGate` events. A bounded queue per
target preserves order and a manager-supplied authorization check runs before
playback. Stop revokes capture and clears waiting work while retaining the
reservation for dispatched commands. `BrowserSessionManager` fences new input
before closing any cohort member and checks the drain before returning its lease.

**Spec:** [Interactive synchronizer](../../interactive-synchronizer-design.md).

- [x] Add regressions for explicit start, per-target order, late authority
  revocation, overflow, timeout, repeated cancellation and close/lease ordering.
- [x] Implement `ActionSync` and `ActionRun` with fixed failure categories and
  no persistent event or capability state. Keep pinned browser identities and
  reject replaced or closing sessions before dispatch.
- [x] Connect close, natural exit and shutdown through the existing browser
  teardown path. Bound waits and retain tracked sessions on uncertainty.
- [x] Verify with real capture validation, browser lifecycle tests and lint/types.
- [ ] Follow-on work in #162: bind current administrator cookie authority and
  lease/maintenance/workflow admission, launch the owner-restricted native host,
  validate exact tab/origin/geometry immediately before dispatch, coordinate
  scoped close-all with cohort teardown, and exercise
  installed-manager parity. An expired or dropped transport cannot trigger replay.

The controller, window mapping UI and installed preview are covered by #163.

Independent review exercised three lifecycle races: pending startup authorization,
sequential cohort close, and an exit callback allowing a follower to send before
cleanup. Regression tests prove those paths stop admitting work and preserve
leases until results settle. A separate preflight/submit interface makes the final
authority, browser identity and deadline check explicit. The production adapter
must demonstrate the send and settlement contract before it can be wired.

The Start cancellation regression failed on Python 3.11 before the fix. The
Start caller now waits on the owned authorization task and commits the transition
only after a successful, timely wait. All 14 lifecycle tests pass on Python
3.11. The combined capture and browser lifecycle suite passes 105 tests.
