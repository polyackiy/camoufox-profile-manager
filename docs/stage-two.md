# Stage two: everyday profile workflows

Stage two makes routine profile work practical without scripts. Implementation
is tracked in [#71](https://github.com/polyackiy/camoufox-profile-manager/issues/71).
Proxy-library work shipped in [#70](https://github.com/polyackiy/camoufox-profile-manager/pull/70).

## Acceptance scope

| Workflow | Required behavior |
| --- | --- |
| Proxies | Reusable entries, list import, checks, guarded bulk assignment and authenticated SOCKS5 |
| Cookies | JSON/Netscape import and export, complete validation, explicit merge/replacement, recovery and real browser verification |
| Templates | Reusable settings, independent new profiles, no copied sessions or pinned fingerprint |
| Extensions | Managed Firefox add-ons, readable permissions, per-profile assignment, upstream Camoufox launch and portable archives |
| Tags | Bounded labels, edits and bulk operations with conflict reporting, persistence and server-side filtering |
| Saved views | Named persistent search/filter/order configurations with predictable application and pagination |
| Integration | Existing profiles remain compatible; English/Chinese UI, documentation, tests and supported-platform CI |

## Completion evidence

The combined browser test creates independent profiles from a template, assigns
an extension, imports cookies, exports/imports a profile, then finds it through
tags and a saved view. Separate browser cases check cookie lifetime and flags,
extension removal/upgrades, authenticated SOCKS5, desktop chrome and pinned
identity. Fixtures and screenshots contain synthetic local data only.

Offline tests cover validation, concurrent edits, cancellation, backup recovery,
archive paths and imported-extension integrity. UI acceptance covers cookie
transfer, templates, extensions, tags and saved views in English and Chinese.
Regular CI runs the backend on Python 3.10–3.13, frontend checks and builds;
compatibility CI runs real browsers on Linux, Windows and macOS. Desktop builds
cover Windows/Linux x86_64 and macOS Intel/Apple Silicon.

Exact run links, revisions and final results are recorded in
[PR #72](https://github.com/polyackiy/camoufox-profile-manager/pull/72) and
[the acceptance issue](https://github.com/polyackiy/camoufox-profile-manager/issues/71).
A successful API response is not a substitute for the real-browser checks.

Windows canvas acceptance uses an explicitly initialized native renderer while
retaining strict equality and repeated restart checks. The separate
[cold-host rendering limitation](windows-canvas-rendering.md) remains tracked in
[#73](https://github.com/polyackiy/camoufox-profile-manager/issues/73); this stage
does not claim to fix native first-use pixel changes.

## Separate work

Team permissions, cross-device synchronization, signed installers and automatic
application replacement are tracked separately. They are not substitutes for
finishing the workflows above. This stage does not modify the Camoufox engine.
