# Coordinated manager and browser updates

Design follow-up: [#63](https://github.com/polyackiy/camoufox-profile-manager/issues/63).
This describes the update architecture. See [implementation status](update-recovery.md)
for the integrated transaction and remaining production release gates. Unsigned
public previews retain manual installation until trust provisioning and signed
platform acceptance are complete.

## One compatible pair

A manager release must declare its tested Python wrapper, Playwright range and
paired Camoufox browser. Upstream now publishes the wrapper's browser pairing;
use that metadata instead of independently choosing the newest browser binary.
An explicit expert-selected browser remains visible as an override, with a
compatibility check before launch. Stable is the default channel; preview is an
explicit choice, never an automatic promotion.

The update screen should show installed and target manager/browser versions,
download size, changes relevant to profile users, and whether a restart is needed.
Downloading may happen while profiles run. Applying must wait until browsers and
file operations close; schedules pause during the transition. Closing a window
must not silently cancel an almost-finished update or discard its progress.

## Transaction and recovery

1. Resolve a compatible pair and verify OS, architecture, signatures, checksums,
   free space and the ability to write the target directories.
2. Download into staging, support retry/resume, and verify before installation.
   Keep the installed pair available until acceptance succeeds.
3. Close profiles, hold installation/recovery locks and pause the scheduler.
   If another process owns a profile, show which operation is waiting.
4. Take a complete installation checkpoint: consistent application database,
   profile archives, templates, extension library, saved views, schedules,
   settings and the encryption key. Protect the key and credentials with the
   same access controls as live data. Today's Settings profile backups alone
   are insufficient for whole-application rollback.
5. Install the manager using an OS-appropriate verified updater/helper and stage
   the paired browser through upstream's versioned installer. Never overwrite a
   running executable or patch the Camoufox engine.
6. Persist an update journal before each transition. Restart, perform database
   migrations, verify assets and run a disposable-profile launch smoke test.
   Resume schedules only after this acceptance step.
7. Mark the pair accepted and retain the previous pair/checkpoint for a defined
   recovery window. Report failure with one retry/restore action and useful logs.

Rollback restores the matching data checkpoint as well as binaries. A previous
Firefox may refuse or corrupt a profile already migrated by a newer Firefox;
switching the browser version alone is not a safe rollback. Restoring a checkpoint
also loses changes made after it, so show its timestamp and consequences before
restoration. Preserve a copy of the failed newer state for recovery.

## Acceptance before automatic replacement

- Interrupt at every download, install, migration and restart boundary; restarting
  the manager must converge to the old or new accepted pair, not a mixture.
- Test low disk space, offline mode, corrupted assets, invalid signatures,
  revoked/expired certificates, locked files and another running instance.
- Test fresh install and upgrade from the previous release on supported macOS,
  Windows and Linux architectures, including paths with spaces/non-ASCII text.
- Verify cookies, local storage, pinned identity, proxies, extensions, tags,
  templates, saved views and scheduler state after upgrade and after rollback.
- Keep signing/notarization and package-manager distribution requirements in the
  release milestone. A link to official GitHub assets is not signature verification.

## Upstream maintenance

For each wrapper update, read official release notes and the browser pairing,
review changed config keys and installer APIs, update the lockfile, then run unit,
API, UI and real-browser suites. Record the tested pair and platform in
[Camoufox compatibility](camoufox-compatibility.md). The manual compatibility
workflow allows maintainers to repeat this evidence without changing user data.
