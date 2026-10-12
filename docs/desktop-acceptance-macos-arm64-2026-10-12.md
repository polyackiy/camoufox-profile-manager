# macOS Apple Silicon trial — 2026-10-12

Recorded for [#62](https://github.com/polyackiy/camoufox-profile-manager/issues/62)
using the [desktop acceptance protocol](desktop-acceptance.md). Installation,
profile persistence, archive recovery and app-only removal/reinstallation worked
on this Mac. Closing the published app left its manager process running:
[failure #67](https://github.com/polyackiy/camoufox-profile-manager/issues/67).
This is a developer-workstation trial, **not a completed clean-system acceptance**.

## Artifact and environment

| Field | Observation |
| --- | --- |
| Public release | [v0.6.0-rc.1](https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0-rc.1) |
| Source commit | `d855c2fb62822bccad9603d1beddc33eb4c0540d` |
| Asset | `camoufox-pm-macos-arm64.dmg` |
| SHA-256 | `0fe56d93e47312f89383dd4fecf1af960e46f96ae693ab3e1bf7b7fcaa094267` — matched release sidecar |
| Host | macOS 26.6.2, build 25G83; arm64 |
| Account | Existing development account; Python, Node and a Camoufox cache already present |
| Installation | Read-only DMG mount; `ditto` copy into a separate user-writable application folder; LaunchServices launch |
| App | `com.github.polyackiy.camoufox-pm`; bundle version `0.6.0rc1`; Mach-O arm64 |
| Signing | Ad-hoc; no Developer ID Team ID; deep/strict verification passed |
| Notarization | No stapled ticket (`stapler validate` exit 65) |
| Gatekeeper | `spctl` rejected the ad-hoc app; normal quarantined first launch not tested |
| Browser used | Cached official Camoufox `152.0.4-beta.29` |

The download used the CLI and did not carry quarantine. OS security settings were
not disabled. This does **not** establish the browser-download/Finder/Gatekeeper
installation flow required by D02 and #61.

The installed executable was launched with only `/usr/bin:/bin:/usr/sbin:/sbin`
in PATH, outside the source checkout. Its native window, packaged UI and local
API worked. The browser driver Node runtime was bundled inside the app. This is
evidence that external Python/Node executables were unnecessary for these runs;
their absence from a fresh machine remains untested.

## Isolation and evidence method

Every launch used an explicit isolated `CPM_DB_PATH`. The generated key, database,
browser directories, archives and desktop log were outside the app bundle. A
loopback-only page seeded a synthetic persistent cookie and localStorage value
once; subsequent visits used a read-only observation route. No real account,
Google session, production proxy or private profile was launched.

Native UI checks covered the installed manager window, profile creation, browser
navigation/tab controls, manual backup and restore, migration login and window
close. The installed app's actual HTTP API, SQLite integrity checks, local key and
archive digests, and process/socket inspection supplemented the UI observations.
API-only checks are identified below. Private logs, keys, databases and archives
are not attached to this report. The pre-trial manifest of the existing default
desktop data directory matched after the trial. Test processes were stopped.

## Published DMG results

| ID | Result | Observation |
| --- | --- | --- |
| D01 | Pass | Exact ARM DMG digest matched the published sidecar; architecture verified. |
| D02 | Partial | Copied DMG app launched with a system-only PATH. Existing development account; CLI copy, no quarantined Finder first launch and no clean-machine runtime absence check. |
| D03 | Not tested | Existing cached browser was ready. Fresh download, interruption and retry remain untested; shared browser cache was preserved. |
| D04 | Pass | Synthetic profile created in the native UI, launched, closed and reopened. Persistent cookie/localStorage and all nine observed session/machine fields matched. |
| D05 | Pass | Native New Tab/Close Tab worked; closing the additional tab retained the original fixture tab and profile. |
| D06 | Pass | Native manual backup and Restore as new profile produced a distinct ID. Full pinned fingerprint/settings matched; original profile and archive remained. Restored browser reused the saved session. |
| D07 | Partial | API Trash/restore retained the ID; UI confirmed empty Trash after restore. Unconfirmed permanent DELETE returned 400. Wrong-name UI confirmation and successful permanent deletion were not exercised. |
| D08 | **Fail** | Native window close stopped the API, driver and launched browser, but manager/resource-tracker processes remained beyond 25 seconds. Normal reopen did not restore a usable window. Linked #67. |
| D09 | Partial | Second instance was rejected by the data lock; no second listening server, first still healthy. The rejection was confirmed through logs/processes; its native error dialog was not verified. |
| D10 | Not tested in full | Backup of a running profile was skipped correctly. Update preparation returned 409 because no newer release was offered; this does not exercise update preparation's running/unbackable-profile refusal. |
| D11 | Not tested | No newer release was available to exercise complete pre-update backup preparation including Trash. |
| D12 | Not tested as specified | No distinct later published DMG was available. The separate 0.5-to-DMG migration check below does not satisfy the later-package scenario. |
| D13 | Pass | Same published package reused both profiles, pinned identity/settings, group, paused schedule, encrypted synthetic proxy credentials, key and existing backup after reinstall. |
| D14 | Pass | Removing only the test app by moving it aside retained its separate data folder/key; reinstall from the same DMG reused those records. |

The nine compared fields were cookie, localStorage, user agent, language list,
platform, screen dimensions, CPU count, timezone and canvas SHA-256. Stable canvas
was enabled before the baseline. Reopen and archive-restore comparisons did not
reseed the fixture or regenerate the profile fingerprint.

Native Check for updates reported no newer release: current preview `0.6.0rc1`,
latest stable `0.5.0`, `available=false`. No installer update or automatic updater
was performed.

## Upgrade from existing 0.5 data

A separate synthetic database was created using the actual installed **0.5.0**
package and its old schema, not a current database with its version label changed.
It contained a warmed browser directory, pinned settings/fingerprint, encrypted
synthetic proxy credentials, a group, a paused schedule and a manager login.

Launching the published DMG against that database migrated its schema (including
`deleted_at`) with SQLite integrity intact. Native login with the existing test
account worked. Profile identity/settings, group, paused schedule, user record,
decryptable proxy credentials and the key were preserved. The migrated browser
opened the observation page with all nine baseline fields intact. A launch-only
proxy override let it reach the loopback fixture without changing its saved proxy.

This demonstrates **0.5 source/CLI data → 0.6.0rc1 DMG** migration on this host.
It does not demonstrate upgrade between two distinct published DMGs, preservation
of pre-existing Trash during that upgrade, or whole-installation rollback.

## Local helper-dispatch candidate

A separate, unpublished DMG was built from main commit
`8043ab7c60ebb5a9f8a8636236605426ae6c0c99` plus the launcher change to call
`multiprocessing.freeze_support()` before importing desktop startup. It retains
bundle version `0.6.0rc1`; it is **not another public release**.

- Asset: `camoufox-pm-macos-arm64.dmg`.
- SHA-256: `30f4a2ac2317013403f8b18dff9e0845cc0a63002784e3a6d4412059c17bab3b`.
- The real frozen executable's resource-tracker protocol probe exited 0 after
  its input pipe closed, without creating a manager database/window. This validates
  helper dispatch independently of mocked entry-point tests.
- Installed from that DMG into the isolated test app location, it retained the key
  and opened the restored browser with all nine baseline fields preserved.
- **D08 still failed.** After native window close, API/browser shutdown completed
  but the parent remained beyond 25 seconds. A native process sample placed its
  main thread in `Py_FinalizeEx → finalize_modules → gc_collect_main`. The tracker
  eventually exited, while the parent still remained several minutes after launch.
  The sample identifies where it was delayed, not the definitive root cause.

The launcher change follows [PyInstaller's multiprocessing guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing)
and resolves helper dispatch only. It must not close #67 or claim clean shutdown.
Native packaging/static UI export succeeded; local verification recorded
**547 passed, 2 skipped, 24 browser tests deselected**, Ruff check/format passed,
and mypy passed for 43 source files.

## Remaining acceptance work

#62 remains open. Apple Silicon still requires quarantined normal installation,
a fresh account/system without development runtimes or browser cache, browser
download/retry, the remaining deletion/update scenarios, a distinct later DMG
upgrade, and a passing retest of #67. Intel macOS, Windows and Linux need their
own target trials. Signing/notarization is tracked separately in #61.
