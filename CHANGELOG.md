# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Proxy health in the profiles list.** A check now leaves its answer in the
  row — the exit address, country and latency under the configured proxy, with a
  dot that is green when the proxy answers and agrees with the profile, amber
  when it answers but contradicts it, and red when it does not answer or when a
  finding means the launch itself is wrong. The full detail is in the row's
  tooltip. Selected profiles can be checked in one go, a few at a time so the
  rows land as progress rather than one long wait.

  Checks happen only when asked for; nothing polls. The answer is kept on the
  profile so it survives a reload, and is dropped the moment the proxy
  changes — an answer from the proxy that was there says nothing about the one
  that is there now. A clone starts unchecked: it shares its source's proxy, but
  it is not the profile that was checked.

### Changed
- **The browser test suite runs offline.** It used to load `example.com` and
  `iana.org` to compare canvases per site, and launching asked the internet for
  this machine's public address through Camoufox's geoip lookup, so a run could
  fail on someone else's rate limit and look like a product bug. Pages now come
  from a loopback server and the exit address is named rather than looked up —
  which keeps the geoip path rather than switching it off, since Camoufox gates
  only the HTTP lookup on `geoip is True` and resolves everything else from the
  database on disk. Verified by running the suite with every outbound connection
  blocked: all 22 pass with nothing but loopback reachable. That check ships as
  `pytest --no-network`, which raises past `except Exception` so a module that
  swallows failures by design cannot hide a connection from it. Two tests keep
  the claims honest: the cross-site assertion only means something if the browser
  really treats the two local origins as different sites, and the guard only
  means something if it outranks the code it polices.
- **TypeScript 5.9 → 6.0.3**, the last TypeScript line with a JavaScript compiler API.
  TypeScript 7 is the Go rewrite and ships no API, which is what
  `typescript-eslint` and Next's build-time type check both run on, so 6.0 is as
  far as the toolchain goes for now. See the roadmap for the detail.

## [0.3.0] - 2026-08-14

A toolchain release. Next 16 removed the `next lint` the CI job called, which
turned a routine dependency bump into a migration and pulled the rest of the
frontend stack forward with it — ESLint 10 and lucide-react 1. Two of the three
majors were blocked by a dependency that has not published in months, and both
turned out to be workable rather than something to wait for. What a user sees is
the same product with a handful of icons redrawn.

### Changed
- **Icons are on lucide-react 1.** Nothing the UI imports was dropped in the
  major, but four of the names had been legacy aliases and now use the ones
  lucide actually ships: `TriangleAlert`, `LoaderCircle`, `Ellipsis`, and
  `RotateCcwClock` (kept locally as `History`, which is what it means beside a
  History button). Five icons were redrawn upstream — the play triangle and the
  pause bars are heavier and now match each other, and the gear, trash, and
  calendar-clock are near-identical at the sizes the UI uses.
- **The web UI is on Next 16.** Next 16 removes `next lint`, so linting now calls
  the ESLint CLI directly; `eslint-config-next` 16 ships real flat configs, so the
  `FlatCompat` shim and `@eslint/eslintrc` are gone. Next 16's rule set flagged
  seven `set-state-in-effect` findings: the page-reset-on-filter-change is now
  adjusted during render, which is React's documented pattern and avoids painting
  the old page once before correcting itself, and the remaining six each carry a
  written reason — reading `localStorage` after mount cannot happen during a
  prerender, and a loader that clears the previous error before awaiting is doing
  it on purpose.

  **ESLint is on 10**, which took working around a dependency that has not moved:
  `eslint-config-next` asks `eslint-plugin-react` to detect the installed React
  version, and detection in the bundled 7.37.5 — the last release, from April
  2025 — calls the `context.getFilename()` ESLint 10 removed, which aborted the
  entire run. The config now names the version instead, read from React itself,
  so detection never runs. The resolved rule set is identical to the one ESLint 9
  produced: same 113 rules at the same severities.

  **TypeScript stays on 5**, still blocked upstream: `typescript-eslint` declares
  `typescript: >=4.8.4 <6.1.0` and refuses to load under TypeScript 7.

  `next` and `eslint-config-next` are pinned to the same exact version rather
  than a range, because a mismatched pair between them is a broken lint run, and
  the lint script fails on warnings so a new accessibility warning stops CI the
  way `next lint` used to.

### Security
- The web UI's dependency tree is clean again. `npm audit` reported eight
  advisories against the Next 15 lockfile — one critical, six high, most of them
  reached through the `sharp`/libvips chain that Next 15 pulled in — and reports
  none against this one.

## [0.2.1] - 2026-08-10

A fix release. Three of the four came out of one thread: reading Chrome's cookie
format properly, which turned out to be wrong in two independent layers. The
fourth was found while taking screenshots for the README.

### Fixed
- **A device preset could not be pinned behind an unreachable proxy.** Resolving
  the machine ran with Camoufox's IP lookup on, so it reached the internet
  *through* the profile's proxy — and creating a profile from a captured device
  failed outright when that proxy was dead, which says nothing about its screen
  or its GPU. Only hardware is frozen; geography is deliberately left out of the
  pin and follows the proxy at launch, so the resolve is now offline. Found while
  taking screenshots of the product for the README.
- **Chrome cookie migration was broken on Windows, and `v11` everywhere.** The
  `v10`/`v11` tag does not name a cipher: on Windows those values are
  AES-256-GCM, on macOS and Linux AES-128-CBC with an IV of sixteen spaces. The
  decryptor applied CBC to all of them. On Windows that fails outright for
  fifteen cookies in sixteen — and for the sixteenth, where the body happens to
  be a whole number of AES blocks, it "succeeded" and wrote random bytes as the
  cookie value. A 36-character session UUID is exactly that case. Separately,
  `v11` was read as "12-byte IV, then CBC", which cannot work at all because CBC
  needs a 16-byte IV, so `v11` never decrypted on any platform. The cipher is now
  chosen by platform, PKCS#7 padding is validated instead of trusted, and a value
  that cannot be decrypted is skipped rather than written mangled.
- **…and the cookie-store schema on top of it.** From schema 24 (Chrome ~130) the
  encrypted plaintext is `SHA256(host_key) || value`, not the value — a cookie
  store change, so it applies to `v10`, `v11` and `v20` alike, on every platform.
  Getting the cipher right but not this recovered *zero* cookies from any recent
  Chrome, because the plaintext then begins with a digest. The schema version is
  read from the database and the digest is verified against the row's own domain
  before being stripped, exactly as Chrome does; that check also gives the CBC
  platforms the integrity signal they otherwise lack. `v20` no longer strips the
  prefix unconditionally, which was wrong for the Chrome 127–129 window that
  wrote App-Bound cookies into a schema-23 store.
- A migration that decrypts nothing now says so. Every failure was `debug` and
  the summary counted unencrypted rows, so a wholly broken key reported
  "Successfully decrypted 0 Chrome cookies".

## [0.2.0] - 2026-08-08

The release where a profile became a machine you can keep. `0.1.x` could launch
Camoufox with settings; it could not promise that the same profile looked like
the same computer tomorrow. It does now, and everything below follows from that:
pinning the fingerprint, moving a pin onto a newer browser without changing the
hardware, checking that a proxy agrees with the profile, and refusing to rotate
hardware on a timer because that would undo the whole point.

### Added
- **User accounts and login, for deployments more than one person can reach.**
  The API key stays what it was — one shared machine secret — and humans now
  get their own path: `camoufox-pm user add <name>` creates an account
  (password prompted, argon2id-hashed, never in argv or logs), and from the
  moment any account exists the API requires a login session or the API key.
  The web UI grows a login screen and a logout control. Sessions are opaque
  random tokens stored server-side as SHA-256 — the cookie is HttpOnly,
  SameSite=Lax, Secure over TLS (`CPM_SECURE_COOKIES` forces it behind a
  proxy), expires after `CPM_SESSION_TTL_HOURS` (default a week), and logout
  deletes the server-side row, so a logged-out token is dead even if replayed.
  A failed login does not reveal whether the username exists, and costs the
  same argon2 work either way plus a half-second delay. There is deliberately
  no self-registration and no role system: accounts are managed from the CLI
  (`user add|passwd|remove|list`), and every authenticated user is equivalent —
  profiles are shared, so there is nothing for roles to protect yet. With no
  accounts and no `CPM_API_KEY`, the loopback default stays exactly as open as
  before. `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`,
  `GET /api/v1/auth/session`; existing databases gain the `users` and
  `sessions` tables on first start without touching existing rows.
- **Task scheduling.** A profile can now be opened on a schedule (account
  warming, a regular session — with an optional "close after N minutes" so the
  session ends by itself), and its pinned fingerprint can be moved onto the
  installed browser version on a schedule, so a long-lived profile keeps pace
  with browser releases the way a real machine does. The scheduler runs inside
  the `camoufox-pm` process — no cron, no extra service — and scheduled
  launches go through the same session manager as manual ones. Schedules are
  either *every N minutes* or *daily at HH:MM* on chosen weekdays, on the
  server's clock; they persist in the database and survive restarts. Runs
  missed while the app was closed are recorded as `missed` and skipped, never
  replayed — a night's backlog of warming launches must not open at once on
  startup. Every run is recorded (`ok`, `skipped`, `error`, `missed`; last 20
  kept per schedule), one failing schedule never blocks another, and a schedule
  whose profile is gone disables itself. New **Schedules** screen in the UI and
  `GET/POST/PUT/DELETE /api/v1/schedules`, `POST /api/v1/schedules/{id}/run`,
  `GET /api/v1/schedules/{id}/runs`.

  **Deliberately absent: scheduled hardware rotation.** The roadmap item said
  "automated fingerprint rotation", wording left over from before fingerprints
  were pinned, when they changed every launch anyway. Rotating the hardware on
  a timer would hand a warmed-up account a new GPU, screen and core count
  overnight — exactly what the pinned machine exists to prevent — so it was not
  built. Hardware regeneration stays a deliberate manual action; the honest
  scheduled rotation is the browser-version refresh. See `docs/scheduling.md`.
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
- **A profile that disagrees with itself now says so, and offers the fix.** Two
  cases, both of which a profile could previously carry silently:
  - **The OS setting and the pinned machine.** The pin is what a page sees, so
    changing the `os` dropdown on a pinned profile changed nothing observable and
    warned about nothing — the setting just quietly stopped meaning anything. The
    profile response now reports `pinned_os`, `settings_os` and `os_mismatch`,
    the save is logged, the form says so under the dropdown, and the Machine
    panel offers both honest ways out: *Keep this machine* puts the setting back
    and changes no fingerprint value at all, or a new machine is pinned for the
    OS that was chosen — new screen, GPU, cores, fonts and seeds, which for a
    warmed-up account is a real cost. `POST /api/profiles/{id}/reconcile-os`,
    which refuses when the two already agree.
  - **Geography from before it followed the proxy.** Profiles created when every
    new one was given a randomly chosen region still carry that timezone and
    those coordinates, so an old profile can claim Shanghai on a German proxy and
    only find out if someone presses *Check proxy*. They are still not migrated
    behind the user's back, but both fields can now be cleared in one action —
    from the profile form, or as a bulk action on a selection in the profiles
    list — after which Camoufox derives the timezone, the coordinates and the
    WebRTC address from the exit address, as it does for a profile created today.
    Languages and locale are left alone; they are identity, not geography.
    `POST /api/profiles/clear-geography`. No heuristic guesses which values were
    deliberate: nothing recorded it, and the only distinguishable signal is
    exactly what a deliberate choice of that city would look like, so the action
    is offered wherever geography is set and explained rather than inferred.
- **Chrome App-Bound Encryption (`v20`) cookies in the migration extra.** Chrome
  127+ on Windows writes new cookies under App-Bound Encryption, whose key is
  double-DPAPI-wrapped (an outer SYSTEM layer, an inner user layer) plus an AEAD
  wrap from Chrome's elevation service — the classic DPAPI path could not read
  them. The migration extra now decrypts `v20` cookies when run **as
  Administrator on the machine that wrote them**, using the documented offline
  unwrap chain (SYSTEM DPAPI via `lsass` impersonation → user DPAPI → the
  elevation service's AEAD key → AES-256-GCM, stripping the 32-byte domain
  prefix). The parsing, key-unwrap and cookie decryption are pure and unit-tested
  cross-platform; the Windows DPAPI/CNG syscalls are isolated in `abe_windows.py`
  and imported lazily so the module still loads on macOS/Linux. When it cannot
  decrypt a `v20` cookie (not elevated, a different machine, a machine-bound
  `flag 3` key, or non-Windows), the cookie is **skipped with one clear warning**,
  never written as garbage. Adds `pywin32` to the `chrome-migration` extra,
  gated to Windows.
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

### Changed
- Dependabot keeps the GitHub Actions and the web dependencies current. Python is
  deliberately excluded: Dependabot has no uv ecosystem, and its pip updater would
  leave `uv.lock` stale against a bumped `pyproject.toml`, which `uv sync --frozen`
  refuses — a pull request that cannot build is worse than no pull request.
- **Docker runs one container, not two.** The compose file still ran the old
  two-process stack — a Python service on 8000 and a separate Next.js server on
  3000, wired together with CORS — months after everything else moved to one
  process serving the UI and the API on one port. The image now builds the UI in
  a Node stage, copies the static export into the package and runs the same
  `camoufox-pm` entry point a local install does; `web/Dockerfile` is gone and
  there is no CORS to configure. Verified by building the image and creating a
  profile through the running container.

### Fixed
- **An imported archive ignored a `name` sent as a form field.** Every other part
  of that multipart request travels in the body, so a client author reaches for
  the form field first — and got no error and no rename, because only the query
  parameter was read. Both work now.
- `POST /api/v1/profiles/clear-geography` refuses an empty profile id instead of
  cheerfully reporting it as `not_found`.
- **The app reported the wrong version.** `__version__` was a second copy of the
  number in `pyproject.toml`, so a wheel built as `0.2.0` announced `0.1.1`
  through `/health`, the OpenAPI schema and `--version`. It is read from the
  installed package metadata now, with a test that fails if the two ever
  disagree again — found by installing the release wheel into a clean
  environment and asking it what it was.

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
- The parts that had never been exercised are now tested: deleting profile
  directories, the browser session lifecycle, bulk spreadsheet import, the
  destructive system endpoints and the command-line launcher. Six bugs came out
  of writing them, listed below. `ProfileCleanupManager` takes the database path
  separately from the data directory, and its CLI gained `--db-path`;
  `create_profile` takes a `status`. What is left uncovered on purpose, and why,
  is written down in CONTRIBUTING.
- Removed `ExcelManager._prepare_profile_updates`, which nothing has ever called:
  import only creates profiles.

### Fixed
- **The cleanup endpoint could delete every profile directory on the machine.**
  `POST /api/system/profiles/cleanup` and the diagnostic beside it built their
  storage view from hard-coded defaults — `./data` and `./data/profiles.db` —
  instead of the configured database. With `CPM_DB_PATH` pointing at any other
  file name, they opened an empty database next to the real one, found nothing to
  match the directories on disk against, and removed all of them. A profile
  directory is the account: cookies, storage and saved logins go with it.
- **A clone was the same machine as the profile it came from.** Cloning copied
  the pinned fingerprint along with everything else, so the copy reported an
  identical GPU, screen, core count, fonts and noise seeds — and a pin never
  changes on its own, so it stayed that way. Two accounts that are provably one
  computer is the one thing this tool exists to prevent. A clone now starts
  unpinned and takes its own machine on first launch; asking it not to regenerate
  the fingerprint still keeps the pin, which is the deliberate case.
- **A rearranged spreadsheet imported into the wrong fields.** Import located
  columns by position, so deleting the read-only ID column or dragging one
  elsewhere — the two things bulk editing invites — shifted every field after it.
  Nothing objected, because most of them are free text: a sheet with Locale moved
  ahead of Timezone imported cleanly with those two values swapped, for every row.
  Columns are now found by their header, and a file without a recognisable header
  row is refused instead of imported as garbage.
- **Excel import dropped the Notes and Status columns.** Both were exported, both
  were read back out of the row, and neither was ever applied, so every imported
  profile came back active and without its notes. An unrecognised status now
  fails that row rather than quietly reactivating a profile someone had parked.
- **Profile statistics were always empty.** `GET /api/profiles/{id}/stats` read
  keys the manager never returned, so it reported no sessions, no last session
  and no actions however much the profile had been used.
- **Cloning an unknown profile answered 500 instead of 404**, reporting the
  caller's own bad id as a server fault.
- **A page of profiles could not be taken from the middle of the list.** SQLite
  will not accept an `OFFSET` without a `LIMIT`, so `list_profiles(offset=...)`
  on its own had the offset dropped and returned the first page.
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

[Unreleased]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.1.0
