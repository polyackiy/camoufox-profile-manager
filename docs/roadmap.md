# Roadmap

This is a rough, non-binding plan. Priorities may change based on feedback.
Track progress in [GitHub Issues](https://github.com/polyackiy/camoufox-profile-manager/issues).

## Delivery status after 0.6.0

The published stable installer release is [0.6.0](https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0).
The following work is merged on `main` for the combined **0.7.0** release; it is
not an instruction to expect these additions in the existing 0.6.0 installers.
See [Unreleased changes](../CHANGELOG.md#unreleased) and the
[current product tracker (#117)](https://github.com/polyackiy/camoufox-profile-manager/issues/117)
for evidence and release acceptance. No per-feature installer releases are planned.

| Merged work | User/contributor guide | Tracking |
| --- | --- | --- |
| Bounded batch launch/stop with reviewed targets, progress and cancellation | [Batch operations](batch-operations.md) | [#119](https://github.com/polyackiy/camoufox-profile-manager/issues/119) |
| Mass creation from templates with naming/proxy preview and recovery | [Template batches](template-batches.md) | [#120](https://github.com/polyackiy/camoufox-profile-manager/issues/120) |
| Sequential saved-proxy assignment with lease/version guards | [Proxy sequence](proxy-sequence.md) | [#121](https://github.com/polyackiy/camoufox-profile-manager/issues/121) |
| HTTP/HTTPS/SOCKS4/SOCKS5 diagnostics and installed acceptance | [Proxy support and limits](proxy-support.md) | [#116](https://github.com/polyackiy/camoufox-profile-manager/issues/116) |
| Russian interface, verified Chinese workflows and first-run onboarding | [Localization](localization.md) | [#123](https://github.com/polyackiy/camoufox-profile-manager/issues/123) |
| Declarative workflows with schedules, bounded execution and history | [Automation](automation.md) | [#122](https://github.com/polyackiy/camoufox-profile-manager/issues/122) |
| Explicit SQLite/TCP resource ownership and closed operation evidence | [Safe diagnostics](operation-diagnostics.md) | [#131](https://github.com/polyackiy/camoufox-profile-manager/issues/131), [#139](https://github.com/polyackiy/camoufox-profile-manager/issues/139), [#140](https://github.com/polyackiy/camoufox-profile-manager/issues/140), [#142](https://github.com/polyackiy/camoufox-profile-manager/issues/142), [#144](https://github.com/polyackiy/camoufox-profile-manager/issues/144) |

Before the combined release, review dependencies/security, run the actual
installers through install → create a batch → assign proxies → launch/run →
stop/recover, and retain data-preservation/recovery evidence and exact package
checksums. Do not substitute a source test or an old installer report for release
asset acceptance. Device synchronization remains experimental and explicitly
opt-in; automatic application replacement remains disabled.

## Ordered next work

1. Complete installed synchronization reliability in
   [#143](https://github.com/polyackiy/camoufox-profile-manager/issues/143) and
   [#145](https://github.com/polyackiy/camoufox-profile-manager/issues/145). Preserve
   original ciphertext and fail-closed profile protection; use fixed diagnostics
   and deterministic regressions before claiming an underlying cause. A passing
   rerun alone does not close an intermittent bug. Upload readback/recovery and
   serialized control-store initialization cover reproduced defects; they do not
   prove the cause of every earlier platform refusal.
2. Design and validate an **interactive multi-window action synchronizer** after
   the completed workflow engine. The [proposed architecture](interactive-synchronizer-design.md)
   records leader/target selection, trusted capture, tab mapping, stop/drain and
   security gates. It introduces no runtime feature. Prove the capture/channel
   adapter with upstream Camoufox before implementing a user-facing controller.
3. Improve window organization and manual navigation through supported upstream
   browser interfaces; preserve saved fingerprints and avoid an engine fork.
   See [browser-interface.md](browser-interface.md).
4. Keep the independent production/physical gates open: device-sync review and
   compatibility [#82](https://github.com/polyackiy/camoufox-profile-manager/issues/82)/[#90](https://github.com/polyackiy/camoufox-profile-manager/issues/90),
   updater trust/exact-package acceptance [#63](https://github.com/polyackiy/camoufox-profile-manager/issues/63),
   and native Windows cold-host rendering [#73](https://github.com/polyackiy/camoufox-profile-manager/issues/73).
   These scopes are not completed by ordinary same-runner CI or unsigned releases.
5. Re-evaluate additional everyday workflows in #117, including explicit proxy
   IP-change operations and bounded support reports. Define each operation's
   permission, privacy and retry contract before implementation. Team sharing
   and wider device combinations need separate decisions after the owner-device
   safety contract is accepted.

The commercial-workflow comparison in #117 links official vendor sources. The
implemented installer, browser, proxy library, cookies, templates, extensions,
roles and activity records are the baseline, not missing future features.

## Desktop adoption — current implementation

Stage one now has native installers/build automation, in-app browser setup,
short RU/EN onboarding, profile backups, Trash and backup-gated release checks.
See [accessibility-roadmap.md](accessibility-roadmap.md) and the
[desktop evidence](desktop-acceptance.md) for installation coverage and follow-ups.
Releases remain unsigned under the [release policy](signing.md); certificates
and notarization are not planned or required for publication. A successful build
still needs the release asset/checksum audit before publication.

Manual browsing is part of this stage: use Camoufox's upstream theming toggle
for ordinary tabs and controls, with a per-profile choice. See
[browser-interface.md](browser-interface.md) for the implementation and policy
for tracking upstream changes without maintaining an engine fork.

Stage two includes the [proxy library](proxy-library.md): reusable entries,
bulk import, health checks and assignment to closed profiles. Authenticated
SOCKS5 uses a session-owned relay without changing the Camoufox engine.
It also adds [JSON/Netscape cookies](cookies.md), reusable [templates](templates.md),
managed [Firefox extensions](extensions.md), and [tags/saved views](profile-organization.md).
See [stage-two.md](stage-two.md) for the acceptance record and linked GitHub work.
The [Camoufox compatibility review](camoufox-compatibility.md) records the tested
wrapper/browser pair. Stage three implements [roles, profile access and activity records](stage-three.md)
([#77](https://github.com/polyackiy/camoufox-profile-manager/issues/77)).
Cross-device synchronisation follows a separate design. [Coordinated manager/browser updates](update-recovery.md) now have an integrated
checkpoint, helper, validation and rollback transaction. Public unsigned releases,
including stable releases, retain manual installation. Automatic replacement
remains disabled under its separate trust and platform acceptance requirements.

## Since 0.2.0

- The browser tests assert what a *page* sees, not what automation sees. Three
  timezone assertions read through `page.evaluate`, which since Camoufox
  152.0.4-beta.29 runs in an isolated world with a realm of its own — so they
  reported the host's zone while real pages were correctly spoofed. Verified
  green on beta.28, beta.29 and beta.30.
- Proxy health is visible in the profiles list: a check leaves its answer in the
  row — exit address, country, latency, and a green, amber or red dot — and a
  selection can be checked in one go. On demand only; nothing polls.
- The browser suite no longer touches the internet: pages come from a loopback
  server, and the exit address a launch would look up is named instead. A run
  can no longer fail on someone else's rate limit.
- The web UI runs on Next 16 and ESLint 10, with linting moved from the removed
  `next lint` to the ESLint CLI and the `FlatCompat` shim dropped for the native
  flat config.
- Chrome cookies decrypt with the cipher the platform that wrote them actually
  used (AES-256-GCM on Windows, AES-128-CBC on macOS and Linux) *and* with the
  cookie-store schema they were written under — from schema 24 the plaintext
  carries a `SHA256(host_key)` prefix that is verified against the row's domain.
  Both layers were wrong, so Windows migration had recovered nothing since well
  before App-Bound Encryption existed.

## Shipped in 0.2.0

- Profiles keep one pinned machine across launches, instead of resolving a new
  fingerprint every start.
- Profiles can be created from Camoufox's real device presets.
- A whole profile — settings, pinned fingerprint and browser data — exports to a
  single archive and imports elsewhere.
- A profile's pin can be moved onto a newer browser without changing its
  hardware, so a long-lived profile does not keep advertising the version it was
  created with.
- A proxy can be checked from the UI: where it comes out, how fast, and whether
  the profile's timezone and coordinates agree with it.
- A profile that contradicts itself says so and can be fixed: an OS setting that
  disagrees with the pinned machine is reported with both ways out, and a profile
  carrying geography from before it followed the proxy can have it cleared, one
  profile or a whole selection at a time.
- The Chrome-migration extra reads Chrome 127+ App-Bound Encryption (`v20`)
  cookies when run elevated on the machine that wrote them, and skips — rather
  than corrupts — the ones it cannot. What remains is the part that is inherently
  out of reach off the originating machine: `v20` keys that use the machine-bound
  CNG variant cannot be recovered elsewhere, so those cookies still need
  re-authenticating in Camoufox.
- The web UI covers the product: creating profiles, groups, and a settings screen
  reporting how the instance is configured.
- The REST API is stabilised for `1.0`: `/api/v1` paths (the unversioned ones
  stay as aliases), one error shape everywhere, typed responses on every route,
  and a written stability contract in [api.md](api.md) saying what freezes and
  what deliberately does not.
- The parts that delete profile directories, run browsers and import spreadsheets
  in bulk are under test, and the six bugs that turned up in doing it are fixed.
  What is deliberately left uncovered, and why, is written down in
  [CONTRIBUTING.md](../CONTRIBUTING.md#what-is-deliberately-not-covered).
- Authentication beyond the API key: user accounts (`camoufox-pm user add`),
  argon2id password hashes, HttpOnly session cookies with a real server-side
  logout, and a login screen in the web UI. The three states stay coherent —
  nothing configured is as open as before (loopback), the API key keeps
  machine clients working unchanged, and the moment a user exists humans must
  log in. See [SECURITY.md](../SECURITY.md#authentication).
- Task scheduling, in-process and stored in the database: open a profile's
  browser on a schedule (with an optional self-closing session length), and
  move its pinned fingerprint onto the installed browser version on a schedule.
  Runs missed while the app was closed are recorded and skipped, not replayed.
  This item was originally written as "task scheduling and automated
  fingerprint rotation"; the second half was **deliberately not built**. The
  wording predated pinned fingerprints, when the hardware changed every launch
  anyway — now rotating it on a timer would hand a warmed-up account a new
  machine overnight, which is exactly what the pin exists to prevent. Hardware
  regeneration stays a manual action; the scheduled browser-version refresh is
  the rotation that imitates what real machines do. See
  [scheduling.md](scheduling.md).

## Next

Everything planned for `0.2.0` shipped. These are the known follow-ups, in rough
order of how much they would hurt if left alone:

- Read the Linux keyring for the `v11` cookie password. The key is currently
  always derived from Chrome's hardcoded `peanuts`, so a genuine Linux `v11`
  cookie is skipped rather than decrypted.
- Reconcile the deprecations before `1.0`: the flattened `browser_*` update
  fields, `browser_session_id`, and the top-level `detail` in errors all go away
  in `1.0`, and the unversioned `/api` prefix in `2.0`. See the deprecation table
  in [api.md](api.md#stability-contract).
- **TypeScript 7 waits on the tools, not on us.** The UI is on TypeScript 6.0.3,
  the last TypeScript line with a JavaScript compiler API. TypeScript 7 is the Go
  rewrite: its package ships a `tsc` binary and no API — `require("typescript")`
  has no `createProgram` — and both `typescript-eslint` and Next's build-time
  type check need that API. TypeScript documents a side-by-side install that
  keeps the 6.0 API for tooling, but here it would buy only a faster standalone
  `tsc` that CI never runs, for ~30 MB of platform binaries. Worth revisiting
  when `typescript-eslint` supports TS 7 (their issue #10940) and Next type-checks
  through it.

## Later / ideas

- PyPI package once the browser-binary bootstrap is smoothed out.
- Published Docker images.
- Community localization beyond English, Russian and Simplified Chinese (Russian
  is merged for the upcoming combined release).
- PostgreSQL storage for larger deployments.
- Encrypted profile archives, so an export is not as sensitive as the account.

## Not planned

- **Publisher certificates and macOS notarization.** Releases remain unsigned;
  [#61](https://github.com/polyackiy/camoufox-profile-manager/issues/61) is closed
  as not planned. Signing/notarization are not release gates.
- **Chromium-based profiles.** This is a Camoufox manager, and Camoufox is
  Firefox. A Chrome fingerprint is a different product.
- **Mobile profiles.** Camoufox is desktop Firefox; Android and iOS profiles are
  out of reach.
- Bundling or redistributing the Camoufox browser binary — it is fetched from
  upstream.
- Anything that facilitates violating a site's terms of service.

## Canvas stability

By default a site sees a different canvas hash each time a profile is launched.
Within one session it is stable per site. The current supported pair is
Camoufox 0.5.8 / 156.0.1-beta.36; see
[Camoufox compatibility](camoufox-compatibility.md).

**Done: the `stable_canvas` per-profile setting.** Turning it on launches with
`privacy.baselineFingerprintingProtection = false` to disable randomised image
export. The pinned font list retains the profile's font identity.
Camoufox 0.5.8 no longer generates `canvas:seed` or
`fonts:spacing_seed`. The setting remains off by default: native canvas output
can correlate the same machine across sites. Browser tests cover default drift,
cross-site linkability with randomisation disabled, and exact restart equality
within an initialized renderer environment.

**Known limit: cold-host Windows rendering.** The native Firefox 153 control in
[run 38319076724](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38319076724)
reproduced the same first-render/restart pixel shift seen with Camoufox 156.
CI preserves those cold-host observations, initializes the host renderer with a
bounded synthetic preflight, and then runs the unchanged strict profile checks
and repeated restart assertions. It does not establish an exact-byte guarantee
for first use on a fresh Windows host. There is no product warmup or engine/pref
workaround. See [issue #73](https://github.com/polyackiy/camoufox-profile-manager/issues/73)
and [Windows canvas rendering](windows-canvas-rendering.md).

**Historical upstream report:**
[daijro/camoufox#721](https://github.com/daijro/camoufox/issues/721) described an
inert advertised canvas seed and absent per-context hook in Camoufox 152.
Upstream closed it through [PR #787](https://github.com/daijro/camoufox/pull/787),
which audited documentation and dead code. It is no longer an open request or
evidence that 0.5.8 offers a deterministic per-profile canvas seed.

See [profile-settings.md](profile-settings.md#known-limitations) for the measured
behaviour and the trade-off table.
