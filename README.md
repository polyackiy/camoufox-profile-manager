# Camoufox Profile Manager

[![CI](https://github.com/polyackiy/camoufox-profile-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/polyackiy/camoufox-profile-manager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Camoufox 152](https://img.shields.io/badge/camoufox-152-orange.svg)](https://github.com/daijro/camoufox)

A self-hosted, open-source manager for [Camoufox](https://github.com/daijro/camoufox)
antidetect browser profiles — a free alternative to AdsPower, Dolphin, Multilogin
or GoLogin, for people who would rather run it themselves.

Each profile is **one long-lived machine**. It keeps the same fingerprint every
session, along with its own cookies, storage and history, so an account opened
from it in January still looks like the same computer in June.

> **Status:** `v0.3.0`, and in working order. 394 tests, 22 of which drive a real
> browser and check what a page actually sees; 90% coverage. The REST API is
> versioned at `/api/v1` behind a written
> [stability contract](docs/api.md#stability-contract), and every release is
> installed from its own wheel and run before it is published.
>
> Two things to know before you rely on it. `1.0` will remove the fields listed
> as deprecated in that contract. And [Chrome
> migration](extras/chrome_migration/README.md) is a separate, experimental
> extra: its Windows path is written to Chrome's documented format but has never
> been run against a real Windows profile.

![The profiles list](docs/assets/screenshot-profiles.png)

## What it does

- **A profile is the same machine every launch.** Camoufox generates a fresh
  fingerprint on every start, which is right for a privacy tool and wrong for a
  long-lived account. The first launch resolves the fingerprint once and stores
  it; every launch after replays it. Location, timezone and WebRTC stay dynamic
  so they keep following the proxy. See
  [docs/profile-settings.md](docs/profile-settings.md).
- **Real device fingerprints.** Create a profile from one of the 312 fingerprints
  Camoufox captured from actual machines, instead of a synthetic one.
- **Profiles and groups** — create, edit, clone, delete, search, filter, bulk
  actions, and grouping by client or purpose. A clone gets its own machine by
  default: two profiles sharing one fingerprint are provably one computer.
- **Browser control** — launch and stop a browser per profile; closing the window
  yourself is noticed and the session is cleaned up.
- **Scheduling** — open a profile on a schedule (warming), and keep its pinned
  browser version current automatically. Hardware never rotates on a timer, on
  purpose — [docs/scheduling.md](docs/scheduling.md).
- **Proxies, checked before you trust them.** HTTP, HTTPS and SOCKS, with
  passwords encrypted at rest when `CPM_SECRET_KEY` is set. *Check proxy* reports
  where the proxy really comes out and whether the profile agrees with it — a
  timezone on a different clock from the exit country is the kind of
  contradiction a page can measure in two lines of JavaScript.
- **Multi-user, when you need it.** Nothing to configure for the usual case: the
  app binds to loopback and is open. Set `CPM_API_KEY` for machine clients, or
  create an account (`camoufox-pm user add`) and humans get a login screen —
  argon2id hashes, HttpOnly session cookies, a logout that really invalidates.
- **Move a profile anywhere.** Export a profile with its fingerprint *and* its
  browser data into one archive, and import it on another machine.
- **Bulk editing via Excel** — [docs/excel.md](docs/excel.md).
- **REST API and web UI**, served on one port from one process.
- **Optional Chrome migration** — import cookies and history from your own Chrome
  profiles ([`extras/chrome_migration`](extras/chrome_migration/README.md)).

## What it looks like

<table>
<tr>
<td width="50%"><img src="docs/assets/screenshot-machine.png" alt="The pinned machine on a profile"></td>
<td width="50%">

**The pinned machine.** This is the part that makes a profile an account rather
than a browser window. Every value here was resolved once and is replayed on
every launch — the screen, the CPU count, the GPU string, the font set, the user
agent. *Regenerate* moves the profile to different hardware, deliberately, and
tells you what that costs.

The profile above is pinned to one of the 312 devices Camoufox captured from real
machines, so the combination is one that actually exists rather than an assembly
of plausible parts.

</td>
</tr>
</table>

| Scheduled work | System settings |
| --- | --- |
| ![Schedules](docs/assets/screenshot-schedules.png) | ![Settings](docs/assets/screenshot-settings.png) |
| Warming launches and browser-version refreshes. Hardware never rotates on a timer — [why](docs/scheduling.md). | Reports the effective configuration and warns when proxy passwords are unencrypted or the API is open. |

## Install and run

You need [uv](https://docs.astral.sh/uv/) and Python 3.10+. Node.js 20.9+ is only
needed if you build the web UI yourself.

### From a release (no Node.js)

Each [release](https://github.com/polyackiy/camoufox-profile-manager/releases)
ships a wheel with the UI already built in.

```bash
pip install <wheel-url-from-releases>
camoufox fetch     # downloads the browser, ~300 MB, first run only
camoufox-pm        # serves the API and UI at http://localhost:8000
```

### From source

```bash
git clone https://github.com/polyackiy/camoufox-profile-manager.git
cd camoufox-profile-manager
uv sync
uv run camoufox fetch
uv run python scripts/build_webui.py    # builds the UI into the package (needs Node)
uv run camoufox-pm
```

`camoufox-pm` opens your browser automatically. The UI is served from the same
origin as the API, so there is no proxy or CORS to configure. Full options in
[docs/cli.md](docs/cli.md).

### As a desktop window

```bash
uv sync --extra desktop
uv run camoufox-pm --desktop
```

Or build a standalone app that needs neither Python nor Node:
`python scripts/build_desktop.py`.

### With Docker

```bash
docker compose up      # then open http://localhost:8000
```

One container serving the API and the UI on one port, like every other way of
running it. The port is published on loopback only. Launching real browsers
inside a container needs a virtual display; profile management, scheduling and
the UI work as-is.

## First steps

1. Open the app and click **New profile**.
2. Pick an operating system, and optionally a **real device** to pin the profile
   to. Leave the fingerprint fields blank and Camoufox generates a consistent set.
3. Add a proxy if you have one. Prefer HTTP/HTTPS for authenticated proxies —
   Firefox cannot authenticate to a SOCKS proxy.
4. Press **Run**. The row shows the profile as running until you stop it or close
   the browser yourself.

The **Settings** screen reports how the instance is configured and warns if proxy
passwords are unencrypted or the API is reachable without a key.

## Configuration

Settings come from environment variables (prefix `CPM_`). Copy `.env.example` to
`.env` and edit as needed.

| Variable           | Default                 | Description                                     |
| ------------------ | ----------------------- | ----------------------------------------------- |
| `CPM_HOST`         | `127.0.0.1`             | Bind address                                    |
| `CPM_PORT`         | `8000`                  | Port                                            |
| `CPM_DB_PATH`      | `data/profiles.db`      | SQLite database path                            |
| `CPM_SECRET_KEY`   | *(empty)*               | Fernet key; encrypts proxy passwords at rest    |
| `CPM_API_KEY`      | *(empty)*               | If set, required as the `X-API-Key` header (machine clients) |
| `CPM_SESSION_TTL_HOURS` | `168`              | Login session lifetime, in hours                |
| `CPM_SECURE_COOKIES` | `0`                   | Force the session cookie's `Secure` flag (set behind a TLS proxy) |
| `CPM_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins                 |
| `CPM_WEBUI_DIR`    | *(auto)*                | Override where the bundled web UI is served from |

Generate an encryption key with:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

User accounts for the web UI are managed from the CLI: `camoufox-pm user add
<name>` creates one, and from then on the API and UI require a login (see
[SECURITY.md](SECURITY.md#authentication)).

## Documentation

| Document | What it covers |
| -------- | -------------- |
| [docs/cli.md](docs/cli.md) | Every command and flag |
| [docs/api.md](docs/api.md) | The REST API, endpoint by endpoint |
| [docs/profile-settings.md](docs/profile-settings.md) | What each setting does, how the pinned machine works, and what Camoufox cannot do |
| [docs/scheduling.md](docs/scheduling.md) | Scheduled launches and browser refresh, and why hardware rotation is not offered |
| [docs/excel.md](docs/excel.md) | Bulk import and export |
| [docs/accessibility-roadmap.md](docs/accessibility-roadmap.md) | Plan for reaching non-technical users |
| [docs/releasing.md](docs/releasing.md) | Cutting a release |
| [docs/signing.md](docs/signing.md) | Code-signing the desktop builds |
| [docs/roadmap.md](docs/roadmap.md) | What is planned |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Working on the project |
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability |

## Measured, not assumed

Every claim above was checked against a running browser, and several of them
turned out differently than expected. The measurements are in the repository, as
tests that fail if the behaviour changes:

| What was measured | Result |
| --- | --- |
| Does an unpinned profile keep its hardware between launches? | **No.** The same profile reported 1600x900 then 3440x1440, 12 then 32 CPU cores, an NVIDIA then an AMD GPU. This is why pinning exists. |
| Is the canvas stable for a site across sessions? | **No, by default** — it is stable within a session and per site, and changes on the next launch. `stable_canvas` fixes that, at the cost of being identical across sites. Both are asserted in [browser tests](tests/browser/test_fingerprint_stability.py). |
| Does Camoufox's `canvas:seed` property work? | **No.** It is declared and passed through, and nothing reads it. Reported upstream as [daijro/camoufox#721](https://github.com/daijro/camoufox/issues/721) with a reproduction. |
| What happens if a profile sets coordinates? | Camoufox's IP lookup switches off, and Firefox then reports **the host machine's own timezone** — a real leak, found by reading the clock in a live browser. Fixed by filling that in ourselves. |

The same discipline applies to the code: cleanup that could delete every profile
directory, clones that shared a fingerprint with their source, and a release that
reported the wrong version were all found by running the thing, not by reading
it. See [CHANGELOG.md](CHANGELOG.md).

## Architecture

```
src/camoufox_pm/
├── main.py             FastAPI app; serves the API and the bundled UI
├── cli.py              the camoufox-pm command, including user accounts
├── config.py           environment-based settings
├── core/
│   ├── models.py            profiles, groups, browser settings, schedules
│   ├── database.py          SQLite storage and migrations
│   ├── profile_manager.py   profile lifecycle and browser control
│   ├── browser_session.py   running browsers
│   ├── fingerprint_store.py pinning, refreshing, and the real device presets
│   ├── fingerprint_generator.py  the high-level constraints a new profile starts from
│   ├── proxy_check.py       where a proxy exits, and whether the profile agrees
│   ├── scheduler.py         scheduled launches and browser refreshes
│   ├── profile_archive.py   whole-profile export and import
│   ├── excel_manager.py     bulk import and export
│   ├── auth.py              password hashing and login sessions
│   ├── crypto.py            proxy-password encryption at rest
│   └── cleanup.py           finding and removing orphaned profile directories
└── api/                routes, models, error shape, middleware
web/                    Next.js web interface
extras/chrome_migration/  optional Chrome → Camoufox migration
```

**Backend:** Python, FastAPI, SQLite, Camoufox + Playwright.
**Frontend:** Next.js 16, React 19, TypeScript, Tailwind v4.

## Development

```bash
uv sync --extra dev
uv run ruff check src tests
uv run mypy src/camoufox_pm
uv run pytest -m "not browser"    # fast suite
uv run pytest -m browser          # launches a real browser, needs `camoufox fetch`
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Disclaimer

This tool is for lawful use only — for example testing, privacy, and managing
multiple accounts in accordance with each site's terms of service. You are
responsible for how you use it. Antidetect browsing does not make any activity
that would otherwise be against a service's rules acceptable.

## License

[MIT](LICENSE) © Camoufox Profile Manager Contributors.

Built on [Camoufox](https://github.com/daijro/camoufox) by daijro.

---

Русская версия: [README.ru.md](README.ru.md).
