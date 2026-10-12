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

The final implementation must cover a combined workflow: create independent
profiles from a template, assign an extension, import cookies, export/import a
profile, then find it through tags and a saved view. Browser tests use synthetic
local data; real account cookies and proxy credentials must never enter fixtures,
screenshots, commits or public reports.

Running profiles and concurrent edits must be handled explicitly. A successful
HTTP response alone does not establish that imported cookies are visible to the
browser or that an extension actually loaded: these require Camoufox tests.

## Separate work

Team permissions, cross-device synchronization, signed installers and automatic
application replacement are tracked separately. They are not substitutes for
finishing the workflows above. This stage does not modify the Camoufox engine.
