# Command-line reference

## `camoufox-pm`

Runs the API and the web UI as one process on one port, and opens the UI.

```
usage: camoufox-pm [-h] [--host HOST] [--port PORT] [--no-browser] [--desktop]
```

| Flag | Default | What it does |
| ---- | ------- | ------------ |
| `--host HOST` | `127.0.0.1` (or `CPM_HOST`) | Bind address. Anything other than loopback exposes the API to your network — set `CPM_API_KEY` first. |
| `--port PORT` | `8000` (or `CPM_PORT`) | Port to serve on. |
| `--no-browser` | off | Do not open a browser tab on start. Use when running as a service. |
| `--desktop` | off | Open a native window instead of a browser tab. Needs the `desktop` extra. |
| `-h`, `--help` | | Show the built-in help. |

The flags win over the environment, and the settings the app reports (including
on the Settings screen) reflect what it actually bound to.

```bash
camoufox-pm                          # http://localhost:8000, opens a browser
camoufox-pm --port 9000 --no-browser # run headless on another port
camoufox-pm --desktop                # native window
```

To stop it, press `Ctrl+C`. Any browsers it launched are closed with it.

## `camoufox-pm user`

Manages web UI login accounts. **Creating the first account turns login on**:
from then on the API requires a session (or the API key), and the web UI shows
a login screen. Removing the last account turns login back off. Accounts live
in the CLI on purpose — there is no registration endpoint, so creating one
requires shell access to the host, and the first user can be created without a
chicken-and-egg lockout.

```
usage: camoufox-pm user {add,passwd,remove,list} ...
```

| Command | What it does |
| ------- | ------------ |
| `user add <name>` | Create an account. Prompts twice for the password (min 8 characters); it is argon2id-hashed before it touches the database and never appears in argv, the environment, or logs. |
| `user passwd <name>` | Change an account's password. Existing sessions stay valid. |
| `user remove <name>` | Delete an account and its open sessions. Warns when it was the last one. |
| `user list` | List account names and creation times. Never shows hashes. |

`add` and `passwd` take `--password-stdin` to read the password from the first
line of stdin instead of prompting — for provisioning scripts and containers:

```bash
camoufox-pm user add alice                            # interactive
openssl rand -base64 18 | camoufox-pm user add ci-bot --password-stdin
```

The commands work against `CPM_DB_PATH` and take effect immediately, even while
the server is running — the guard checks the database per request.

## `camoufox fetch`

Downloads the Camoufox browser (~300 MB). This comes from Camoufox itself, not
from this project, and is required before any profile can be launched.

```bash
camoufox fetch          # installed release
uv run camoufox fetch   # from source
```

Without it the app still runs: you can create, edit, group, import and export
profiles, and browse the device presets. Launching a browser and pinning a
preset to a device are the two things that need the binary — the Settings screen
says so when it is missing.

## Scripts

These live in `scripts/` and are for working on the project, not for daily use.

| Command | What it does |
| ------- | ------------ |
| `python scripts/build_webui.py` | Builds the Next.js UI as a static export and copies it into the package, so `camoufox-pm` can serve it. Needs Node.js 20+. |
| `python scripts/build_desktop.py` | Builds a standalone desktop bundle with PyInstaller that needs neither Python nor Node. See [accessibility-roadmap.md](accessibility-roadmap.md). |
| `python examples/seed_demo.py` | Creates a handful of demo profiles for a look around. |

## Running from source without the console script

```bash
uv run python -m camoufox_pm.main
```

This starts the API with reload enabled and serves the UI if one has been built.
It reads `CPM_HOST` and `CPM_PORT` and takes no flags — `camoufox-pm` is the
supported entry point.

## Environment variables

Every setting is also an environment variable with the `CPM_` prefix; they are
listed in the [README](../README.md#configuration). A `.env` file in the working
directory is read automatically.

```bash
CPM_PORT=9000 CPM_API_KEY=secret camoufox-pm --no-browser
```
