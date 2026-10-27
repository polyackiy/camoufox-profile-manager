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
backups before every accepted browser scenario. These deliberate failures do not
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
