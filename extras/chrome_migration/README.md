# Chrome migration (optional, experimental)

This optional module migrates data from **your own** local Google Chrome profiles
into Camoufox profiles: cookies (decrypted), history, and bookmarks.

> **⚠️ Use it only on Chrome profiles you own.** It reads and decrypts local Chrome
> data using your operating system's own key store (macOS Keychain, Windows DPAPI).
> Do not use it on data that is not yours.

> **Experimental.** This module is not part of the core package's stability
> guarantees and is not exercised by the main CI job.

## Install

The module ships as an optional extra:

```bash
uv sync --extra chrome-migration
```

## Usage

Interactive wizard:

```bash
uv run python -m extras.chrome_migration.wizard
```

Or use the pieces directly:

- `importer.py` — `ChromeProfileImporter`: discover Chrome profiles, export/convert cookies.
- `cookie_decryptor.py` — `ChromeCookieDecryptor`: decrypt Chrome cookies per-OS.
- `migration_manager.py` — `ChromeMigrationManager`: orchestrate a full migration.

## Platform support

| Platform | Cookie decryption | Notes |
| -------- | ----------------- | ----- |
| macOS    | ✅ Keychain       | Works on current Chrome. |
| Linux    | ✅ Default key    | Works with the standard `peanuts` key. |
| Windows  | ⚠️ Limited        | See App-Bound Encryption below. |

### Windows App-Bound Encryption (ABE)

Starting with **Chrome 127 (2024)**, Windows encrypts cookies with
[App-Bound Encryption](https://security.googleblog.com/2024/07/improving-security-of-chrome-cookies-on.html),
which ties the key to the browser application. The classic DPAPI path this module
uses no longer decrypts cookies written by recent Chrome versions on Windows.
macOS and Linux are unaffected. Contributions to support ABE are welcome.

## Legal

You are responsible for complying with applicable laws and the terms of service of
any site whose cookies you migrate. See the project [SECURITY.md](../../SECURITY.md)
and [LICENSE](../../LICENSE).
