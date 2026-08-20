# Contributing

Thanks for your interest in improving Camoufox Profile Manager! This document
describes how to set up a development environment and the conventions we follow.

## Development setup

The backend uses [uv](https://docs.astral.sh/uv/) and targets Python 3.10+.

```bash
# Install dependencies (including dev and the optional Chrome-migration extra)
uv sync --extra dev --extra chrome-migration

# Download the Camoufox browser binary (needed for browser tests)
uv run camoufox fetch
```

The frontend is a Next.js app in `web/`. It needs Node.js 20.19+ — ESLint 10's
floor; building the bundle alone works from 20.9:

```bash
cd web
npm install
npm run dev
```

## Running checks

Please make sure the following pass before opening a pull request:

```bash
# Backend
uv run ruff check src tests        # lint
uv run ruff format --check src tests  # formatting
uv run mypy src/camoufox_pm        # type-check
uv run pytest -m "not browser"     # fast tests (no browser)
uv run pytest -m browser           # opt-in: real Camoufox launch

# Frontend
cd web && npm run lint && npm run build
```

CI runs the same checks on Python 3.10–3.13.

To work on the single-process mode (`camoufox-pm`), build the UI into the package
first — otherwise the server has nothing to serve:

```bash
uv run python scripts/build_webui.py
```

## Testing conventions

- **`-m browser` means the test needs the browser binary**, not merely that it is
  slow. Anything that launches Camoufox *or* resolves a fingerprint belongs
  there; CI does not run `camoufox fetch`, so an unmarked test that needs it will
  fail there while passing on your machine.
- **Check the no-browser path too.** Much of the app must work before
  `camoufox fetch` has run. The quickest way to see what CI sees:

  ```bash
  HOME=$(mktemp -d) uv run pytest -m "not browser"
  ```

- **The browser suite runs offline. Keep it that way.** It used to load
  `example.com` and `iana.org`, and launching asked the internet for this
  machine's public address, so a run could fail on a rate limit and look like a
  product bug — one full run failed three tests and passed all twenty on the next
  attempt with nothing changed. Now the pages come from a loopback server
  ([tests/browser/support.py](tests/browser/support.py)) and the exit address is
  named instead of looked up. A new test that reaches the internet brings that
  back; if one truly needs to, say why in the test. The claim is a command
  rather than a promise:

  ```bash
  uv run pytest -m browser --no-network   # refuses any connection off this machine
  ```

  Two things to know about its reach. It guards the *test* process; the browser
  is a child of its own, and what keeps its page loads local is that the tests
  only ever hand it loopback URLs. And it will refuse the one-off downloads a
  cold machine still needs — Camoufox fetches uBlock Origin into its addon cache
  the first time anything launches, alongside `camoufox fetch` for the browser
  itself. Both are once per machine, not per run; warm them first, as you would
  before running this suite at all.
- **Prefer tests that observe real behaviour.** The fingerprint tests launch a
  browser and read what a page would actually see, rather than asserting on the
  options passed in. When a test guards a premise about Camoufox's behaviour, say
  so — `test_unpinned_profiles_look_like_new_hardware` exists to fail loudly if
  upstream ever changes.
- **A test should be able to fail.** Coverage is a way of finding untested
  behaviour, not a target: a test that asserts a getter returns what was set
  costs time on every run and catches nothing. Name the behaviour in the test
  name, and when the test exists because something once broke, say in a docstring
  what broke.

### What is deliberately not covered

Two modules are left untested on purpose, so nobody spends an afternoon
rediscovering why:

- **`desktop.py`** composes uvicorn and pywebview and has no logic of its own
  beyond a connect-retry loop. pywebview is an optional extra that CI does not
  install, so a test would have to stand in for every call it makes and would
  then only assert that the stand-ins were called in order. The thing that can
  actually break here — the window opening at all — is not observable without a
  display.
- **The FastAPI lifespan in `main.py`** builds the storage and profile manager
  from the settings on startup. The integration tests inject their own instead,
  which is what lets each of them have a throwaway database.

The remaining uncovered lines are mostly `except Exception -> 500` handlers,
which exist for failures that cannot be provoked without breaking SQLite
underneath the process, and `fingerprint_store.resolve()`, which needs the
browser binary and is covered by the `browser`-marked tests.

## Project layout

- `src/camoufox_pm/` — the Python package (core logic, REST API).
- `web/` — the Next.js web interface.
- `extras/chrome_migration/` — optional Chrome → Camoufox migration module.
- `tests/` — unit, integration, and opt-in browser tests.
- `scripts/`, `examples/` — utilities and runnable examples.

## Coding conventions

- **Style:** [ruff](https://docs.astral.sh/ruff/) handles linting and formatting.
- **Types:** new code should type-check under mypy.
- **Language:** all code, comments, docstrings, and UI text are in English.
- **Commits:** follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, …).

## Pull requests

1. Fork the repository and create a feature branch.
2. Make your change with tests where it makes sense.
3. Run the checks above.
4. Open a pull request describing the change and its motivation.

## Reporting bugs

Open an issue using the bug-report template. Include the version, your OS, and
steps to reproduce.
