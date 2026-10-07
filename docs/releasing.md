# Releasing

A `v*` tag triggers `.github/workflows/release.yml`. It builds the web UI into
Python wheel/sdist packages and calls the reusable `desktop.yml` workflow with
the tag. Desktop jobs build native macOS ARM/Intel, Windows x64 and Linux x64
installers and attach them to the same GitHub Release. No manual second workflow
is needed. The Actions **Desktop builds** manual dispatch remains available; an
optional `release_tag` checks out that tag and attaches its installers.

## Artifacts

| Platform | Release asset | Builder |
| --- | --- | --- |
| macOS Apple Silicon | `camoufox-pm-macos-arm64.dmg` | `macos-15` ARM runner |
| macOS Intel | `camoufox-pm-macos-x86_64.dmg` | `macos-15-intel` runner |
| Windows x64 | `camoufox-pm-windows-x86_64-setup.exe` | Windows 2022 + NSIS |
| Linux x64 | `camoufox-pm-linux-x86_64.deb` and `.tar.gz` | Ubuntu 22.04 + bundled Qt webview |

Each desktop asset has a same-name `.sha256` sidecar, computed after signing and
notarization. Checksums detect file corruption; checksums downloaded from the
same release do not independently establish the publisher's identity.

macOS DMGs include an Applications shortcut. Windows installs per user without
administrator rights and adds Start menu and uninstall entries. Packaging downloads
the official Microsoft WebView2 bootstrapper and verifies its Authenticode
publisher before inclusion; NSIS installs that runtime if missing (requires
internet). See [Microsoft’s deployment documentation](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution). Uninstalling
keeps the separate user data folder. Linux `.deb` installs the bundle in
`/opt/camoufox-pm`, a launcher in `/usr/bin`, and an Applications entry; the
tarball keeps the complete runnable bundle for other layouts. Its runtime still
needs compatible system graphics libraries. AppImage, Linux ARM, Windows ARM,
and full automatic app replacement are not shipped.

## Before tagging

1. Update `CHANGELOG.md`, `pyproject.toml` and `web/package.json` consistently,
   including `web/package-lock.json` when the npm package version changes.
2. Run backend tests and frontend lint/static build. In a clean build environment
   run `uv sync --extra build` and `uv run python scripts/build_desktop.py --package`.
   Build on each target platform; PyInstaller is not a cross compiler. Linux also
   needs `PyQt6`, `PyQt6-WebEngine`, `qtpy`, `dpkg-deb` and the shared libraries
   declared in the generated Debian control file. Windows needs NSIS (`makensis`).
3. Install the wheel in a clean environment, run `camoufox-pm --no-browser`,
   and confirm `/health` reports the release version. The frozen build also copies
   package metadata so its version is available to update checks.
4. Download workflow artifacts and install them on real target systems. Verify
   first launch without Python/Node, browser installation progress/retry, create
   and run a profile, restart from a different working directory, backups and
   restore, Trash, closing running browsers with the manager, and reinstalling
   without data loss. Confirm a second launch gives a clear already-open message.
5. For signed builds verify signatures and macOS notarization on downloaded
   quarantined artifacts. No certificates are present in this development
   environment; conditional signing steps need real credentials and validation.
6. Only after release approval, create and push an annotated `v<version>` tag.
   Watch **Release** and all four **Desktop builds** jobs finish. Check every
   expected asset is present before announcing the release. Jobs upload assets
   independently; a partially failed build can leave an incomplete release.

No tag or release upload is performed by the build script itself.

## Local build and signing order

```bash
uv sync --extra build
uv run python scripts/build_desktop.py
# Apply platform signing to the app/binary when credentials are available.
uv run python scripts/build_desktop.py --package-only
# Notarize/staple the DMG or sign the Windows installer when configured.
uv run python scripts/build_desktop.py --checksums-only
```

`--package` combines a fresh UI/PyInstaller build and packaging for unsigned local
checks. The browser binary is intentionally absent; first-run installation uses
the in-process Camoufox installer so it works in frozen applications too.

## Update boundaries

Settings checks only the configured project's GitHub releases. Update preparation
creates a backup first, then offers the project release URL for a supported
installer. Users download it, close the manager and install it. The app does not
cryptographically verify installer signatures, swap its running executable,
guarantee rollback of app binaries, or bypass OS
security prompts. Keep archives and `secret.key` outside the installation folder.

## Publishing to PyPI (optional)

PyPI publishing is off by default. Set repository variable `PUBLISH_TO_PYPI=true`
and configure a PyPI Trusted Publisher for owner `polyackiy`, repository
`camoufox-profile-manager`, workflow `release.yml`, environment `pypi`. Create
that GitHub environment before enabling publishing. The release workflow uses
OIDC rather than a PyPI API token. Desktop installation does not require PyPI.
