# Offline complete recovery

Automatic executable replacement remains disabled pending trusted TUF root,
publisher policy and signed package-to-package acceptance on supported platforms.
Issue #63 remains open for production trust provisioning and signed platform trials.
The manual commands below remain usable without certificates. The integrated
desktop transaction is described after them; it stays unavailable in unsigned
previews and source installations.

Close every manager instance and browser before running:

```sh
camoufox-pm update-recovery checkpoint
camoufox-pm update-recovery status
```

Use the same working directory, database configuration and encryption key source
on every invocation. Desktop users add `--desktop` so the stable desktop database
is selected; put this global flag before the subcommand:

```sh
camoufox-pm --desktop update-recovery checkpoint
```

Packaged applications dispatch the same recovery arguments before starting the
server or generating encryption keys. For example on macOS, invoke the bundle
executable from Terminal (adjust the installed application name/path):

```sh
"/Applications/Camoufox Profile Manager.app/Contents/MacOS/camoufox-pm" --desktop update-recovery status
```

On Windows, invoke the installed `camoufox-pm.exe` with these arguments from
PowerShell; on Linux invoke the installed desktop executable. Windowed bundles
without stdout/stderr append command output to
`update-control/recovery-command.log` beside the configured database. Open that
file to read checkpoint IDs, timestamps and versions; the command does not open
a desktop window. Console installations print directly to the terminal.

Checkpoint output includes its ID and UTC creation timestamp. Copies
include all database tables, profiles including Trash, the entire versioned browser
installation and its active configuration, the adjacent local encryption key and
the current directory's `.env` if present. Extension library and templates live in
the database. No remote storage or key service is contacted. Externally configured
keys must remain available; only their hash reference is recorded. Checkpoints are
private and retained outside app bundles, and are not routine profile backups.

To restore, close every manager instance and browser, keep the compatible manager
installer available and acknowledge the loss of changes since the checkpoint:

```sh
camoufox-pm update-recovery restore --checkpoint-id ID --confirm-timestamp TIMESTAMP
```

The checkpoint records manager/wrapper versions and the configured browser identifier.
Restore requires the exact recorded manager version; use its matching installer
before running recovery. Stored encrypted proxy credentials must be readable with
the original local or external key before either checkpoint or restore proceeds.

Restoration retains the newer failed directories and restores matching profiles,
database, key/config and browser together. It never switches only the browser after
profile migrations. Restore retry resumes the checkpoint journal if live database
or browser directories are temporarily absent. If restoration fails, maintenance
stays closed; rerun the same command after resolving the failure. Use a compatible
manager binary before opening the restored data. This command does not install or
restore the previous manager executable.

Interrupted **preparation**, before executable replacement, and abandoned
read-only checkpoint creation (`checkpoint_interrupted`) can be cancelled.
Partial checkpoint copies are retained:

```sh
camoufox-pm update-recovery cancel
```

A restart during maintenance refuses to migrate/open application data. The offline
CLI remains available; HTTP cancellation is available only in an already running
server. Cancellation is refused after replacement starts.

Only the manager's configured local layout is supported. Symlinks/reparse points,
missing required resources, uncertain ownership, unreadable encryption keys,
remaining profile leases (even expired), other manager/browser processes and
unverifiable private Windows ACLs block recovery. Process scanning supplements the
registry for older manager versions. Older binaries do not cooperate with this
maintenance gate: keep them closed throughout checkpoint/restore. An unrelated
Firefox process may also conservatively block the command. Legacy/remote unknown
operation records remain blocked; process birth identity proves local crashed
participants safe to remove without expiring live operations.

Firefox's POSIX `profiles/profile_*/lock` symlink is the sole narrow exception.
When its target matches Firefox's `IPv4:[+]PID` signature and the exclusive offline
checks pass, checkpointing omits this transient marker from copied data and
inventories. It never follows or deletes the link; rollback retains the original
marker with the failed state. Other symlinks, malformed lock signatures and lock
links elsewhere remain rejected. The marker alone does not prove a browser stopped.

## Integrated desktop transaction

Settings exposes only capabilities approved by the local runtime: download a
verified stable/preview pair, restart to install, retry an interrupted transaction,
and restore the previous version. Installation and rollback require confirmation;
rollback shows the checkpoint timestamp and explains that later changes will no
longer be active. The newer files are retained for recovery. Checkpoints and retained installation
trees are not automatically pruned; keep them until recovery acceptance is complete. With no trusted
policy or publisher credentials, Settings explains why automatic installation is
unavailable and keeps the manual backup/download flow.

The desktop closes profiles and the API before a helper proceeds. A private copy
of the current installation runs the helper outside the directory being replaced,
so Windows DLL locks do not require overwriting a running program. The helper
waits for the original process identity to exit, reauthenticates retained archives
against cached signed metadata without network access, and rechecks publisher and
complete file-tree integrity. HTTP requests can select a channel or action; they
cannot supply an executable, filesystem path, trust root or publisher identity.

A complete checkpoint precedes any browser-cache or application replacement.
The paired browser archive is fetched directly from upstream under separately
signed target metadata, safely extracted, and installed without modifying the
Camoufox engine. Once browser installation begins, cancellation cannot release
maintenance. Retry or matching-data rollback is required.

The installed manager runs a fixed validation command in a fresh process. It
checks the declared manager/schema version, verifies stored credentials remain
readable, applies numbered schema migrations, selects the exact browser pair,
and launches an isolated disposable profile. User profiles are not used for this
smoke test. Failed validation restores both the previous executable tree and the
matching data/browser checkpoint. Unknown future schema versions fail before
migration writes.

If startup encounters an incomplete transaction, a local recovery window opens
before database initialization or key generation. It does not start an API server
or require a readable application database. Only actions permitted by the durable
journal are offered. The offline CLI also remains available.

## Production enablement

The integrated path requires a frozen macOS/Windows installation with a locally
provisioned policy, trusted TUF root and native publisher identity. Linux system
installations remain under their package manager/manual installer. See
[the deployment and signing contract](update-signing.md).

Certificate-independent tests cover archive authentication and extraction,
full-tree tampering, real subprocess acceptance, failed migrations, journal
interruption recovery, browser pairing and timestamp-confirmed binary/data
rollback. They use synthetic packages and isolated temporary data. They do not
establish that an unsigned public preview can safely self-replace.

Remaining release gates are production root/key ownership, authenticated metadata
publication/rotation, Apple Developer ID/notarization and Windows code signing,
and real signed package-to-package acceptance on each enabled OS/architecture.
A successful unsigned build or mocked publisher check cannot satisfy these gates.
#63 stays open for these gates; #61/#62 track signing and installation acceptance.
