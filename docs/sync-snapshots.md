# Portable snapshot container

The experimental S3 codec lives in `sync/snapshots.py`. It is a **plaintext private
staging format**, never a relay upload. The engine must encrypt it with the S1
codec and authenticate the selected signed revision before invoking its validator.
The codec alone does not enable synchronization or establish remote freshness.

`capture_snapshot(manager, profile_id, destination, compatibility, prove_closed)`
holds the existing renewable profile-operation lease and drains its worker on
cancellation. The engine must also hold its maintenance/checkout gate and provide
an independent process/OS-lock check through `prove_closed`. The check runs before
and after capture. A missing Firefox lock marker is not sufficient proof. The
low-level `build_snapshot` is internal integration support for a caller already
holding those guarantees; it must not become an unguarded export endpoint.

The container is an uncompressed ZIP with one `snapshot.json` and regular
`data/...` files. Stored entries trade bandwidth for predictable decompression CPU
and disk limits; stream encryption follows afterward. Metadata contains an exact
browser build, OS, architecture, protocol/schema and capabilities tuple, portable
profile fields and a size/SHA-256 inventory. Compatibility must match the trusted,
tested local target exactly. There is no downgrade, migration or cross-platform
fallback. Extension capabilities and local library references currently fail
closed until explicit portable extension mapping is implemented.

Portable profile metadata includes name, tags, notes, browser settings, proxy and
pinned fingerprint. It excludes local IDs, storage paths, groups, users, grants,
schedules, status/history and manager configuration. Fingerprints must satisfy the
existing frozen-hardware allowlist. Encrypted local proxy-password strings are
rejected; source storage must first decrypt them. The destination storage layer
must re-encrypt validated proxy passwords with its own local key. No local
`secret.key`, `.env` or manager database is transported.

Capture rejects unreadable/missing inputs, special files, symbolic links/reparse
points and hard links. It checks file identity and size/timestamps around reads
and rechecks captured files/directories before finishing. Exact root browser lock
artifacts and existing disposable caches are excluded, never used as proof that
the browser stopped. Changes fail the entire capture; no best-effort skipped files.

Validation bounds the ZIP central directory before `ZipFile` allocates entries,
and then checks names and declared totals before creating staging. Limits are
20,000 files/entries, 8 MiB metadata/central directory, under 2 GiB total plaintext,
32 path components, 240 UTF-8 bytes/component and 1,024 bytes/path. ZIP64, compressed
members, ZIP comments, extras and nonregular entries are rejected. Names must be
NFC and portable across Windows/POSIX; duplicate, case-fold, directory-component,
file/parent, traversal, absolute, reserved-device and alternate-stream aliases
fail closed. Bounded streaming verifies every file's actual size, ZIP CRC and
inventory digest. JSON duplicates, excessive nesting, nonfinite numbers, unknown
fields and boolean protocol versions are rejected.

Staging is always a new private directory; files use mode 0600 on POSIX or the
existing verified owner/SYSTEM Windows DACL. Existing destinations are preserved.
Failures remove only newly created staging. Error messages omit source values and
paths. Validation returns metadata plus `staging/data` for S6 journalled apply;
it never modifies a live profile or authorizes a launch. Files are fsynced, but
activation/journal directory durability belongs to the apply engine. Same-user OS
compromise remains outside the filesystem trust boundary, and deletion is not a
promise of secure erasure on SSDs.

`tests/unit/test_sync_snapshots.py` covers round trips, compatibility rejection,
path aliases, special files, compressed entries, metadata authority injection,
secret error redaction, inventory corruption, bounds, source mutations and both
closed-process checks under the lease. Real-browser compatibility and packaged
cross-device acceptance remain separate release gates.
