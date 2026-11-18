# Action synchronization integration

**Goal:** Connect the internal #162 lifecycle to real cookie authority, private
Native Messaging and a final document check on reviewed follower tabs.

**Spec:** [Interactive synchronizer](../../interactive-synchronizer-design.md).

1. Authenticate the initiating cookie and bind the immutable user ID before a
   prepared launch. Use one database snapshot for administrator status, cookie
   expiry and every cohort lease at Start and before dispatch.
2. Reuse the existing capture extension and native host. Add owner-restricted
   IPC, launch identity, bounded I/O and explicit confirmation of the native
   tab/viewport binding. Keep launch credentials out of launch receipts and
   close the channel on revocation, navigation, startup failure and disconnect.
3. Use exact persistent-context pages and pin each follower's reviewed document.
   The initial click adapter validates location, geometry and hit target in the
   same document task as the click. Keep this adapter internal and document its
   page-world and synthetic input scope. Raw wheel playback remains disabled.
4. Wire an internal manager entry point with held maintenance/profile admission,
   explicit Start and cancellation-safe Stop. Maintain native package discovery
   through the fixed bundled console host path. Exercise source and frozen host
   gates, run the security review and all PR checks before merge.

No settings or HTTP route enables capture in this block. The user-facing
controller and installed manual-device acceptance remain tracked work.
