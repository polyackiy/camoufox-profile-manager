# Accessibility roadmap: installation without a developer toolchain

Stage 1 adds the desktop path: download an installer, open the manager, install
the browser with visible progress, and create a profile. The [beginner guide](getting-started.md)
and [Russian guide](getting-started.ru.md) are the default entry points.

## Implemented in stage 1

- A dedicated desktop entry point opens a native window without `--desktop`.
  The existing `camoufox-pm` CLI remains available for source/web deployments.
- A writable OS data folder keeps the database, browser profiles and encryption
  key outside the application bundle. A fresh desktop install generates a key;
  an existing database with a missing key is not silently assigned a new one.
- A private loopback server selects an available port. The launcher owns its
  listening socket, rejects a second instance using the same data folder, and
  shuts down the server and browser sessions when its window closes.
- A dismissible setup banner installs the Camoufox browser in a background job
  with visible status, progress, errors and retry; Settings offers **Retry download** for missing browser resources.
- Empty states guide users to create their first profile. Fingerprint regeneration
  and permanent deletion explain their consequences and ask for confirmation.
- Profiles move to Trash before permanent deletion. Backups and restoration are
  available in Settings; restoration creates a separate profile.
- Update checks prepare a backup before linking to the official release downloads.
  Users still download and install the package themselves.
- Tag releases build macOS Apple Silicon/Intel DMGs, Windows x64 NSIS installers
  and Linux x64 Debian/portable packages automatically. Manual builds remain
  available. Signing runs conditionally when maintainers provide credentials.

The web UI is bundled with the backend. Neither Python, Node.js, two separate
servers, `.env` edits nor a `camoufox fetch` command is needed for desktop users.

## Validation boundaries

Build automation is an implementation, not proof that every installer runs on
real hardware. See [release checks](releasing.md) for the target-system matrix.
Signing/notarization could not be exercised without certificates. Unsigned builds
can be blocked by OS policy and cannot yet promise a warning-free install.

Linux currently targets Ubuntu 22.04+ and compatible Debian x64. The Qt webview
is bundled, while system graphics libraries remain dependencies. Other Linux
distributions, AppImage, Windows ARM and Linux ARM are not currently packaged.

## Next work

1. Verify fresh install, update, reinstall, backup restore and browser launch on
   real macOS Intel/ARM, Windows and Linux machines, including downloaded/quarantined
   artifacts. Capture current beginner screenshots after that verification.
2. Configure and verify signing/notarization. Publish actual signing status and
   supported OS versions with release assets.
3. Add a verified installer download and a full app replacement/rollback mechanism
   only after backup compatibility and signature verification are established.
4. Translate the interface; documentation is English/Russian but UI controls remain
   English. Add keyboard and screen-reader audits for dialogs and progress states.
5. Add broader Linux distribution packaging once the supported package has been
   exercised on real desktop systems.

The Docker and CLI paths remain useful for advanced deployments; desktop users
should start with the download guide.
