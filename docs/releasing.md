# Releasing

A `v*` tag triggers `.github/workflows/release.yml`. It builds the web UI into
Python wheel/sdist packages and calls the reusable `desktop.yml` workflow with
the tag. Desktop jobs build native macOS ARM/Intel, Windows x64 and Linux x64
installers and attach them to the same draft GitHub Release. A maintainer
publishes it only after all jobs and the asset/checksum audit pass. No manual second workflow
is needed. The Actions **Desktop builds** manual dispatch remains available; an
optional `release_tag` checks out that tag and attaches its installers.

## Release policy

Preview and stable releases use unsigned installers. The project will not obtain
platform code-signing certificates; publisher signing and macOS notarization are
not release requirements. [#61 is closed as not planned](https://github.com/polyackiy/camoufox-profile-manager/issues/61)
by the maintainer's 21 October 2026 decision. See [expected signing status](signing.md)
for package labels, integrity checks and normal OS approval prompts.

## Artifacts

| Platform | Release asset | Builder |
| --- | --- | --- |
| macOS Apple Silicon | `camoufox-pm-macos-arm64.dmg` | `macos-15` ARM runner |
| macOS Intel | `camoufox-pm-macos-x86_64.dmg` | `macos-15-intel` runner |
| Windows x64 | `camoufox-pm-windows-x86_64-setup.exe` | Windows 2022 + NSIS |
| Linux x64 | `camoufox-pm-linux-x86_64.deb` and `.tar.gz` | Ubuntu 22.04 + bundled Qt webview |

Each desktop asset has a same-name `.sha256` sidecar, computed after final
packaging. Checksums detect file corruption; checksums downloaded from the
same release do not independently establish the publisher's identity.

macOS DMGs include an Applications shortcut. Windows installs per user without
administrator rights and adds Start menu and uninstall entries. Packaging downloads
the official Microsoft WebView2 bootstrapper and verifies its Authenticode
publisher before inclusion; NSIS installs that runtime if missing (requires
internet). See [Microsoft’s deployment documentation](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution). Uninstalling
keeps the separate user data folder and unknown files inside the installation
folder. The Windows packaging job tests exact-file removal, reinstall and
redirected-path refusal before uploading installers. Linux `.deb` installs the bundle in
`/opt/camoufox-pm`, a launcher in `/usr/bin`, and an Applications entry; the
tarball keeps the complete runnable bundle for other layouts. Its runtime still
needs compatible system graphics libraries. AppImage, Linux ARM, Windows ARM,
and full automatic app replacement are not shipped.

Linux uses the host's `libstdc++.so.6` and `libX11.so.6` instead of collecting
copies from the build runner. Newer Mesa drivers can require newer C++ ABI
symbols and matching X11 behavior; shadowing these with Ubuntu 22.04 libraries
breaks GLX on Ubuntu 24.04. The Debian package declares `libstdc++6 (>= 12)` and
`libx11-6`; portable users must install these system prerequisites too. Test a
real Qt window with the target system's graphics driver: `ldd` alone cannot
validate dynamically loaded Mesa components.
The ALSA prerequisite uses `libasound2t64 | libasound2 (>= 1.2.6)` so a minimal
Ubuntu 24.04 installation cannot choose the unversioned OSS compatibility
provider, which lacks sequencer symbols required by Firefox. The clean-package
test checks the installed library for the required sequencer API as well.

## Before tagging

1. Update `CHANGELOG.md`, `pyproject.toml` and `web/package.json` consistently,
   including `web/package-lock.json` when the npm package version changes. Prereleases use PEP 440 `0.6.0rc1`
   in Python and SemVer `0.6.0-rc.1` in npm and the Git tag; Debian packages
   use `0.6.0~rc1` so the final version sorts after its preview.
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
5. Record the expected unsigned status and normal Gatekeeper/SmartScreen behavior
   of downloaded packages. No publisher certificate, Authenticode signature,
   Developer ID or notarization ticket is required. Preserve OS protections and
   distinguish checksum integrity from publisher verification; see [the policy](signing.md).
6. Run **Installed desktop proxy acceptance** on the candidate revision and
   require all four target reports to pass. Keep the package SHA-256, source
   revision and wrapper/browser pair with the acceptance record; see
   [scope and reproducible protocol trials](proxy-support.md#installed-package-acceptance).
   These disposable package trials do not replace the physical-system checks above.
7. Only after release approval, create and push an annotated `v<version>` tag.
   Watch **Release** and all four **Desktop builds** jobs finish. Check every
   expected asset is present and its checksum matches before publishing the draft.
   Mark `-rc` tags as prereleases and leave the previous stable release as Latest.
   Jobs upload independently, so a failed build leaves the draft unpublished.
   The release candidate is a public testing preview: unresolved stable acceptance
   work must be named in its release notes and tracked in the release milestone.

No tag or release upload is performed by the build script itself.

### Recorded 0.6.0 acceptance

The maintainer approved publishing 0.6.0 as a stable, unsigned release on
19 October 2026. The release policy above applies to subsequent releases too;
certificate acquisition and notarization are not planned work.

Issue #62 is closed by maintainer acceptance of the recorded Windows/Linux
evidence. Its original unchecked platform entries and the
[qualified follow-up](desktop-acceptance-followup-2026-10-19.md) remain the coverage
record; closure does not establish untested macOS or Windows 11 runtime-absence
combinations. Synchronization remains experimental and opt-in. Production trust
and signed package acceptance still gate automatic application installation.

## Local unsigned build

```bash
uv sync --extra build
uv run python scripts/build_desktop.py --package
uv run python scripts/build_desktop.py --checksums-only
```

`--package` combines a fresh UI/PyInstaller build and packaging for unsigned local
checks. The browser binary is intentionally absent; first-run installation uses
the in-process Camoufox installer so it works in frozen applications too.

Native app icons are checked in under `packaging/`: the shared SVG, a 1024-pixel
PNG, a multi-resolution ICNS for macOS and ICO for Windows. To regenerate the
raster formats after editing the SVG, run `npm ci --prefix web` followed by
`node scripts/build_icons.mjs` (using Next's installed sharp renderer). PyInstaller
embeds the ICNS/ICO, NSIS uses the ICO for both installer and uninstaller, and
Linux uses the SVG for its desktop launcher and GTK/Qt window. Regenerate these
assets before packaging and compute checksums from the final files.

## Update boundaries

Settings checks only the configured project's GitHub releases. Unsigned preview
and stable releases prepare a backup and offer the project release URL; users
close the manager and install manually. Keep complete pre-upgrade checkpoints and matching
installers: older managers must not open a newer database schema.

The integrated updater includes verified target preparation, complete checkpoints,
offline replacement, migration, validation and recovery. Automatic installation
remains disabled. Its existing production TUF, publisher-policy and signed-package
acceptance requirements belong to automatic replacement, not manual release
publication. The unsigned release policy does not relax these checks or enable
that path; any future enablement needs a separate reviewed trust design. It does
not bypass OS security prompts. See [the current recovery implementation](update-recovery.md).

## Publishing to PyPI (optional)

PyPI publishing is off by default. Set repository variable `PUBLISH_TO_PYPI=true`
and configure a PyPI Trusted Publisher for owner `polyackiy`, repository
`camoufox-profile-manager`, workflow `release.yml`, environment `pypi`. Create
that GitHub environment before enabling publishing. The release workflow uses
OIDC rather than a PyPI API token. Desktop installation does not require PyPI.

## Current public tracking

- [Published stable 0.6.0](https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0)
- [Signing and notarization #61](https://github.com/polyackiy/camoufox-profile-manager/issues/61) — closed as not planned; releases remain unsigned
- [Clean-system acceptance #62](https://github.com/polyackiy/camoufox-profile-manager/issues/62) — closed by maintainer acceptance; recorded coverage limits remain
- [Verified automatic updates #63](https://github.com/polyackiy/camoufox-profile-manager/issues/63) (production trust and signed acceptance remain open)

Use the [desktop acceptance protocol and recorded evidence](desktop-acceptance.md)
for installation checks. The [automatic updater design](automatic-updates.md) records the architecture;
[update-recovery.md](update-recovery.md) distinguishes implemented recovery from
the production trust and publisher checks that remain specific to automatic replacement.

Prerelease tags are excluded from the optional PyPI publish job. The application's
stable update checker also intentionally excludes prereleases; preview testers
download them explicitly from GitHub.
