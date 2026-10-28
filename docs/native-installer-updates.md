# Native installer updates (unreleased)

The settings page can download and run the official OS installer after an
explicit **Download and install → Update and restart** confirmation. This new
flow supports Ubuntu/Debian x64 `.deb` installations in `/opt/camoufox-pm` and
registered per-user Windows x64 installations. Source, portable, macOS and
unsupported installations keep the existing manual flow. Installations configured
with `CPM_UPDATE_POLICY` continue using their existing verified-update policy.

This is an explicit unsigned-installer convenience flow. It does not enable or
relax the separate TUF/publisher-verified replacement mechanism described in
[automatic updates](automatic-updates.md). Public installers remain unsigned.
HTTPS and the matching release's SHA-256 sidecar detect download corruption;
they do not independently authenticate the publisher. OS security prompts and
organization policy remain applicable.

## Normal operation

1. Check stable releases, or explicitly select the preview channel. Confirm the
   displayed version after saving work in browser profiles.
2. Download that exact release's platform installer and checksum from the official
   repository. The server rechecks the selected version/channel; it never accepts
   caller-supplied URLs or shell commands. Download progress appears in settings.
3. Copy a helper outside the application installation, close the desktop, and
   finish the server's browser shutdown. The helper waits for the old process.
4. Recheck the package digest and, on Debian, its package identity, architecture
   and version. Create a private offline checkpoint of the database, profile
   storage, installed browser cache, encryption key and local configuration.
   Missing profile storage, invalid credentials, another running manager/browser,
   incomplete backups and insufficient space stop installation. An absent browser
   engine is allowed, so fetching the engine is not a prerequisite for updating.
5. Hold maintenance admission closed while invoking the system installer. Ubuntu
   uses `pkexec apt-get --yes --no-remove install <package.deb>` and may ask for
   administrator authorization. Windows invokes the per-user NSIS installer using
   ShellExecuteEx and `/S /D=<existing installation>`. Download provenance is
   retained for Windows attachment policy. Security controls are not disabled.
6. After the installer succeeds, release maintenance and reopen the installed
   application. If reopening fails, open it from the applications menu.

No update is installed merely by checking for updates. There is no periodic or
unattended installation. Download failures leave the running app usable and need
an explicit retry. The browser engine and pinned fingerprints are not upgraded
as a side effect of installing the manager.

## Recovery and disk usage

All update files are beside the configured database, outside the application:

- `update-control/installers/<id>/`: package, copied helper, `request.json` and
  `install.log`.
- `update-control/checkpoints/<checkpoint-id>/`: complete data checkpoint,
  manifest and `key-reference.json` containing the original manager version.
- `update-control/native-install.json`: an unresolved installation marker.

Windowed builds without Python console streams write helper diagnostics to
`update-control/recovery-command.log`; inspect it if `install.log` is empty.

Failed downloads remove their temporary package/helper copy. Completed and
interrupted installation directories and checkpoints are retained for diagnosis
and recovery. Allow space for the installer, a copy of the application and a copy
of the configured data/browser cache. After verifying the new application works,
completed `installed` request directories may be removed while the helper is no
longer running; preserve the checkpoint until it is no longer needed. This flow
does not automatically prune checkpoints or guarantee automatic rollback.

Before installation starts, backup failure releases its maintenance gate and the
old application can be reopened. If `request.json` records `installation_failed`,
the system installer returned or failed to launch, and maintenance remains closed.
After correcting the reported cause, explicitly run the retained helper with
`--native-installer-helper <id>` (the executable inside that request's `helper/`
directory, using the original data-directory environment). It rechecks the package
and reuses its retained checkpoint. A failed retry never releases maintenance.

An interrupted `installing` state is intentionally **not** automatically retried:
the OS installer may still be running. Finish or repair the native installation
first, while keeping the manager closed. After an administrator has verified the
installed version, the package-manager state, and that no installer remains, they
can archive `native-install.json` and use the existing offline
`update-recovery cancel` command to release checkpoint maintenance. Do not remove
the maintenance database. For rollback, reinstall the manager version recorded
in the checkpoint, then follow [offline recovery](update-recovery.md); restoring
loses changes made after that checkpoint. An interrupted `backing_up` state without
the installation marker can be resolved using the existing checkpoint cancellation
procedure. Never delete a retained checkpoint to work around a failed update.

## Validation status

Unit/API tests cover download restrictions, corruption, duplicate requests,
checkpoint refusal, installation failure, failed retry, maintenance protection and
reopen failure. Mocked browser tests cover explicit consent, the selected channel,
progress, error/retry behavior and Simplified Chinese text. These do not prove a
real package-to-package upgrade. Before shipping, exercise actual Ubuntu and
Windows upgrades, denied authorization/security prompts, file locks, power loss,
and recovery on disposable installations. Windows native execution has not been
verified on a Windows machine in this development session.
