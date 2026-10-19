# Linux package acceptance — 19 October 2026

The unsigned DEB and portable archive built from
`3a7009f0d428321b6901fe1ba0fff220f3fe98ab` in
[workflow 39117477241](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/39117477241)
passed the limited checks below in separate fresh Ubuntu 22.04.5 and 24.04.5
x64 cloud-image guests. These results concern that Linux build; they do not
automatically validate later commits, including the shared frozen-version hook,
or complete
[#62](https://github.com/polyackiy/camoufox-profile-manager/issues/62).

| Exact package | SHA-256 |
| --- | --- |
| `camoufox-pm-linux-x86_64.deb` — 249,631,282 bytes | `d024906ee6bf87492732d15a9a2e01077cd1273e541bc28981f6500a25c1e7a7` |
| `camoufox-pm-linux-x86_64.tar.gz` — 315,452,330 bytes | `b2ba39fbf56e8931defeff829093fdbcbef580b1725f927d40662c49e2b6be17` |

Both guests used Xvfb and software Mesa with synthetic profiles. The cloud
images include system Python; Node and Node.js were absent. The app uses its
bundled Python. This is not acceptance on a machine without system Python.

## Observed results

Nine package checks passed on each guest: apt installation, launcher, painted
Qt window, ordinary desktop close, reinstall with database/key preservation,
remove with preservation, installation after removal with preservation, purge
with preservation, and portable GUI reopening the preserved data after purge.
The native-window screenshots were inspected for both the DEB and portable app.

Eight limited API/browser checks also passed: frozen health, bundled UI,
Camoufox installation, synthetic profile creation, real browser relaunch with
pinned identity metadata, persistent cookie, backup/new-ID restore, and Trash
restore/refusal of unconfirmed permanent deletion. The application reported
`0.6.0rc8`; Camoufox reported `156.0.1-beta.36`.

The fresh Ubuntu 22.04.5 trial used system Python 3.10.12 and the native
`libasound2` 1.2.6.1 provider. Neither `libasound2t64` nor
`liboss4-salsa-asound2` was installed. Its ALSA target was
`/usr/lib/x86_64-linux-gnu/libasound.so.2.0.0`.

The first Ubuntu 22 trial failed before product installation because the local
test harness used `hashlib.file_digest`, unavailable in Python 3.10. The
harness was corrected to use SHA-256 over the same file bytes. A fresh guest
then passed; the original failed report remains separate. Product package bytes
were unchanged.

## Limits and retained evidence

These checks do not establish native IME/input, physical GPU behavior,
interactive menus/tabs, credential-store prompts, signed upgrades, or the full
desktop fixture and scenario matrix. No publisher-signing claim is made.

Local evidence is retained under `reports/final-source-build/` for Ubuntu 24
and `reports/linux22-20261019-retry/` for Ubuntu 22, including package identity,
runtime libraries, sanitized reports and screenshots. Private databases, keys,
cookies and archives are not published.

The Ubuntu 22 guest ran in the dedicated U: SSD lab. After normal shutdown and
checks for live/backing references, both its failed and successful disposable
VMs were removed. Reusable OS media remains on U:. The Ubuntu 24 base image
was subsequently moved from C: to U: with the same verified SHA-256 and no old
backing references. That path relocation has not received a new Ubuntu 24
boot trial; the earlier Ubuntu 24 acceptance remains evidence for its recorded
environment.
