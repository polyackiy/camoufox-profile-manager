# Roadmap

This is a rough, non-binding plan. Priorities may change based on feedback.
Track progress in [GitHub Issues](https://github.com/polyackiy/camoufox-profile-manager/issues).

## Done since 0.1.1

- Profiles keep one pinned machine across launches, instead of resolving a new
  fingerprint every start.
- Profiles can be created from Camoufox's real device presets.
- A whole profile — settings, pinned fingerprint and browser data — exports to a
  single archive and imports elsewhere.
- The web UI covers the product: creating profiles, groups, and a settings screen
  reporting how the instance is configured.

## Now (0.1.x)

- Stabilise the REST API before `1.0`.
- Proxy testing and health checks in the UI, so a dead proxy is visible before a
  profile is launched with it.
- Fill remaining gaps in test coverage.

## Next

- Warn when a profile's timezone contradicts its proxy's country — the mismatch
  the pinned-fingerprint work deliberately avoids creating, but which a user can
  still configure by hand.
- Keep the pinned machine coherent when the operating system changes, instead of
  asking the user to regenerate.
- Authentication for multi-user or hosted deployments, beyond the optional API key.
- Task scheduling and automated fingerprint rotation.
- Windows App-Bound Encryption support for the Chrome-migration module.

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

**Planned: make this a per-profile setting.** Launching with
`privacy.baselineFingerprintingProtection = false` stops the randomisation, and
together with the pinned `fonts:spacing_seed` already stored per profile the
canvas becomes fully reproducible — measured, identical across three launches.
The trade is that the canvas is then the same across sites, which is what a real
machine looks like and what the randomisation existed to prevent. That makes it a
choice rather than a default: one long-lived account per profile wants it on,
unlinkable browsing wants it off. The work is a setting, the launch option, and a
browser test alongside the existing fingerprint-stability ones.

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
