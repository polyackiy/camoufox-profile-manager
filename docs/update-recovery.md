# Offline complete recovery

Automatic executable replacement remains disabled pending trusted TUF root,
publisher policy and signed package-to-package acceptance on supported platforms.
Issue #63 remains open. These commands provide a complete local recovery point
around the existing manual installer flow; they do not downgrade the manager app.

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

## Settings and implementation boundaries

Settings uses stable releases by default. **Include preview releases** opts into
preview discovery for checking/preparation; changing it clears the previous offer.
The existing profile-backup/download flow remains manual. **Update recovery**
shows sanitized transaction status and permits cancellation only before replacement.

The implementation also includes TUF target verification with persistent metadata,
exact manager/browser compatibility manifests, native publisher checks and an
internal journalled directory replacement helper. See [the signing contract](update-signing.md).
The helper retains the previous package and calls restartable data restoration on
rollback, but is not an exposed installer or an API for running arbitrary packages.

Before one-click automatic installation is enabled, production root/key ownership,
signed metadata publication, platform publisher credentials and key rotation must
be provisioned. The verified archive-to-installed-tree binding, platform extraction
helpers, numbered schema migrations and validation-mode startup must be integrated
and tested with real signed package-to-package upgrades on every enabled platform.
Linux system installations stay under the package manager. A successful unsigned
build or mocked signature check does not satisfy these gates. #63 remains open;
#61/#62 track signing and real installation acceptance.
