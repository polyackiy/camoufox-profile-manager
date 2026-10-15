# Sync key storage and offline recovery

The S2 vault adapters store opaque key records. They do not authorize enrollment,
assert checkpoint freshness, or enable synchronization. Callers must authenticate
owner operations and serialize multi-record ceremonies using a durable journal.

## Native storage

`OSVault(owner_id)` accepts only the exact native backend selected by `keyring`:

| Platform | Required backend |
| --- | --- |
| macOS | `keyring.backends.macOS.Keyring` (Keychain) |
| Windows | `keyring.backends.Windows.WinVaultKeyring` (Credential Manager) |
| Linux | `keyring.backends.SecretService.Keyring` (Secret Service) |

Unknown, chained, plaintext and fallback backends fail closed. Missing libraries,
locked stores, dismissed native unlock prompts and native storage errors do not
select another store. Native keychain access may require an OS prompt.

Each owner/slot pair uses a separate credential service name. This avoids the
Windows backend's multi-username compatibility copies retaining obsolete values.
Windows persistence is explicitly local-machine, overriding the library's roaming
enterprise default. OS-vault payloads are bounded at 64 KiB, but a native service
may impose a lower limit; writes then fail, with no fallback or partial splitting.
Authority material and routine device keys belong in separate slots.

Backend behavior was checked against the maintained primary implementations:
[macOS](https://github.com/jaraco/keyring/blob/v25.7.0/keyring/backends/macOS/__init__.py),
[Windows](https://github.com/jaraco/keyring/blob/v25.7.0/keyring/backends/Windows.py),
[Secret Service](https://github.com/jaraco/keyring/blob/v25.7.0/keyring/backends/SecretService.py).

## Explicit encrypted file storage

`EncryptedFileVault(directory, password)` is an explicitly selected alternative
for headless environments. It never follows a failed OS-vault operation
automatically. Its private directory and files use the shared current-user-only
permission checks (protected DACL on Windows, 0700/0600 on POSIX). Symlinks and
reparse paths are rejected. Special files are rejected before reading; POSIX
nonblocking/no-follow opens and descriptor checks also defend replacement races.
The local OS account is trusted; another process fully controlling that account
can access process memory or replace the application and is outside this boundary.

Each slot is independently encrypted with AES-256-GCM, a fresh 16-byte salt and
12-byte nonce. The key is derived using cryptography's public Argon2id API with
**65536 KiB memory, 3 iterations, 1 lane, 32-byte output**. These parameters are
fixed by format version 1, never accepted from a file. The authenticated domain
includes the canonical slot UUID, preventing substitution between slots. Passwords
are 12–1024 Unicode characters and are retained only in memory for the unlocked
object's lifetime; they never appear in persistent records. Length validation is
not a password-strength guarantee.

Files are bounded, versioned JSON envelopes containing only salt, nonce and
ciphertext. Unknown fields, duplicate keys, unexpected versions and noncanonical
base64url fail closed. Writes persist encrypted bytes to a newly private temporary
file, fsync, replace atomically, and fsync the directory on POSIX. Concurrent writes
to one slot are last-writer-wins: callers must lock journal transactions. A failure
after replacement may have committed a complete encrypted record; recovery must
reconcile durable records instead of assuming an exception means no write.
Read/decrypt an existing ceremony record before treating a password as successfully
unlocked: construction alone only validates directory and password shape.

## Recovery package

`seal_recovery(payload)` returns an encrypted package and a separately generated
256-bit random base64url secret. AES-256-GCM authenticates a recovery-specific
domain distinct from vault records. There is no password KDF or attacker-controlled
KDF cost in recovery packages. The opaque payload is 1–65536 bytes; parsed packages
are limited to 90000 bytes and reject duplicate/unknown fields and unsupported
versions. The secret must be retained separately and must never be uploaded to a
relay, stored alongside the package, logged or embedded in a URL.

`open_recovery` authenticates and returns the payload; this alone does not complete
bootstrap verification. The ceremony must verify restored owner identity and
private-key capabilities in a fresh isolated process, with no source vault access,
and keep synchronization disabled until that succeeds. Recovery without an
independent recent checkpoint cannot assert freshness.

Python and native libraries may retain copies of keys in process memory. Deleting
a slot does not cryptographically erase old disk snapshots, keychain backups,
recovery packages or copies previously received by devices. The application must
not promise those forms of erasure or revocation.

## Linux CI acceptance

The `Linux Secret Service acceptance` job runs the native adapter against a real
GNOME Keyring service, following the [keyring headless setup](https://keyring.readthedocs.io/en/stable/#using-keyring-on-headless-linux-systems).
It creates a private D-Bus session and temporary XDG directories, supplies a random
disposable unlock password through stdin, and checks write/read/delete through
normal backend selection. A fresh process with an unreachable session bus must
raise `VaultError`; a fallback or successful write fails the job. Subprocess
deadlines prevent an unlock dialog or unavailable service from hanging CI.
The daemon and temporary keyring are removed afterward. Run this harness only on
a disposable Linux CI runner (`CI=true`), never against a user's desktop session.

This covers the service adapter, not interactive desktop unlock, locked-store
prompt dismissal, distro-specific desktop integration, or packaged application
behavior with every Linux keyring provider. Those acceptance checks remain open.
