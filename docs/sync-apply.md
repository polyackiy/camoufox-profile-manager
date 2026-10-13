# Journalled activation of a synchronized profile

`sync.apply.apply_snapshot` applies an already authenticated, selected snapshot to
an existing local profile. It preserves the local profile ID, group, grants,
schedules, creation history and other local authority. Only the snapshot's
validated portable fields (name, tags, notes, browser settings, proxy and pinned
fingerprint) change. A changed proxy clears its old local connectivity result.
Local extensions currently block activation because portable extension capability
mapping has not been established.

The caller must authenticate the selected manifest/ciphertext and decrypt into
private local storage first. The adapter then independently validates the portable
archive and exact compatibility target. Neither the revision UUID argument nor an
archive path proves authenticity. The synchronization engine must persist its own
apply-pending state before entering and mark the revision applied only after the
adapter returns a complete result and the installed pointer is verified.

## Atomic pointer publication

Each attempt receives a fresh operation UUID. The validated browser directory is
created as `profiles/sync-<operation UUID>/data`. It is never copied over the live
local directory. The adapter holds the existing installation maintenance admission
operation, a renewed exclusive profile lease, and **both** the old and new native
Firefox profile locks through the database compare-and-swap.

Publication changes the existing profile row's `storage_path` and portable fields
in one version-checked database update. The old directory remains intact. This is
an atomic directory **pointer** change: renaming directories while holding an
exclusive Windows `parent.lock` would either fail or require releasing protection.
Keeping both directories at their stable names avoids that unlock window on
Windows and preserves the corresponding POSIX lock inodes. Inventory checks never
reopen the held lock inode, because closing another descriptor for a POSIX record
lock can release the process's lock.

Native browser locks cooperate with Firefox/Camoufox. They do not prevent a hostile
process running as the local OS account from editing files directly or launching
another copied profile. The adapter independently checks known browser processes
and fails closed for ambiguous browser state through the shared quiescence module.

## Private recovery copy and public journal

Before publication, a private local backup ZIP stores the previous browser data,
full previous local profile metadata, intended target metadata and validated
snapshot inventory. Credentials stay inside that protected archive. The journal
contains only operation/profile/revision IDs, expected row version, phases and
integrity digests. It lives in the independent control database outside ordinary
application-data rollback.

The archive is fsynced before its hash is committed to the journal. Extracted
snapshot files and their directories are fsynced before the database pointer
changes. The pointer update must use the storage layer's durable FULL-synchronous
CAS operation. Each subsequent journal transition uses the control store's FULL
transaction durability.

| Durable phase | Interpretation after interruption |
| --- | --- |
| `preparing` | No publication permitted yet; old row must still match |
| `prepared` | Private backup and verified staging are durable |
| `publishing` | CAS may or may not have committed; inspect exact row and pointer |
| `complete` | Target row/digest/pointer were reread successfully after CAS |
| `aborted` | Old row was verified unchanged; attempt did not activate |

The engine must block launch for unresolved phases. An aborted attempt does not
itself grant distributed ownership or prove freshness. A complete journal alone
also cannot prove that the application database survived a later restore: the
engine must compare the current storage pointer with the journal's derived target
path. Ordinary profile-data rollback must never erase this control evidence.

## Recovery decisions

`recover_apply` runs under the same maintenance admission and profile lease.
If an interrupted attempt's complete old-row digest still matches, it marks the
attempt aborted while retaining old data and quarantined staging. If the target
row, expected next version, derived pointer and metadata digest match a publishing
attempt, recovery authenticates the private backup's hash, reacquires both native
locks, rechecks the complete staged inventory, then finalizes the journal.

An unrelated user edit, missing/corrupt backup, changed staging, unknown journal
format, database rollback or mismatched pointer leaves recovery blocked. It never
replaces a later user edit with an old row, guesses a winner, or deletes either
copy. A completed activation can be a valid recovery outcome even when its prior
caller saw an exception: the database commit may have succeeded immediately before
the interruption. The caller must reconcile rather than retry with an assumed old
row version.

Old directories, unused staging and backup ZIPs are intentionally retained for
explicit recovery/retention work. This adapter does not silently prune them or
restore distributed authority from a backup. Restoring an older remote revision
requires the separate owner-authorized history/conflict procedure.

Cancellation drains worker I/O before releasing leases or native locks. Losing
the lease or detecting a stale database row blocks publication. Tests inject an
interruption after each durable step and verify old/new data, same-ID grants and
schedules, native lock retention, maintenance exclusion, backup/staging tampering,
concurrent edits and application-database rollback.
