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
| Windows  | ✅ `v10`/`v11`, ⚠️ `v20` needs elevation | See App-Bound Encryption below. |

### Windows App-Bound Encryption (ABE)

Starting with **Chrome 127 (2024)**, Windows writes new cookies with
[App-Bound Encryption](https://security.googleblog.com/2024/07/improving-security-of-chrome-cookies-on.html)
(a `v20` prefix). The key no longer lives under the classic per-user DPAPI blob:
it is in `Local State` under `os_crypt.app_bound_encrypted_key`, wrapped by
**two** DPAPI layers (an outer SYSTEM-context layer, an inner user-context layer)
and an inner AEAD wrap performed by Chrome's SYSTEM-level elevation service. This
is Google's anti-infostealer measure, and unwrapping the outer SYSTEM layer needs
SYSTEM-level access — a normal user process cannot do it.

**What this module does:**

- **`v10`/`v11`** cookies: decrypted as before.
- **`v20`** cookies, when the tool runs **as Administrator on the same Windows
  machine** that wrote them: decrypted using the documented offline unwrap chain
  (SYSTEM DPAPI via `lsass` impersonation → user DPAPI → the elevation service's
  AEAD key → AES-256-GCM). Requires the `chrome-migration` extra, which pulls in
  `pywin32` on Windows.
- **`v20`** cookies in any other situation (not elevated, a different machine,
  non-Windows, or a machine-bound `flag 3` key): **skipped, never guessed.** You
  will see one warning explaining why, and those cookies are simply not migrated
  rather than written as garbage.

**If a cookie cannot be decrypted:** re-run the migration from an elevated
(Administrator) prompt on the machine where the Chrome profile lives, with Chrome
closed. If it still cannot be read, the profile most likely uses a machine-bound
key variant that cannot be recovered off that exact machine; migrate what you can
and re-authenticate the rest in Camoufox.

> **Scope note.** Full `v20` recovery is inherently Windows- and
> elevation-bound; the crypto is implemented and unit-tested cross-platform with
> synthetic fixtures, but the Windows DPAPI/CNG syscalls can only run on an
> elevated Windows host. See `abe.py` and `abe_windows.py`.

## Legal

You are responsible for complying with applicable laws and the terms of service of
any site whose cookies you migrate. See the project [SECURITY.md](../../SECURITY.md)
and [LICENSE](../../LICENSE).

## How cookies are encrypted

The `v10`/`v11` prefix on a cookie value does **not** name a cipher — it means
something different depending on the platform that wrote it:

| Platform | Cipher | Layout | Key |
| --- | --- | --- | --- |
| Windows | AES-256-GCM | `[v10\|v11][nonce:12][ciphertext][tag:16]` | DPAPI-unwrapped `os_crypt.encrypted_key` |
| macOS, Linux | AES-128-CBC, IV of 16 spaces, PKCS#7 | `[v10\|v11][ciphertext]` | PBKDF2 over the Keychain/keyring password |
| Windows, Chrome 127+ | AES-256-GCM (App-Bound) | `[v20][nonce:12][ciphertext][tag:16]` | see App-Bound Encryption below |

On macOS and Linux `v11` differs from `v10` only in where the password comes
from, never in the cipher. A value that cannot be decrypted is skipped, never
written mangled: CBC has no authentication tag, so the PKCS#7 padding check and
a strict UTF-8 decode are the only integrity signals available.
