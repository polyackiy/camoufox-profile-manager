# Desktop release acceptance

Tracks [signing #61](https://github.com/polyackiy/camoufox-profile-manager/issues/61)
and [clean-system trials #62](https://github.com/polyackiy/camoufox-profile-manager/issues/62).
Use this protocol on disposable accounts or clean VM/system snapshots with
synthetic profiles. A source installation on a developer's Mac does not qualify
as a clean-machine trial. [Release checks](releasing.md) cover packaging.

## Evidence recorded on 2026-10-12

The public preview is [v0.6.0-rc.1](https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0-rc.1),
built from `d855c2fb62822bccad9603d1beddc33eb4c0540d`. All four desktop build
jobs passed in [the tagged release run](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/37676900975).
All publisher-signing and notarization steps were skipped. The repository had no
configured repository Actions secrets when inspected; this is an observation
at this date, not a requirement to disclose secret values.

The downloaded `camoufox-pm-macos-arm64.dmg` was mounted read-only and inspected:

- SHA-256 `0fe56d93e47312f89383dd4fecf1af960e46f96ae693ab3e1bf7b7fcaa094267`
  matched the published sidecar.
- App identifier: `com.github.polyackiy.camoufox-pm`; bundle version: `0.6.0rc1`.
  Main executable: Mach-O arm64.
- `codesign --verify --deep --strict` passed, but `codesign -dv --verbose=4`
  reported `Signature=adhoc` and `TeamIdentifier=not set`. This is not a
  Developer ID publisher signature.
- `xcrun stapler validate` returned 65: no stapled ticket.

The initial inspection did not launch the app. A subsequent
[Apple Silicon workstation trial](desktop-acceptance-macos-arm64-2026-10-12.md)
tested installation, profile reopening, archive restore, 0.5 data migration and
app-only removal/reinstallation. Closing the published app failed to terminate
the manager process ([#67](https://github.com/polyackiy/camoufox-profile-manager/issues/67)).
A subsequent local build with the completed fix passed three native shutdown/reopen
cycles, including Cmd+Q; the linked trial distinguishes its hash from the unchanged
public preview and records the lifecycle workaround and remaining limitations.
Normal quarantined Gatekeeper first launch, a distinct later DMG upgrade and
clean-system behavior remain unverified. The preview also predates the merged
Linux-input/Chinese-UI PR #65; testing this installer does not validate that newer code.

| Platform | Package builds | Publisher verification | Clean install/update/reinstall |
| --- | --- | --- | --- |
| macOS Apple Silicon | Passed | Ad-hoc only; no stapled ticket | [Workstation trial](desktop-acceptance-macos-arm64-2026-10-12.md); public D08 failed, local fix retested; clean-system trial pending |
| macOS Intel | Passed | Signing/notarization skipped | Not recorded |
| Windows 10/11 x64 | Passed | App/installer signing skipped | Not recorded |
| Ubuntu 22.04+ x64 | Passed | Unsigned release downloads | Not recorded |

## Trial record

For each trial record the following, leaving unknown fields explicitly unknown:

```text
Release/tag and source commit:
Exact asset name and SHA-256:
OS version/build and CPU architecture:
Fresh account/VM image and installation location:
Python/Node absent: yes/no
WebView2 initially present: yes/no/not applicable
Download method; quarantine/Gatekeeper or SmartScreen behavior:
Publisher identity, signature/timestamp and notarization result:
Scenario IDs below: pass/fail/not tested, with brief observations
Linked reproducible failure issues:
```

Record credentials, cookie values and encryption keys only in the disposable
test environment. Public evidence may include sanitized error text and test IDs;
do not upload profile archives, raw databases or keys. A blocker is a failed or
unexecuted required scenario, not something to turn into a pass by disabling OS
protections.

## Test fixture

Create two synthetic profiles: one kept active and one moved to Trash. Use a
local test site with a harmless persistent cookie and localStorage value. Record
the site-visible user agent, screen, CPU count, timezone and stable canvas hash
before and after reopening. Enable `stable_canvas` for the canvas persistence
comparison; the default per-session canvas randomization is not a failed upgrade.
Keep the same profile identity; do not regenerate it
to make the comparison pass. Use a controlled test proxy to exercise encrypted
credentials, and create a group, a paused schedule and a test manager account.
These records distinguish preserved whole-installation data from a restored
profile archive, which intentionally does not restore all manager data.

Check the current configuration for storage paths. Preserve the whole data
directory and its key before update tests. With the app and browsers stopped,
compare the key digest locally; do not include the key or digest in public logs.
Default storage locations are in [getting started](getting-started.md#where-data-lives).

## Scenarios required for each supported target

| ID | Action | Required observation |
| --- | --- | --- |
| D01 | Download the exact release package and checksum | Digest matches; asset/architecture identified |
| D02 | Install through the normal OS flow without Python/Node | App launcher works; record publisher/security prompts |
| D03 | First launch, interrupt browser download, retry | Clear error and successful retry; no partial browser treated as ready |
| D04 | Create, launch, close and reopen a synthetic profile | Cookies/storage and pinned machine persist |
| D05 | Create/close native browser tabs | Normal tab controls work without changing profile identity |
| D06 | Back up a closed profile, restore as new profile | New ID; source data and backup preserved; restored session usable |
| D07 | Restore from Trash, then test permanent deletion | Restore works; wrong-name confirmation cannot delete |
| D08 | Close the manager with a profile running | Manager server and its launched browser terminate |
| D09 | Start a second manager on the same data directory | Second instance rejected; first remains usable |
| D10 | Attempt update preparation with a running/unbackable profile | Preparation refuses, explains the failure; no download offered as ready |
| D11 | Prepare update with all profiles closed, including Trash | All required profile archives present and readable |
| D12 | Install a later compatible package over the stopped app | Database, groups, users, schedules, Trash, profiles and key preserved |
| D13 | Reinstall the same package | Data/key unchanged; existing identity and proxy credentials readable |
| D14 | Uninstall/remove only the app, then reinstall | Separate data folder and key survive; app reuses them |

D12 requires a distinct later package: reinstalling the same version proves
D13, not upgrade compatibility. Restoring a profile archive as a new profile
proves D06, not whole-database rollback. The proposed automatic updater has
additional interruption/migration tests in [its design](automatic-updates.md).

## Platform additions

### macOS ARM and Intel

Download in a normal browser and preserve the OS quarantine marking. Install
the DMG app using its Applications shortcut, launch through Finder and repeat
D12–D14. Test each architecture separately. For signed candidates, verify the
installed app and downloaded installer with the commands in [signing](signing.md),
inspect the Developer ID Team ID, and record Gatekeeper's normal first-launch
behavior. Passing ad-hoc `codesign` verification does not satisfy #61.

### Windows 10 and Windows 11 x64

Run as an ordinary user, in separate clean images with WebView2 absent and
already installed. Verify runtime installation/retry, Start menu launch,
installer cancellation, app update and uninstaller behavior. Check both app and
installer Authenticode publisher/timestamp; use `signtool verify /pa /all /v`
or OS trust inspection. Record normal SmartScreen behavior separately from
signature validity. An administrator-only successful installation does not
satisfy the ordinary-user case.

### Ubuntu 22.04+ x64

Install the DEB through the package manager with dependency resolution, then
launch from the Applications menu. Separately extract the portable archive into
a user-writable path and record needed system graphics libraries. Test the
supported minimum Ubuntu version and any additional OS version claimed in the
release. Test native input through an installed IME on an actual desktop;
synthetic composition events do not exercise Fcitx. Apply normal package-manager
upgrade/uninstall/reinstall and verify that user data survives removal of the
package's `/opt/camoufox-pm` installation directory.

## Stable release decision

#61 remains open until downloaded macOS ARM/Intel packages pass Developer ID,
notarization and normal Gatekeeper checks, Windows app/installer publisher and
timestamp checks pass, and beginner guides describe those verified flows.

#62 remains open until the supported platform/runtime combinations have recorded
results for required scenarios, with failures fixed and retested. Automated
source/UI tests and successful packaging are useful evidence but do not replace
these trials. Any intentionally unsupported combination must be removed from
the stated support matrix before promotion. #63 is later design/implementation
work and is not a prerequisite for the current manual-update desktop preview.
