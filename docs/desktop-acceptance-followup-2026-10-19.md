# Remaining desktop acceptance — 19 October 2026

These later same-day trials extend the [Windows record](desktop-acceptance-windows-2026-10-19.md)
and [Linux record](desktop-acceptance-linux-2026-10-19.md) for
[#62](https://github.com/polyackiy/camoufox-profile-manager/issues/62).
Historical failures and qualifications remain recorded. The complete platform
matrix and signing requirements remain open.

## Exact packages

Current packages came from source `2a6ad3288b974d8aef02d56a1194c30d384f2e71`,
[build 39269728704](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/39269728704).
The selected Linux artifact's checkout was verified against that source.

| Package | Bytes | SHA-256 |
| --- | ---: | --- |
| Windows x64 setup | 86,336,017 | `58077a2738aaf45f19e7952bbb1df58054729ea137a22ce71205ac9c421844c2` |
| Linux x64 DEB | 249,634,848 | `b7a2bc425b8923e0b5c834db6c49ff49348e8b5fc0b935b89323fe73bf4b1d32` |
| Linux x64 portable | 315,447,633 | `cd472884a84892a6a52e97eab5163b70831d6e702e7ccf9adb08cabdd1434439` |

The earlier published [v0.6.0-rc.7 DEB](https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0-rc.7)
was 250,884,320 bytes, SHA-256
`3ee4e2a02aaf2879cd182f390eb659a4d6b61e596ac15b914b580faf039da32e`.
APT reported `0.6.0~rc7` before upgrade and `0.6.0~rc8` afterward; frozen APIs
reported `0.6.0rc7` and `0.6.0rc8` respectively. All disposable guests and caches
were in the dedicated WSL/QEMU lab on the U: SSD, using synthetic data only.

## Windows additions

Windows 10's retained ordinary-user fixture passed the missing positive
permanent-delete case. A new empty disposable profile was moved to Trash:
unconfirmed API DELETE returned 400, then `confirm=true` removed only that profile.
Original active/Trash ID sets and the complete retained fixture were checked
afterward. Normal shutdown received positive QMP guest-shutdown evidence.

Windows 11 used a restored clean image, ordinary `tester` account and the exact
current setup above. Fresh CIM observations reported Enterprise Evaluation
26H2 **26300.9457**; the older desktop watermark was not used for OS identity.

| Check | Observation |
| --- | --- |
| Download/install | Exact checksum and Internet-zone marking retained; Defender scan completed. Operator handled SmartScreen; normal per-user wizard and installed Start menu shortcut worked. |
| D03 error | A 90-second HTTP/HTTPS outage affected only the Windows 11 QEMU UID. Native error/Retry displayed; API reported `error`, never partial `ready`. |
| D03 retry | After automatic network restoration, native Retry completed: Camoufox `156.0.1-beta.36`, API `ready`. Original isolation rules were unchanged. |
| Protection afterward | Defender real-time, behavior, IOAV, tamper protection, Secure Boot, TPM and UAC remained enabled. |
| Cleanup | Positive QMP guest shutdown; audited working disk and paired UEFI/TPM removed, retaining clean base and offline Windows 10 fixture. |

The Windows 11 image already contained WebView2 with machine registration
`NoRemove=1`, `SystemComponent=1`. Its Authenticode-valid Microsoft uninstaller
was invoked with the registered normal command and exited **93**, leaving the
runtime present. All four registry views and runtime executable locations were
checked afterward. No forced removal, hidden registration or deleted runtime
files simulated absence. This combination is **not passed**; it needs a suitable
supported image or a support-matrix decision. Windows 10's earlier genuine
runtime-absence/cancellation/retry results remain separate.

An older local ShellOpen/handle-wait opener timed out at the trust step. Its
failed receipt remains. Normal QMP shutdown, transport archival and reboot
preserved the same guest disk; Explorer then opened the installer, with the
operator deciding SmartScreen. The recovery does not establish an uninterrupted
single-command pass or rewrite the original command failure.

## Ubuntu 22.04.5 native desktop and full fixture

The fresh x64 guest used Xfce, Xvfb, software Mesa and installed Fcitx5 Pinyin.
System Python 3.10.12 was already in the image; Node/Node.js were absent. No
separate Python or Node installation was performed for the bundled app. This
does not establish operation on a machine without system Python or physical GPU.

Native GTK control and actual Camoufox page accepted physical Pinyin key events,
showed candidates and committed **你好**. The page recorded trusted
`compositionend`, with no DOM fill or synthetic composition. Current DEB and
portable launches both passed. Desktop setup required the normal
`fcitx5-module-xorg` package and the session display environment in D-Bus
activation; application and browser files were not patched.

The fixture included two original profiles, an additive new-ID restore, retained
Trash, authenticated local non-forwarding proxy, group, paused schedule,
administrator/viewer accounts and profile grant. Tokens were seeded only on each
profile's first site visit; later observations never reseeded them. Private
digests, credentials, database, key and archives were not published.

| Scenario | Observed result and scope |
| --- | --- |
| D01–D02 | Verified packages; normal APT dependencies and Applications-menu launch of rc.7/current DEB. Current portable ran from user-writable extraction after DEB purge. |
| D03 | Native first-download error/Retry passed on rc.7; downloaded Camoufox `156.0.1-beta.36` was retained through upgrade. |
| D04 | Baseline close/reopen preserved cookie, localStorage, site-visible machine and stable canvas. Current lifecycle comparisons matched the original reference. |
| D05 | Native browser tab creation/close on baseline and current portable, without regenerating identity. |
| D06 | Baseline closed-profile backup restored into new ID; all ZIP streams readable, source/archive retained, restored session/fingerprint/proxy usable. |
| D07 | Baseline same-ID Trash restore preserved real session; missing API confirmation refused, confirmed deletion removed separate empty disposable. Current native wrong-name dialog refused deletion; original Trash remained. |
| D08 | Current DEB and portable native close stopped manager, API and launched browser without forced termination. |
| D09 | Current second instance displayed already-open refusal; after closing its notice it exited, leaving first API/browser usable. |
| D10–D11 | Current backup service refused running profile and unreadable Trash directory; after restoring permissions, three readable `before_update` archives included Trash with no skips. Preparation control below. |
| D12 | Actual APT rc.7 → current rc.8 upgrade preserved stopped database/key bytes, existing login, six collections, original session/machine/canvas. |
| D13 | Current DEB reinstall preserved stopped database/key; existing login, proxy, six collections and original session/machine/canvas matched after launch. |
| D14 | Normal APT remove/reinstall preserved database/key and reused original fixture/session/proxy. Purge followed by current portable launch also preserved them. |

The six collections were active profiles, Trash, groups, schedules, users and
grants. Database/key digests were compared only privately.

### Update-preparation control

Current rc.8 correctly reported no later official preview and preparation returned
409; that alone does not exercise its incomplete-backup branch. After current
lifecycle trials, the genuine rc.7 binary was separately extracted for an
auxiliary control on the synthetic fixture, without spoofing version/feed or
replacing current package bytes. The real official feed offered a later preview.
Preparation returned 409 with an incomplete-backup explanation for running and
unreadable-Trash cases; with all profiles closed/readable it returned 200 and
three readable archives including Trash. This is an rc.7 control, not a second
upgrade trial. The preparation route and backup implementation have identical
Git blobs in rc.7 and tested `2a6ad32`; current backup-service failure cases were
also exercised directly.

### Harness corrections and limits

Harness corrections covered direct schedule-response handling, explicit
coordinates for a non-forwarding proxy, copying dangling Firefox lock symlinks
as links, and login before protected configuration access. Initial failed
launches occurred before the first session seed. Fixture identities and the
original session reference were not regenerated to pass comparisons.

The preceding table records Ubuntu 22.04.5. macOS ARM/Intel, signing and physical
cold-host GPU behavior remain outside these observations. The complete #62 matrix
stays open.

## Fresh Ubuntu 24.04.5 follow-up

The retained official Ubuntu 24 cloud image was booted from its relocated U:
path, SHA-256 `04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94`.
This is a new guest, separate from Ubuntu 22 and the historical `3a7009f` trial.

The rc.7 normal launcher failed with missing `libwayland-server.so.0`, before any
profile fixture was seeded. Current DEB installation resolved the declared
dependency through normal APT and its Applications-menu launch worked, without
manual library injection. The failed rc.7 launch is not a successful
distinct-version lifecycle baseline. D12 with pre-existing data remains the
Ubuntu 22 observation above.

Current-package native browser download error/Retry passed. The read-only
10-second observer timed out during the final stage; native UI and a subsequent
read-only API request confirmed `ready` for the same retry. The download was not
retriggered. GTK and real Camoufox Pinyin passed, including trusted composition.
Current profile reopening, new-ID backup restore, Trash recovery, confirmed
empty-disposable deletion, second-instance refusal and readable backups including
Trash also passed with the original session/machine/canvas reference.

The current DEB reinstall and normal remove/reinstall preserved stopped database
and key bytes, existing accounts/proxy, all six collections and original
cookie/localStorage/machine/canvas. After DEB purge, current portable reused that
same fixture; its Pinyin, native tabs and wrong-name delete refusal passed.
Both DEB and portable native close stopped the server and launched browser.
System Python 3.12.3 was preinstalled in this image; Node/Node.js were absent.
No separate interpreter was installed for the bundled application.

## Local manual checker

The local `Test-Release.ps1` now resolves release assets directly to the verified
U: artifact cache. Its previous `outputs/cache` path was already a junction to
that same U: directory; no physical C: cache relocation or space recovery is
claimed for this change. A regression first failed for the missing resolver,
then passed for ordinary/preview tags, invalid tags and missing-lab refusal.
An actual `-Tag v0.6.0-rc.7` audit exited 0 with all three Windows/Linux package
digests matching. Asset audit remains distinct from desktop scenario acceptance.

## Evidence and cleanup

Sanitized local receipts in `reports/remaining62/` and `reports/windows62/`
include package provenance, 28 Ubuntu 22 and 27 Ubuntu 24 observation records, native screenshots and
Windows error/retry/protection/QMP evidence. Failed receipts remain distinct.

Linux normal systemd poweroff produced serial `reboot: Power down`. After
live-process, open-file and backing audits, its approximately 9.9 GiB working
lab and private fixture/login file were removed. Completed Windows 11 state
removed approximately 22.2 GiB of allocated WSL data; a cancelled 154 MiB duplicate
download and empty failed-setup directory were removed too. Removed WSL allocation
does not imply equal physical NTFS reclamation.

The new Ubuntu 24 guest also shut down normally and passed the same reference
audit before its approximately 7.8 GiB working lab/private fixture was removed.

Reusable official images, verified cache, sanitized receipts and offline Windows
10 fixture remain on U:. Checks remain manual. No scheduled task, certificate
purchase, new release or stable promotion was added.
