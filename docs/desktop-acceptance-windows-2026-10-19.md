# Windows acceptance follow-up — 19 October 2026

The [later same-day follow-up](desktop-acceptance-followup-2026-10-19.md)
adds current Windows 11 native download error/retry and Windows 10 confirmed
disposable deletion, with protection, runtime-absence and cleanup receipts.

These results extend the [initial Windows 11 trial](desktop-acceptance-windows11-2026-10-18.md).
They concern unsigned previews. They do not close the full platform matrix in
[#62](https://github.com/polyackiy/camoufox-profile-manager/issues/62), publisher
signing in #61, production updater trust in #63, or independent human review.

This is a dated evidence record. The maintainer subsequently closed #62 by
acceptance and #61 as not planned; see [the current unsigned release policy](signing.md).
Those decisions do not turn untested configurations into passes or change the
historical failures below.

## Candidate identity and protected environments

The initial corrected Windows installer was built from
`f1f53af0fba64c2f5a6ad283245e97da5346cc9d` in the build-only
[workflow 39134541718](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/39134541718).
Asset: `camoufox-pm-windows-x86_64-setup.exe`, 86,337,357 bytes,
SHA-256 `533b0df594abadd0e16c0e22f5710c3af53af591f62454a2cf8f533ddbf425f5`.
It is a PR artifact, not a replacement for the already published rc.8 asset.
The installer and installed application have no publisher signature or timestamp.

A subsequent distinct-version trial found stale version discovery after an
in-place upgrade. The fix is committed as
`9425f08072dc75ad0334518e724e5737b23b41fb`; its build-only
[workflow 39259076622](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/39259076622)
completed successfully on all four build platforms. Its Windows setup is
86,336,446 bytes, SHA-256
`333a6c32c3ea21e727419b3bc33da42cc257ff3243ef61d7cdad7672b156433e`.
The artifact ZIP digest is
`7b7f0f16aaa73fd8c0cb0094084ee8d99aab27d19b867dbafa0486ff32078dfb`.
The actual Windows checkout SHA and absence of release attachment were verified.
Both guests received these exact bytes with Internet provenance and a new native
Defender scan. Both scan processes reported exact-path no-threat results and exit
0 with protection enabled; Windows 11 supplied correlated completion events,
while Windows 10 retained the old-platform event-correlation qualification.

Both guests use official Microsoft Evaluation media, separate ordinary `tester`
accounts, no separately installed Python/Node, Secure Boot, TPM and UAC.
Defender real-time, behavior and IOAV protection remain enabled, without added
exclusions. Windows trust decisions are handled by the human. Guest networks are
isolated from the host/LAN; only the dedicated controller and DNS are permitted
exceptions. Test profile secrets, databases, keys and archives stay private.

| Guest | Recorded OS and runtime | Defender evidence |
| --- | --- | --- |
| Windows 11 x64 | Enterprise Evaluation 26H2, 26300.9457; WebView2 155.0.4283.62 already installed | Platform 4.18.26090.9, Normal mode; custom scan and empty threat history |
| Windows 10 x64 | Enterprise LTSC 2021 Evaluation 21H2, 19044.1288; WebView2 155.0.4283.62 present at the original setup | Platform 4.18.1909.6, updated engine 1.1.26090.9 and definitions 1.461.91.0; signed Microsoft scan tool reported exact-path scan finished/no threats, exit 0; empty threat history |

The old Windows 10 platform did not expose `AMRunningMode`; Normal mode is not
inferred from null. Its initial strict event-correlated scan report remains a
failure: a separate native scan result supplies the qualified evidence above.

## Windows 11 corrected candidate

The manual local checker completed its installation/API/second-instance/shutdown
chain with exit 0. This was a verified continuation of the same guest, not a new
uninterrupted run. Its original manual-step timeout remains a failure. A previous
exit-0 report lacking positive shutdown evidence was invalidated. The accepted
chain checks exact source/package identity, protection state, the eight limited
API/browser cases, D09, and a positive QMP event with `guest=true` and
`reason=guest-shutdown`.

The fuller fixture also includes an authenticated controlled proxy, a persistent
cookie and localStorage token, site-visible machine observations, a group, a
paused schedule, active profiles, Trash, an administrator and a viewer with a
profile grant. Observation-only requests do not seed replacement session values.

| Scenario | Result and limit |
| --- | --- |
| D01 | Exact candidate size/hash and Internet provenance verified. |
| D02 | Ordinary-user per-user setup, registration, Start menu and bundled UI work with an existing runtime. The separate Windows 10 missing-runtime cancellation/Retry trial is described below; no Windows 11 runtime-absence result is inferred. |
| D03 | The earlier Windows 11 trial interrupted download by closing the manager; idle/uninstalled was reported afterward. Full native network-failure presentation is separately established on Windows 10 below. |
| D04 | Cookie/localStorage and site-visible machine fields persist. The initial canvas differs from the next capture by six pixels; a subsequent reopen is equal. Canvas acceptance remains qualified. |
| D05 | Native New Tab and Close Tab work, preserving the original tab and session; the same canvas qualification applies. |
| D06 | Every closed-profile archive stream was read. Restore received a new ID, retained the source/archive, decrypted the proxy credentials and reopened the actual saved session. |
| D07 | Trash restore retained the same profile ID/session. Wrong-name native confirmation kept Delete disabled; Enter did not delete. The test profile was retained in Trash. |
| D08 | Native manager close with a running browser stopped its API and all 17 recorded owned processes, checked by PID and creation identity. |
| D09 | The rebuilt package shows the intended already-open message. Dismissing it ends the second process; the first API remains healthy. This retests the defect from the initial published-rc.8 trial. |
| D10–D11 | Additional rc.7 trial passed the refusal and complete-backup cases described below, including native error/success presentation. |
| D12 | rc.7 to the initial PR artifact preserved data/session but failed the target-version gate because older metadata remained. The rebuilt fix passed a Windows 11 follow-up repair with the original fixture; the separate Windows 10 rc.7-to-fixed-rc.8 upgrade passed below. The original failure is retained. |
| D13 | Same exact installer retained stopped database/key bytes and all manager collections, both logins, grants and readable proxy credentials. Cookie/storage/machine persisted; cold-reboot canvas mismatch remains a failed comparison. |
| D14 | Normal app removal removed registration/app and retained separate database/key bytes. Reinstallation preserved them and the complete manager fixture. Actual cookie/storage/machine/canvas observations matched afterward. |

No renderer preference, profile identity or warm-up workaround was introduced
to make canvas comparisons pass. A fresh physical Windows cold-host trial in
[#73](https://github.com/polyackiy/camoufox-profile-manager/issues/73) is still
required; VM observations and an already used physical host do not replace it.

## Additional rc.7 update-preparation trial

The distinct-version fixture starts with the published rc.7 Windows installer,
86,319,164 bytes, SHA-256
`6b90b2c7c692e3586bc5f6c4b709fcaa7058b2891e915989505d941735b0b8bc`.
The actual preview check offers `0.6.0-rc.8` to `0.6.0rc7`. The later package for
D12 is the exact PR #111 artifact identified above.

D10 refused preparation with HTTP 409 while a real profile browser was running.
It also refused a closed profile whose synthetic storage directory had been
temporarily retained under a sibling name. Both refusals identified the affected
profile and offered no download URL; the directory was restored in `finally`.
The native Settings view separately showed the running-profile error and no
release-download button.

After closing all browsers, D11 created five new `before_update` archives for
four active profiles and one Trash profile. Every ZIP entry stream was read.
The native Settings flow independently repeated preparation, reported five
backups and exposed the release-download button; its five new archives were
also read in full. The API returned the official GitHub rc.8 release URL.

Cookie/localStorage and machine observations survived tabs/reopening in this
rc.7 fixture. Its initial canvas comparisons differed, so the strict comparison
failure remains retained. The subsequent backup/new-ID restore and Trash restore
observations matched their recorded session/machine/canvas references. One
early Trash check ran before a capture file existed because the wrong window
had focus; that failed tool report remains separate from the later observation.

Before D12, the fixture was extended with its group, disabled schedule, existing
administrator/viewer and viewer profile grant. All browsers and the native
manager were closed, and a private stopped data-folder copy plus database/key
hashes were verified. The later installer passed an exact-path native Defender
scan with correlated completion events and no threats. Native installation with
Run unchecked preserved the stopped database/key bytes. All six manager
collections, both existing logins, the viewer grant and decrypted proxy
credentials were equal/readable afterward. Observation-only browser requests
also retained cookie/localStorage, machine fields and canvas.

The trial nevertheless failed: the new executable reported `0.6.0rc7` through
health. The installed payload contained both rc.7 and rc.8 distribution metadata;
`importlib.metadata.version` selected the older one. The follow-up fix embeds the
build-selected version in a compiled desktop runtime hook, honored only by a
frozen process. It leaves ordinary installed-package discovery and unknown-source
fallback intact, and does not remove unknown installation files. A subprocess
regression first reproduced rc.7 instead of rc.8, then passed after the fix.
Five focused tests, native Windows competing-process locking and scoped Ruff
checks passed; independent source review found no actionable findings. The
rebuilt installer was then tested in the same Windows 11 fixture. A new stopped
database/key comparison passed, health reported `0.6.0rc8`, all six manager
collections and both logins/grants/proxy credentials matched, and an
observation-only browser request retained cookie/storage/machine/canvas against
the original rc.7 reference. This is a follow-up repair of the already attempted
upgrade; the fresh distinct-version trial is the Windows 10 case below.

## Windows 10 original candidate trial

Normal per-user installation, native manager and browser launch succeeded.
A bounded HTTP/HTTPS outage applied only to this guest's QEMU UID interrupted
the real Camoufox download. The native UI showed an error and Retry, with the
partial browser not reported ready. After 90 seconds the rule was removed,
the original isolation policy was checked unchanged, and native Retry installed
Camoufox 156.0.1-beta.36.

Eight limited API/browser cases then passed: frozen health, bundled UI, browser
installation, synthetic profile creation, actual browser relaunch with pinned
metadata, persistent cookie, backup/new-ID restore, and Trash restore/refusal of
unconfirmed permanent deletion. This does not establish full native D04–D14.

The historical layer name `win10-clean-webview-absent` is misleading. WebView2
appeared after the first baseline observation but before that layer was sealed
and before the manager installer started. The original trial receives **no
missing-runtime credit**. A fresh offline image and immediate machine/user
32/64-bit runtime inventories are required for that case.

## Fresh missing-runtime baseline

A new Windows 10 guest was installed with HTTP/HTTPS/QUIC blocked for its QEMU
UID before its first boot. An ordinary-user inventory confirmed the WebView2
client key absent in both registry views of both HKLM and HKCU, and no runtime
executable in the machine/user installation paths. A signed Microsoft offline
security-intelligence package updated Defender to definitions 1.461.104.0 and
engine 1.1.26090.9 without opening guest Internet access or changing protection.

Runtime absence, protection and the ordinary account were checked again before
normal shutdown. The disk was sealed as a reusable base with matching firmware
and TPM, and subsequent work uses a small writable overlay. The exact PR #111
installer was transferred privately and scanned with no threats. An immediate
pre-setup four-view inventory still found no runtime. After the human's
SmartScreen decision, native Install invoked the bundled Microsoft bootstrapper
and displayed the real missing-runtime Retry/Cancel error while Internet access
remained blocked. Cancel followed by closing the aborted wizard produced exit 4.
The manager's files, registration, shortcut, database and key remained absent.
A separate welcome-page Cancel/confirmation also left that clean state intact.

An intervening offline attempt was interrupted by the Windows Evaluation
licensing service, not credited as a completed Retry. WLMS event 101 and User32
1074 identify `wlms.exe` initiating shutdown after an hour; EventLog 6006 records
the stop. The reported Evaluation status was 5 with zero grace minutes. The
preserved disk passed an offline image check and was restarted without restore.
No positive QMP shutdown receipt was collected for that interruption. A later
ordinary activation request for the existing evaluation product did not activate
it; long repeat runs still require a usable OS evaluation/license.

After reboot, another immediate four-view inventory confirmed absence. A new
native Retry was observed while the deliberately introduced Internet fault was
removed. The original LAN isolation table was checked unchanged. A passive
process observer captured a freshly started Microsoft-signed temporary
bootstrapper as a descendant of the exact PR #111 setup. Setup finished with
exit 0 and protection enabled. The post-install inventory found WebView2
155.0.4283.62 in HKCU and its runtime executable. This supplies installer
attribution beyond log timestamps or an earlier baseline snapshot.

Finish had Run unchecked. The complete per-user app/shortcut/registration were
verified with no manager process, database or key. Normal app-only removal then
removed those installation files. A separately downloaded/scanned rc.7 is now
used for a fresh Windows 10 fixture/upgrade trial; no existing database was
downgraded.

The rc.7 trial passed its eight limited API/browser checks. Native new-tab,
close-tab and browser-close/reopen observations retained cookie/localStorage,
machine fields and canvas. Every closed-profile archive stream was read;
new-ID restore and same-ID Trash restore reopened the actual session with equal
machine/canvas observations. The native permanent-delete dialog kept its button
disabled after a wrong name and Enter; cancellation retained the Trash profile.
D10 separately refused running and unavailable-storage profiles. D11 created
five readable archives including Trash through both API and native Settings.

The complete upgrade fixture contained four active profiles, one Trash profile,
a group, a disabled schedule, administrator/viewer accounts and a viewer grant.
Native rc.7-to-fixed-rc.8 installation finished with Run unchecked and preserved
stopped database/key bytes. The Evaluation OS then shut down before the first
new-manager launch. A later requested normal-shutdown observation found the VM
already offline and is retained as failed; no positive QMP receipt is claimed
for that stop. The same disk passed an offline image check and restarted without
restoring or changing licensing/security settings. Subsequent checks reported
`0.6.0rc8`, equal complete manager collections, both existing logins/grant and
readable proxy credentials. The controlled fixture server was restarted, and
observation-only browser requests retained the original cookie/storage/machine/
canvas. This records upgrade preservation across an OS-stop continuation, not
an uninterrupted one-command trial.

## Rebuilt-candidate regression trials on both Windows versions

The exact `9425f08` setup identified above was tested again on both guests after
the upgrade observations. D09 displayed the native already-open message; its
second process exited with code 1 after dismissal while the first API remained
healthy. D08 closed the native manager with an active browser and verified that
the API and every recorded owned process stopped, matching PID and creation time.

D13 ran the same installer again with the application stopped and Run unchecked.
Stopped database/key bytes remained equal. Health reported `0.6.0rc8`; profiles,
Trash, groups, schedules, users and grants matched, both existing accounts could
log in, and encrypted proxy credentials remained readable. The actual reopened
browser retained cookie/localStorage, site-visible machine fields and canvas
against its original rc.7 reference on both guests. These successful same-boot
comparisons do not invalidate the earlier canvas failures or supply a physical
cold-host result.

D14 used the native app-only uninstaller. The executable, uninstaller,
registration and both Start menu shortcuts disappeared, while stopped
database/key bytes remained equal. The same setup restored installation and
registration without changing those bytes. Both guests then passed the complete
manager fixture and existing-login checks with health `0.6.0rc8`. Observation-only
browser visits retained the original cookie/localStorage, machine fields and
canvas; the real browsers closed successfully afterward. No replacement session
values, renderer preferences or warm-up workaround were used.

One Windows 11 snapshot request correctly refused to run while the manager was
still open after an Explorer-focus mistake. The later snapshot followed explicit
native manager closure; the failed operational report remains retained.

WebView2 is [preinstalled as part of Windows 11](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version).
A missing-runtime Windows 11 variant must therefore be prepared and verified
separately; a fresh ordinary Windows 11 image is not evidence of absence.

After the original limited Windows 10 results were exported, its completed
synthetic fixture and obsolete runtime-present backing layer were removed.
An audit found no live processes, open files or external disk dependencies on
either directory. The current guest and its verified runtime-absent
base remain available for subsequent acceptance trials.

## Local repeatability and evidence

Separate installed-sync CI on `9425f08` retained two macOS Intel failures: a
native-vault Trash-state visibility assertion, then an unchanged diagnostic
rerun rejected the earlier encrypted-file owner handoff with HTTP 409. These
are distinct observations; a common cause is not established. Harness-only
commit `2a6ad32` adds exact-message allowlisted failure categories and lifecycle
POST status capture. It does not change the application, replay mutations, relax
assertions or increase timeouts. Twenty focused tests and scoped lint/format
checks passed. All 19 checks on `2a6ad3288b974d8aef02d56a1194c30d384f2e71`
subsequently passed, including installed-sync acceptance on all four CI targets
in [workflow 39268574008](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/39268574008).
Neither earlier failure is converted into a pass, and the common-cause question
remains unresolved; the diagnostic commit is not a demonstrated product fix.

The local `Test-Release.ps1` entry point is manual; no scheduled task was added.
The earlier accepted continuation used `f1f53af`. A fresh canonical `9425f08`
run passed baseline, Defender update and exact-package scan, but its ten-minute
human-install wait expired before the operator responded. The failure is
retained. PR #111 had advanced to the harness-only `2a6ad32`, so strict source/
package checks precluded resuming that older report as current-head acceptance.
The fresh human wait is now 60 minutes, with 60 seconds of job-execution margin;
the resumed-install probe and application/CI assertion timeouts remain unchanged.
A delayed-human regression failed first, then 23 runner/checkpoint tests and
mocked PowerShell 7 guide checks passed; independent review found no blocking
issue. The fuller scenario trials above remain separate evidence.

The current `2a6ad32` build-only
[workflow 39269728704](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/39269728704)
passed all four build platforms. Its Windows setup is 86,336,017 bytes, SHA-256
`58077a2738aaf45f19e7952bbb1df58054729ea137a22ce71205ac9c421844c2`;
artifact ZIP SHA-256
`96b72e73edc52abe7e16a5311c4818f0a84d26c39f053a4fde1649861703c8fa`.
The resolver verified the actual Windows checkout and skipped release-attachment
steps. The initial command for these exact bytes retained its expired 60-minute
operator wait. After the human completed the installer, the strict continuation
verified the same guest boot, source, package and private job evidence, repeated
the protected baseline/update/scan, and observed installation and launch.
Its initial one-shot API discovery ran before the server was ready and failed
before any profile mutation. A read-only follow-up found the same first process
healthy; neither retained failure is rewritten as a pass.

The local checker now polls process/listener discovery for up to 120 seconds
instead of relying on a fixed startup sleep and one probe. Mocked delayed-server
and absent-server checks passed, as did 9 runner, 14 checkpoint, 10 continuation
and 2 QMP tests. The eight actual API/browser checks then passed on the same
installed `2a6ad32` candidate: frozen version, bundled UI, first browser download,
synthetic profile, actual browser relaunch with pinned identity metadata, cookie
persistence, distinct-profile backup restore, and Trash restore/confirmation.
The native D09 message was visually checked and closed; the second process
exited with code 1 while the original process/API stayed healthy. Final Defender
observations contained no detections and kept real-time, behavior and IOAV
protection enabled. Windows finished its shutdown-time updates and a positive
QMP `SHUTDOWN` with `guest=true` and `reason=guest-shutdown` confirmed normal
power-off. The completion command exited 0; the exact original reports are
linked by SHA-256 in `reports/windows62/current-candidate-completion.json`.
This is a qualified continuation, not fresh uninterrupted
acceptance, and remains distinct from the fuller `9425f08` scenario trials above.
It checks release/PR artifact provenance, stages exact installers privately,
keeps OS trust decisions with the operator, refuses ambiguous continuation,
and retains failed evidence. VM creation/restoration requires the registered
U: SSD lab and its filesystem identity; there is no fallback to the former C:
lab. Capacity checks and retained-history budgets prevent unbounded full copies.
Test variants use copy-on-write disks and retain every required backing layer.
The canonical checker restores a verified full copy of the clean disk together
with its UEFI/TPM state. A fresh run exposed read-only baseline permissions being
copied into the working disk, preventing QEMU startup. The local restore helper
now adds owner write access only to the verified staged working state, without
changing baseline bytes/modes or group/world access. A failing regression then
passed with all 14 checkpoint tests; independent review found no actionable issue.
The original startup failure is retained. A later capacity refusal is retained
separately; it occurred before another copy or guest mutation.

After both guests passed rebuilt-candidate D14 and were normally shut down with
positive QMP evidence, completed synthetic Windows 11 fixtures and obsolete
layers were removed after live-process, open-file and backing-reference audits.
The clean bases, Windows 10 fixture and sanitized reports remain. The completed
current Windows 11 disk grew to about 28 GiB during Windows updates and exceeded
the existing 24 GiB retained-history budget for another restore. After positive
shutdown and another reference audit, its disk and paired UEFI/TPM working state
were removed together. A new command restores the verified clean base.
The local restore helper accepts completely absent working state only after its
live-QEMU/TPM checks; partial missing state is refused. Two regressions failed
before that change and all 16 checkpoint tests then passed. The next-restore
capacity preflight passed after cleanup. Deleted allocation inside WSL is not
reported as equal physical NTFS space reclamation.

Local evidence includes `PR111-acceptance.txt`,
`reports/pr-111-build/end-to-end.json`, `Windows62-results.txt`, and individual
`reports/windows62/` job reports/screenshots. They are kept with the local
checker rather than committing private VM state or synthetic secret material.
Both original guests were normally shut down before their disks were retained
for the additional trials. Source merge does not publish a new installer or
promote an unsigned preview to stable.
