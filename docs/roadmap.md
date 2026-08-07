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

## Known limitation we cannot fix here

A site sees a different canvas hash each time a profile is launched. Within one
session it is stable per site; the problem is only across sessions, where a real
browser would be unchanging.

The fix belongs in Camoufox, and the machinery is already there: its per-context
patches add `window.setCanvasSeed()`, which the current release does not expose,
and the `canvas:seed` config property is advertised but not honoured for 2D
canvas readback. The useful next step is an upstream issue carrying the
measurements — not a fork, which would mean building and hosting Firefox for
every platform and rebasing on every Camoufox release, all for one seed value.

See [profile-settings.md](profile-settings.md#known-limitations) for the measured
behaviour.
