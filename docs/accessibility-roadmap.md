# Accessibility roadmap: reaching non-technical users

Today the project is developer-facing: it assumes a terminal, Python + uv,
Node.js, manual `camoufox fetch`, and running two servers by hand. This document
is a plan to progressively lower that barrier so people **without a technical
background** can install and use it.

It is organized by audience, from "comfortable with a terminal" to "double-click
an app". Each level is independently shippable and raises the ceiling of who can
use the tool.

## Current barriers (baseline)

| Barrier | Impact on a non-technical user |
| ------- | ------------------------------ |
| Install uv, Python 3.10+, Node.js 20+ | Blocker — they won't know what these are |
| Run the API and the web app as two separate processes | Blocker — two terminals, correct order |
| `camoufox fetch` as a manual step | Confusing — silent failure if skipped |
| Ports, `.env`, `API_PROXY_TARGET`, CORS | Blocker — env editing is not approachable |
| No installer / binary — clone + build from source | Blocker |
| The browser binary is large (~300 MB) and fetched separately | Slow first run, easy to misconfigure |

The north star: **one download, one click, it just works — no terminal.**

---

## Level 1 — One command (semi-technical users)

**Audience:** people comfortable copy-pasting one command; power users.
**Deliverable:** `docker compose up` starts everything.

- Add a `Dockerfile` for the backend that fetches the Camoufox binary at build time.
- Add a `docker-compose.yml` that runs the API and serves the built web UI on one
  port (e.g. http://localhost:8000), with a persistent volume for the database.
- Document a single quick-start: install Docker, run `docker compose up`, open the
  printed URL.

**Effort:** small. **Impact:** removes the Python/Node/uv toolchain entirely for
anyone who has Docker. This is the highest ROI first step.

**Open questions:** running a real browser inside a container needs Xvfb / the
`headless="virtual"` Camoufox mode on Linux; document that launching browsers may
require the host (or an X server) depending on the platform.

---

## Level 2 — One process, no separate web server (developers → prosumers) ✅ shipped

**Audience:** anyone who can run a single command or launcher script.
**Deliverable:** the app is a single service that serves both the API and the UI.

Status: the `camoufox-pm` console script runs the API and a Next.js **static
export** on one port from FastAPI (same origin — no proxy/CORS). Build the UI into
the package with `python scripts/build_webui.py`. Remaining follow-up: bundle the
built UI into the published wheel so `pip install` needs no Node.js (see below).

- Build the web UI as static export (or a bundled build) and serve it from FastAPI
  under `/`, so there is only **one** server on one port.
- Add a console entry point (`camoufox-pm`) in `pyproject.toml` that starts the
  server and opens the default browser automatically.
- Ship the frontend build inside the Python package so `pip install` + one command
  works without Node.js on the user's machine.
- First-run experience: auto-create the data directory, prompt to run
  `camoufox fetch` (or trigger it automatically) with a progress bar.

**Effort:** medium (build pipeline to embed the frontend; CI to produce the bundle).
**Impact:** removes Node.js and the two-process dance for end users.

---

## Level 3 — Double-click desktop app (non-technical users) 🚧 foundation shipped

**Audience:** no terminal, no Docker — just an installer.
**Deliverable:** signed installers for macOS (`.dmg`), Windows (`.exe`), Linux
(`.AppImage`).

Status: the foundation is in place and verified on macOS.

- **Native window** — `camoufox-pm --desktop` runs the server and opens a native
  window (pywebview; the `desktop` extra).
- **Standalone binary** — `scripts/build_desktop.py` + `packaging/camoufox-pm.spec`
  produce a PyInstaller bundle (and a macOS `.app`) that runs with no Python or
  Node installed. The macOS `.app` is verified to serve the UI and API.
- **CI** — `.github/workflows/desktop.yml` builds macOS/Windows/Linux bundles on
  demand and can attach them to a release.

Remaining (need maintainer resources, so not automated here):
- **Code signing & notarization** — Apple Developer ID + notarization for macOS,
  an Authenticode certificate for Windows; without these the OS warns users.
- **Installers** — wrap the bundles as `.dmg` / `.exe` (NSIS) / `.AppImage`, and
  verify the Windows/Linux builds on real hardware.
- **Auto-update** — Sparkle (macOS) / a Windows updater, or a simple version check.
- **Camoufox binary** — fetch it on first run with a visible progress UI (it is
  intentionally not bundled).

Two viable approaches:

- **PyWebView / pywebview + PyInstaller:** wrap the FastAPI server + a native
  webview window; package with PyInstaller. Lightest, reuses the existing stack.
- **Tauri:** a Rust shell hosting the web UI, talking to a bundled backend. Smaller
  binaries and good auto-update, but adds a Rust toolchain to the build.

Requirements for this level:
- Bundle or first-run-download the Camoufox binary with a visible progress UI.
- A friendly first-run wizard: pick a data folder, optional proxy, done.
- Auto-update (Sparkle / Tauri updater / a simple "new version" check).
- Code signing / notarization so the OS doesn't warn users.
- Release automation: GitHub Actions builds all three installers on tag and
  attaches them to the Release.

**Effort:** large. **Impact:** the actual "non-technical" unlock — users download
one file and run it.

---

## Cross-cutting improvements (help at every level)

- **Onboarding & empty states:** a first-run screen that explains what a profile
  is, with a "Create your first profile" call to action and sensible defaults.
- **In-app guidance:** tooltips for fingerprint fields (what OS/timezone/WebRTC do),
  a "Test fingerprint" button that opens a check page (e.g. a bot-detection test).
- **No env editing:** move all settings into a Settings screen in the UI, backed by
  the database, instead of environment variables.
- **Localization (i18n):** the codebase is English-only today; add a language
  switcher (start with the languages of the core audience).
- **Docs for non-developers:** a "Getting Started" page with screenshots and a short
  screen-recording, separate from the developer `CONTRIBUTING` docs.
- **Safer defaults:** encrypted proxy storage on by default (generate a key on first
  run), localhost binding, clear warnings before exposing the app to a network.
- **Guardrails:** friendly error messages (e.g. "Camoufox browser not found — click
  to download") instead of stack traces.

---

## Suggested order

1. **Level 1 (Docker Compose)** — quick, unblocks power users immediately.
2. **Onboarding/empty-state + in-app Settings** — improves everyone's experience and
   is a prerequisite for a good desktop app.
3. **Level 2 (single bundled service + `camoufox-pm` launcher)**.
4. **Level 3 (desktop installers + auto-update)** — the largest effort, gated on the
   single-service work from Level 2.

Each step is worth shipping on its own; none blocks the current developer workflow.
