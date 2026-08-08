# Roadmap

This is a rough, non-binding plan. Priorities may change based on feedback.
Track progress in [GitHub Issues](https://github.com/polyackiy/camoufox-profile-manager/issues).

## Done since 0.1.1

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

## Now (0.1.x)

- The 0.1.x scope is cleared; the items below are what comes next.

## Next

- Authentication for multi-user or hosted deployments, beyond the optional API key.
- Task scheduling and automated fingerprint rotation.

## Later / ideas

- PyPI package once the browser-binary bootstrap is smoothed out.
- Published Docker images.
- Internationalised (i18n) web UI.
- PostgreSQL storage for larger deployments.
- Encrypted profile archives, so an export is not as sensitive as the account.

## Not planned

- **Chromium-based profiles.** This is a Camoufox manager, and Camoufox is
  Firefox. A Chrome fingerprint is a different product.
- **Mobile profiles.** Camoufox is desktop Firefox; Android and iOS profiles are
  out of reach.
- Bundling or redistributing the Camoufox browser binary — it is fetched from
  upstream.
- Anything that facilitates violating a site's terms of service.

## Canvas stability

By default a site sees a different canvas hash each time a profile is launched.
Within one session it is stable per site; the problem is only across sessions,
where a real browser would be unchanging.

**Done: the `stable_canvas` per-profile setting.** Turning it on launches with
`privacy.baselineFingerprintingProtection = false`, which together with the
pinned `fonts:spacing_seed` makes the canvas reproducible across launches. Off by
default, because the trade is that the canvas is then the same across sites — what
a real machine looks like, and what the randomisation existed to prevent. Covered
by browser tests that assert the stability, the drift when it is off, and the
cross-site linkability it costs.

**Also worth doing: report the upstream bug.** `canvas:seed` is advertised in
Camoufox's property manifest and emitted by its Python layer, but its C++ config
reader never reads it and no patch implements it; `window.setCanvasSeed()` is
documented but absent from the shipped build. A working seed would be better than
the pref, because it would give each profile its own canvas value instead of one
shared true render. This does not need a fork — a fork would mean building and
hosting Firefox for every platform and rebasing on every Camoufox release, for
one seed value.

See [profile-settings.md](profile-settings.md#known-limitations) for the measured
behaviour and the trade-off table.
