# Safe operation diagnostics

Synchronization runtime failures and skipped administrator backups can carry a
versioned `diagnostic` object. It describes an operation stage and failure family
without exporting exception text or private application state. It does not prove
the underlying cause of an intermittent failure.

A synchronization error retains its existing HTTP 409 status, generic message and
`Cache-Control: no-store` / `Pragma: no-cache` headers. Its metadata appears in
`error.details[0].diagnostic`, for example:

```json
{
  "version": 1,
  "area": "sync",
  "stage": "execute",
  "category": "invalid_state",
  "site": "core.profile_operations",
  "line": 64
}
```

Backup skips add an optional `diagnostic` alongside the existing `profile_id` and
`reason`. A missing profile uses `profile_lookup` / `missing_profile`; failures
while creating an archive use `backup_profile`. Existing backup reasons remain
local administrator messages and can include private paths. When reporting a
problem, copy the **diagnostic object**, not the complete response or private log.

## Contract and privacy

- Areas, stages, categories and sites have closed vocabularies defined in
  `core/operation_diagnostics.py`. Version 1 must be explicitly present.
- A site is the deepest allowlisted frame among at most 64 inspected frames. Its
  globals must belong to the actually loaded module; a user script cannot claim
  a site merely by supplying that module's name. It is not a full stack trace.
- Sanitizing `ControlError` / `ControlConflict` / `SyncBlocked` wrappers may carry
  a frozen diagnostic captured before replacing an internal error. Only the actual
  loaded wrapper classes can forward it; the closed model is revalidated, and the
  caller retains its area/stage. No exception cause chain is traversed or copied into the diagnostic.
  This preserves a control-store origin through an outer profile safety fence.
- A line is an integer from 1 through 10000 and requires an allowlisted site.
  Unknown modules have no exported path, function name or line.
- Formatting does not read exception arguments, messages, repr, causes, traceback
  locals, credentials, URLs, profile/user identifiers or file contents. Exception
  types and selected integer OS error numbers select fixed categories; raw
  error numbers are not exported and unknown types become `internal`.
- Formatting imports no optional synchronization modules, initializes no keys or
  synchronization state, and creates no persistent error store.
- Live-owner cookie and administrator checks still run before these operations.
  A failed sync operation rechecks its live owner before returning metadata. If
  that check is denied or cannot complete, no diagnostic is returned.
  Authentication/authorization denials retain their existing response; error
  metadata grants no authority and never authorizes a retry.

## Installed acceptance

The synchronization harness exercises a missing-profile operation using the real
installed manager and verifies that existing profiles are unchanged. The proxy
harness verifies a missing-profile backup skip, then still requires normal
backups before every accepted browser scenario. Synchronization observations also
finish an additive public backup before their one-shot browser launch, keeping
the normal due-backup scheduler enabled without racing fresh/imported profiles.
The same shared preparation code permits only explicit additive-backup busy
retries; launch and handoff requests are never replayed. Reports include fixed
`backup_checks` stages and a `profile_busy` classification without holder identities.
The synthetic CI relay retains at most eight HTTP 409 diagnostics from its
allowlisted `sync.relay` frames in `relay_refusals`. Its actual API response is
unchanged; request bodies, URLs, identifiers and private exception details are
not collected. `relay_storage_checks` records at most eight fixed codes for
metadata/EOF discrepancies or restarted uncommitted prefixes; no file metadata,
paths, lengths or bytes are exported. Observers wrap only this harness's synthetic
relay and restore its helpers on exit. This is harness evidence, not an
authenticated relay proof.

Resumable upload acknowledgements follow flush/fsync, close, and private-file
readback of the chunk. Open-handle EOF and counted, hash-verified bytes establish
length independently of cached stat size. Only the authenticated original writer
can restart a missing/short uncommitted prefix under the relay's writer transaction;
ready/committed blobs, live legacy writers, expiry, quotas, ciphertext identity,
nonce, membership and checkout checks retain their existing boundaries. Restart
resends the original immutable ciphertext; no browser/handoff/workflow action is
replayed. The OS-level cause of the observed Windows prefix mismatch remains
unestablished; these checks make loss detectable and incomplete staging recoverable.
These deliberate failures do not
replace successful round-trip, browser-session, TLS, workflow or cleanup checks.

CI consumers accept only plain JSON objects matching the closed schema and the
expected area. Unknown versions, fields, sites and malformed line numbers are
rejected. Legacy errors remain supported without metadata. Backup retry rules
are unchanged even if a diagnostic says `profile_busy` or `resource_busy`.

Include the manager version when reporting a diagnostic; CI reports also record
the exact Actions revision. Reports may include `diagnostic_check`, `operation_diagnostic`, or
`backup_preparation.operation_diagnostic`; arbitrary API bodies and private
installed-process logs are not exported. Same-host/same-account CI remains
distinct from physical/enterprise acceptance and external protocol review.
Experimental synchronization stays explicitly opt-in.


## Control-store initialization

First database creation and constructor schema inspection share a private
`control.init.lock` file. Each opener owns a distinct handle; a bounded ten-second
OS-lock acquisition precedes SQLite initialization. The lock inode stays in place
and contains no owner identity, key, lease timestamp or captured event. The OS
releases ownership after process exit; explicit teardown closes/unlocks handles.
This prevents a second constructor from inspecting another creator's unfinished
zero-byte database. Existing empty/corrupt stores are still rejected, and unsafe
lock paths/permissions/links never grant admission. Application database/profile
leases, synchronization records and SQLite journal validation are unchanged.

The native lock behavior follows the [Python CRT locking contract](https://docs.python.org/3.13/library/msvcrt.html)
and [Windows byte-range locking rules](https://learn.microsoft.com/en-us/windows/win32/fileio/locking-and-unlocking-byte-ranges-in-files).
Native tests exercise separate handles, timeout cleanup and process interruption;
these tests do not replace physical/enterprise compatibility acceptance.
