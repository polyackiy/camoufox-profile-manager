# Verified automatic updates: proposed design

Status: proposal for [#63](https://github.com/polyackiy/camoufox-profile-manager/issues/63).
This document specifies the complete target design. Recovery, trust verification
and transaction primitives are now implemented as described in
[implementation status](update-recovery.md); automatic executable replacement is
not enabled. The current release check and manual
installer flow remain described in [getting started](getting-started.md).

## Current boundaries

`core/updates.py` checks stable GitHub releases and filters asset URLs to this
repository. Update preparation makes per-profile recovery archives and refuses
to continue if any profile is skipped. These archives include Trash and browser
data, but exclude manager users, login sessions, groups as independent records,
schedules and global configuration. They are not a whole-installation rollback.
The desktop encryption key lives beside the database unless supplied externally.

The database currently adds missing columns and tables during initialization;
there is no numbered migration compatibility contract. A future updater must
introduce that contract before it can decide whether an older binary may open a
newer database. An official download URL or same-release checksum alone is not
an authenticity check.

## Decisions proposed for the first implementation

1. Updates require an explicit **Install update** action. Checking or downloading
   an update must never close a profile or replace the app without that action.
2. Use The Update Framework (TUF) for authenticated metadata and targets. Bundle
   an initial trusted root in the app; do not download the initial trust anchor
   from the same location as the proposed update. Use maintained client tooling
   rather than implementing signature verification and metadata rotation here.
3. Require platform publisher verification in addition to TUF target verification
   on macOS and Windows. Unsigned previews keep the manual installer flow.
4. Start with per-user macOS and Windows installation locations. System-owned
   locations remain manual until a separately reviewed privileged helper exists.
   Linux application replacement remains with the package manager; a verified
   download can be offered without invoking unattended `sudo` or replacing `/opt`.
5. Rollback before ordinary post-upgrade writes are enabled restores both the
   previous app and a complete data checkpoint, including after a failed migration.
   Once ordinary writes are enabled,
   restoring that checkpoint is explicit recovery and may lose newer changes.

These decisions need maintainer acceptance and the platform trials in
[desktop acceptance](desktop-acceptance.md) before automatic updates are enabled.

## Release authentication and compatibility

Keep root keys offline with threshold signing. A protected release job publishes
targets, snapshot and timestamp metadata only after package signing,
notarization, tests and the final asset audit succeed. Separate publishing
credentials from root keys. Document root-key rotation and recovery from a
compromised online signing key before operating this service.

The client persists trusted metadata versions and validates expiry, signatures,
target size and target digest through TUF. Missing, expired, rolled-back or
unverifiable metadata blocks installation while the existing app stays usable.
Retries do not reset this trusted state. A clock error requires correction; it
does not disable metadata expiry checking.

Signed target metadata must identify:

- app version, release channel and immutable artifact name;
- OS, CPU architecture and minimum OS version;
- publisher policy identifier and bundle/product identifier;
- updater protocol version and minimum supported updater version;
- input database schema range, resulting schema version and supported recovery
  path; these are a new contract, not inferred from the current column checks;
- whether the change requires a compatible Camoufox browser version. Updating
  the manager must not silently regenerate pinned hardware or upgrade the
  browser engine as a side effect.

Select by the installed package architecture, and verify the executable
architecture again after extraction. Reject cross-platform/cross-architecture
targets. Stable installations do not silently switch to preview releases.
Reverting a failed transaction uses the locally retained prior package, rather
than accepting an older remote target as a fresh update.

On macOS, verify the extracted app's signature, expected Developer ID Team ID,
bundle identifier, nested code and OS distribution policy/notarization. An
ad-hoc signature passing `codesign --verify` is insufficient. On Windows,
verify both installer and app with the OS Authenticode trust policy and an
explicit publisher/key-rotation policy; matching a certificate's display name
alone is insufficient. If publisher verification cannot complete, do not run
the downloaded code. Policy identifiers and credential owners are unresolved
until [#61](https://github.com/polyackiy/camoufox-profile-manager/issues/61) is met.

## Complete data checkpoint

Before replacement, enter a maintenance state shared by every cooperating
process using the database. Pause schedules, reject mutations/new launches,
and wait for in-flight operations to finish. The implemented maintenance gate coordinates updated manager processes;
older binaries must be closed separately and cannot be made to cooperate
retroactively. Refuse to
proceed while any browser or another process can write the profile directories.
Ask the user to close profiles rather than terminating account sessions silently.

Create the existing before-update profile archives for every profile, including
Trash. Any missing directory, unreadable credentials, skipped profile, archive
failure or insufficient disk space blocks the transaction. Hold the maintenance
gate across the full checkpoint; an archive batch followed by concurrent edits
is not a coherent snapshot.

Also checkpoint the SQLite database through its backup API, preserving all
tables, and copy the closed profile directories, key file and required local
configuration. Include the resolved configured storage paths, not just default
desktop paths. For an externally supplied key, retain its configuration reference
and require it to be available for restore; never invent a replacement key.
An installation with an incomplete external-data map remains manual.

Verify database integrity, archive readability, file inventories and locally
recorded digests before publishing a completion marker. Store the checkpoint
outside the installation folder, with private permissions/access controls. It
contains credentials and session data; keep it local and out of logs/telemetry.
Protect update checkpoints from routine backup pruning until the transaction is
resolved. Define a bounded retention policy without deleting the last valid
checkpoint just to free space for another attempt.

## Replacement transaction and recovery

Use a private staging directory and a durable journal outside the app bundle.
The journal contains transaction ID, verified package version/digest, previous
package location, checkpoint ID and state; it contains no session values.
Write each state atomically and flush before the next irreversible operation.

| State | Condition to advance | Failure behavior |
| --- | --- | --- |
| Discovered | Trusted metadata, compatible target | Keep current app; offer retry |
| Downloaded | Size/digest and OS publisher policy verified | Remove incomplete staging; preserve current app |
| Prepared | Maintenance gate and complete verified checkpoint | Release gate; current app continues |
| Stopped | Browser processes, server and database handles closed | Refuse replacement while a writer remains |
| Replaced | Prior package retained, replacement committed by helper | Journal enables recovery after power loss |
| Validating | Expected version, successful migration and recovery checks | Restore prior app and complete checkpoint before normal writes |
| Committed | Health and data validation passed | Enable writes/schedules; retain recovery point |
| Recovery required | Automatic rollback could not complete | Keep data isolated; show recovery location and actionable error |

The signed helper runs outside the directory being replaced. Validate canonical
installation/staging paths and ownership; reject path traversal and symlink or
reparse-point escapes. On macOS, stage the app on the installation volume,
retain the prior bundle and journal each rename: a multi-step directory swap is
not assumed to be atomic as a whole. On Windows, use a retained helper and a
per-user installer only after all executable handles are closed. A locked file
or permission failure preserves the old installation and checkpoint.

The new process starts in validation mode with schedules, browser launches and
ordinary mutations disabled. It validates expected app/schema versions,
database integrity, referenced profile paths and key readability before signaling
success to the helper. A `/health` 200 alone is insufficient. Interrupted or
failed migrations restore the coherent database/directory/key checkpoint while
all writers remain stopped. Never attach an old binary directly to an unknown
new schema. If rollback fails, retain both copies and the journal for recovery.

After commit, any downgrade requiring old data asks the user explicitly and
states the checkpoint date and potential loss of post-upgrade changes.

## Implementation and release gates

1. Accept this design; assign publisher/key owners; set supported installation
   locations and approve the migration schema contract.
2. Implement trusted metadata/target verification and negative tests: tampering,
   wrong architecture/OS/publisher, expired/replayed metadata, unsupported schema,
   interrupted download and key rotation. No replacement in this stage.
3. Implement cross-process maintenance and complete checkpoint/restore. Test
   users, groups, schedules, Trash, profile identity, cookies and encrypted proxies,
   plus missing keys, partial backups, disk-full and another active writer.
4. Implement the platform helper/journal and inject interruption at every state
   boundary. Test denied permissions, file locks, failed migrations, launch/health
   failures and rollback interruption; prove that one complete recovery point
   survives each failure.
5. Exercise the downloaded, signed package-to-package path on every enabled OS
   and architecture. Source tests and successful CI packaging do not meet this gate.
   Until #61 and #62 are satisfied, retain the manual installer flow.

Issue #63 acceptance maps to sections above: authentication/compatibility,
complete backup refusal, persistent data/key ownership, journal/migration rollback,
and the real-platform release gate. Approval of this proposal completes the
design stage; it does not establish that an automatic updater has shipped.

## References

- [TUF overview](https://theupdateframework.io/docs/overview/): authenticated metadata and targets; installation is still the application's responsibility.
- [SQLite backup API](https://www.sqlite.org/backup.html): coherent database snapshots.
- [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
- [Microsoft SignTool](https://learn.microsoft.com/windows/win32/seccrypto/signtool).
