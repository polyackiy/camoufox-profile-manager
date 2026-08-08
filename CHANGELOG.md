# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **A stability contract for the REST API**, written down in `docs/api.md`:
  what 1.0 freezes (paths, field names, status codes, the error shape), what
  stays deliberately unstable (the `fingerprint` summary, launch internals),
  and what is deprecated with the version it goes away in.
- **`/api/v1` is the canonical API prefix.** The unversioned `/api/...` paths
  keep working as aliases for existing scripts — same routes, same behaviour —
  but are left out of the OpenAPI schema so a generated client sees each
  operation once. They go away in 2.0. The bundled web UI now calls `/api/v1`.
- **One error shape for every failure.** All non-2xx responses now carry
  `{"error": {"code", "message", "details"}}` with a stable snake_case `code`;
  validation failures use it too, so `detail` is no longer sometimes a string
  and sometimes a list. The top-level `detail` string is kept as a mirror of
  `error.message` for FastAPI-style clients and is deprecated, removed in 1.0.
- Every route now declares a stable `operation_id`, a summary and a typed
  `response_model` — including the browser lifecycle endpoints and `/health`,
  which returned bare dicts — so a generated client is actually usable.
- **Check a proxy, and what it says about the profile.** *Check proxy* — in the
  form and in a profile's row menu — reaches the internet through the proxy and
  reports where it comes out, how long it took, and everything a page could
  notice: a timezone on a different clock from the exit country, coordinates far
  from it, or SOCKS credentials Firefox will refuse to send. The form checks what
  is on screen, so a proxy can be tested and a mismatch fixed before saving.
  `POST /api/profiles/{id}/check-proxy` and `POST /api/proxy/check`.
- **A pinned machine can move onto a newer browser.** A pin never ages by
  itself, so a profile created on one Firefox release kept claiming it — and a
  browser several versions behind is itself unusual, since real machines update.
  *Update* in the Machine panel replaces only the browser version and keeps the
  screen, GPU, cores, fonts and noise seeds, which is what a real computer looks
  like after an update. The profile response now reports `browser_major`,
  `installed_major` and `browser_outdated`, and the button appears only when the
  pin is behind. The user agent is resolved for the OS the *pin* describes rather
  than the profile's setting: the two can disagree, and following the setting put
  a macOS user agent on Windows hardware.
- **`stable_canvas`, a per-profile setting.** By default a site sees a different
  canvas each session, which makes a long-lived account look like new hardware
  every visit. Turning this on keeps the canvas reproducible across launches. It
  is off by default because the trade is real: a stable canvas is also identical
  across sites, so they can link the profile between them — which is what real
  hardware does. Browser tests assert the stability, the drift when it is off,
  and the cross-site linkability it costs.
- **Profiles keep the same machine across launches.** Camoufox resolves a new
  fingerprint every time it starts, so the same profile used to report different
  hardware each session — different screen, CPU count and GPU — which is exactly
  what a long-lived account must never do. The first launch now resolves the
  fingerprint once and stores it, and every later launch replays it. Location,
  timezone, locale and WebRTC are deliberately left dynamic so they keep
  following the proxy. The profile form shows the pinned machine, and
  *Regenerate fingerprint* moves the profile to new hardware.
- **Create a profile from a real device.** Camoufox bundles fingerprints captured
  from actual machines (180 Windows, 67 macOS, 65 Linux) and nothing exposed
  them. The profile form now lists them by screen, CPU count and GPU, and picking
  one pins the profile to that device.
- **Export and import a whole profile**, as one archive carrying the profile
  record, its pinned fingerprint and its browser data — cookies, storage,
  history, saved logins. A warmed-up account is mostly that directory, so this is
  what makes backing one up or moving it between machines possible. Exporting a
  running profile is refused rather than copying its databases mid-write, and
  disposable caches are left out (a 51 MB profile packs to about 16 MB). The
  archive is unencrypted and holds live session cookies and the proxy password;
  the UI says so before the download.
- First schema migration step: existing databases gain the `fingerprint` column
  instead of silently keeping the old layout (tables were only ever created with
  `CREATE TABLE IF NOT EXISTS`).
- **Create a profile from the web UI.** The interface had no way to create one —
  the product's core action was reachable only by calling the API directly. One
  form now serves create and edit, covering identity, proxy and the fingerprint
  overrides Camoufox does not derive itself.
- **Groups screen**: create, rename, describe and delete groups, and assign a
  profile to one from the profile form. The backend already supported groups;
  nothing in the UI did.
- **Settings screen**, backed by a new `GET /api/system/config`. It reports the
  effective configuration — never secret values — and warns when proxy passwords
  are stored unencrypted or the API is unauthenticated.
- The web UI can authenticate: it sends `X-API-Key` from a key stored per
  browser, so setting `CPM_API_KEY` no longer locks the bundled UI out.
- Empty states for a fresh install and for a search with no matches.
- **Desktop mode**: `camoufox-pm --desktop` runs the server and opens a native
  window (pywebview; the `desktop` extra).
- **Standalone desktop app**: `scripts/build_desktop.py` +
  `packaging/camoufox-pm.spec` produce a PyInstaller bundle (and a macOS `.app`)
  that runs with no Python or Node installed. `desktop.yml` builds macOS/Windows/
  Linux bundles on demand. Signing, installers, and auto-update are documented
  follow-ups (see `docs/accessibility-roadmap.md`).

### Deprecated
- The flattened `browser_*` fields on `PUT /api/v1/profiles/{id}` — send the
  same keys inside `browser_settings`. Removed in 1.0.
- `browser_session_id` in the launch response: a random value no endpoint ever
  accepted. The launch response now carries `process_id` at the top level.
  Removed in 1.0.
- Top-level `detail` in error responses — read `error.message`. Removed in 1.0.
- The unversioned `/api/...` paths — use `/api/v1/...`. Removed in 2.0.

### Documentation
- Rewrote the README around what the product actually is now — profiles that keep
  one machine — and replaced the install instructions, which still described a
  two-process setup and an old version.
- Added `docs/cli.md` (every command and flag) and `docs/api.md` (the REST API,
  endpoint by endpoint), both checked against a running instance.
- Corrected `docs/excel.md`: it listed column names the export does not use, and
  said nothing about the file carrying proxy passwords.
- Updated the roadmap to record what shipped, and to state plainly what will not
  be built — Chromium profiles and mobile profiles are out of reach for a
  Camoufox manager.
- CONTRIBUTING now explains what the `browser` test marker means and how to run
  the suite as CI sees it, which is the mistake that broke a CI run.

### Changed
- `/health` answers `503` when the database is unreachable instead of a
  healthy-looking `200`, so load balancers and container healthchecks see the
  failure without parsing the body.
- Cloning a profile with bad input returns `400`; it was previously reported as
  `404` even when the source profile existed.
- The interface was rebuilt as a control panel: a single accent colour marks the
  primary action and a running browser, so a running profile is the only thing
  that draws the eye. Hairline rules replace nested cards, machine values are
  monospaced, and `window.alert`/`confirm` gave way to toasts and dialogs.
- Fonts are self-hosted by `next/font`. The stylesheet used to fetch Google
  Fonts at runtime, so the desktop bundle could not render offline.
- Dropped the unused shadcn/Radix/Headless UI/heroicons/tanstack kit: 15 runtime
  dependencies down to 4.
- `camoufox-pm` aligns its settings with the address it actually binds, so
  `--port` is no longer misreported as the configured default.
- Removed the `/ws/monitor` WebSocket route: no client used it, it had no tests,
  and it was mounted without the API-key guard.

### Fixed
- **A new profile no longer picks a random country.** Every generated profile got
  a timezone, coordinates and languages from a randomly chosen region, so a fresh
  profile might claim Shanghai and then be given a German proxy — the manager was
  manufacturing the contradiction it exists to avoid. Geography is now left unset
  and follows the proxy; a region can still be asked for explicitly. Existing
  profiles keep the values they were given — a stored fingerprint is not rewritten
  under a live account — so check one and clear the two fields if they disagree.
- **Coordinates no longer leak the host machine's timezone.** Setting coordinates
  turns Camoufox's IP lookup off, which is the only way it keeps them — and that
  same branch is what fills the timezone and the WebRTC address. Both were left
  unset, so Firefox fell back to this computer's own zone: a profile with Tokyo
  coordinates reported `Europe/Moscow`. The launch path now fills them from the
  same exit address and database Camoufox would have used. Verified in a real
  browser.
- **Profiles created from the UI had an incomplete fingerprint.** Blank optional
  fields were sent as explicit nulls, which cleared the generated timezone,
  geolocation and CPU count — leaving exactly the kind of inconsistency this
  tool exists to avoid. Blank now means "generate" on create and "clear" on edit.
- **Clearing a proxy, group or note silently did nothing** while reporting
  success: the update route treated null as "unchanged". A field that is sent is
  now authoritative, including null; a field that is omitted is left alone. For a
  proxy this mattered — traffic kept routing through a proxy the user believed
  they had detached.
- **A partial update reset the rest of the fingerprint.** Editing only the
  timezone discarded the generated screen resolution, locale and device memory.
- **ID collisions could overwrite a profile.** IDs derived all but two characters
  from a microsecond timestamp, so profiles minted in the same instant — a bulk
  Excel import — had 961 possible values, and storage is keyed on the ID with
  INSERT OR REPLACE. IDs are now fully random over the readable alphabet.
- Notes given at creation were accepted, then overwritten with a timestamp.
- The app opened on a 404: the root page redirected to a route that does not
  exist in the static export.
- Browser sessions are cleaned up when the user closes the window, driven by
  Playwright's close event rather than by polling a pid that belongs to the
  driver process.
- `webrtc_mode: "none"` now actually disables WebRTC, and a profile's local IPs
  no longer overwrite the public one.
- `window_size` reached Camoufox as a "1280x720" string and was rejected; it is
  parsed into the expected tuple.
- Invalid `browser_settings` values return 422 instead of 500.
- Dialogs close on Escape and on a backdrop click, trap focus, restore it on
  close, and keep their header and footer reachable in a tall form.
- Docker publishes its ports on loopback only.

### Security
- Constant-time API-key comparison; `allow_credentials=False` for CORS.
- The Excel export warns, before downloading, that the file carries proxy
  passwords in clear text.

## [0.1.1] - 2026-08-07

### Added
- Docker support: `docker compose up` runs the API + web UI with a persistent
  data volume.
- **Single-process mode**: `camoufox-pm` serves the API and the web UI (a Next.js
  static export) on one port from FastAPI — no separate Node server, proxy, or
  CORS at runtime. Build the UI into the package with `scripts/build_webui.py`.
- **Installable wheel**: the release workflow builds a wheel with the web UI
  bundled in and attaches it to the GitHub Release, so `pip install <wheel>` +
  `camoufox-pm` works with **no Node.js**. Optional PyPI publishing via Trusted
  Publishing (see `docs/releasing.md`).
- A web UI screenshot in the README, from a full end-to-end run.
- `docs/accessibility-roadmap.md` — a plan for reaching non-technical users.

### Fixed
- Committed the web API client (`web/src/lib/api.ts`, `utils.ts`) that the root
  `.gitignore`'s Python `lib/` pattern was silently hiding, so the frontend now
  builds in CI and in fresh clones.

## [0.1.0] - 2026-08-07

First public release after a comprehensive revamp.

### Added
- Opt-in API-key authentication (`CPM_API_KEY`) and environment-based settings.
- Proxy credentials are encrypted at rest with Fernet (`CPM_SECRET_KEY`).
- Test suite: unit, API integration, and opt-in real-browser smoke tests.
- GitHub Actions CI (Python 3.10–3.13, frontend build) and release workflow.
- `.github` issue/PR templates, `CONTRIBUTING`, `SECURITY`, and `CODE_OF_CONDUCT`.
- Next.js dev proxy for the API (no CORS in local development).

### Changed
- Upgraded Camoufox `0.4.11` → `0.5.4` (Firefox 135 → 152).
- Adopted a `src/` layout with the `camoufox_pm` package and `pyproject.toml` + uv.
- The API now binds to `127.0.0.1` by default with configurable CORS origins.
- Browser lifecycle extracted into a dedicated `BrowserSessionManager`.
- The whole codebase and web UI are now English-only and linted with ruff.

### Fixed
- Anti-detect settings now actually reach the browser — `to_camoufox_config()`
  previously returned an empty dict, so geolocation, WebRTC, and hardware
  overrides were silently ignored.
- Server-side profile search (`name` filter) now works instead of returning
  everything.
- Missing web API client (`@/lib/api`) restored, so the frontend builds again.
- Cloning or resetting a non-existent profile returns `404` instead of `500`.
- Browser PID tracking no longer falls back to a fake placeholder value.

### Removed
- Fingerprint generator no longer hand-crafts stale user-agent and WebGL values;
  Camoufox owns fingerprint generation for consistency.
- Committed profile data, leaked proxy credentials, and duplicate/backup files.

[Unreleased]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.1.0
