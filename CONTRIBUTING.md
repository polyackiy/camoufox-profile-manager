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

The frontend is a Next.js app in `web/`:

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

- **Prefer tests that observe real behaviour.** The fingerprint tests launch a
  browser and read what a page would actually see, rather than asserting on the
  options passed in. When a test guards a premise about Camoufox's behaviour, say
  so — `test_unpinned_profiles_look_like_new_hardware` exists to fail loudly if
  upstream ever changes.

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
