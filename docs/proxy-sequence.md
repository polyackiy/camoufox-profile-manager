# Assign saved proxy lists in order

In **Proxy library**, choose **Assign proxy list**. Select 1–100 stopped profiles
and the same number of distinct saved proxies. Number badges show selection
order; filtering the lists does not clear or reorder hidden selections. Each
profile receives the proxy in the same position. Remove and reselect an entry
to change its order, then inspect **Preview mapping** before applying it.

The preview shows names, current safe endpoints, proposed endpoints and known
blocks. Confirm replacement when profiles already have a proxy. The manager
never automatically shares a saved entry, chooses a random proxy or shifts
later entries when a profile is skipped. To deliberately share one entry, use
its existing **Assign** action. Separate entries may still have the same endpoint
or exit IP; one entry per profile is not a unique-IP guarantee.

The preview validates saved proxy versions and access to the encryption key,
not network connectivity or final sync authority. Check connections separately
in the library. Previewing creates no profiles or leases and changes no profile
settings. Only administrators may preview or execute assignments.

## Execution and results

The entire saved-proxy selection is validated again before the first write.
Each pair then uses the existing profile-operation lease, local browser check
and device-sync ownership guard. Administrator access is rechecked after lease
acquisition. A targeted database update checks both the original profile version
and original saved-proxy version under the lease. Assignment changes only proxy
settings, clears stale proxy-check results and increments the profile version.
Browser data, fingerprints and unrelated settings are preserved. Proxy passwords
use encrypted storage and never appear in previews or requests.

Running, leased, missing, trashed, changed or sync-blocked profiles are skipped.
Other pairs keep their original mapping. Results list assigned and skipped
profiles with safe recovery messages, and mixed results appear as partial in
the activity log. Successful assignments have individual activity records.
Edits or deletion in the proxy library never change already-assigned copies.

## Interruption and retry

Execution is a sequential HTTP operation, not an atomic whole-batch transaction
or a background scheduler. Closing the dialog or losing a response can leave
some pairs committed and others untouched. Nothing retries automatically.
**Retry original mapping** sends the same pairs and original versions: already
committed or edited profiles fail their version check and are never overwritten
again or reassigned to another position. A repeated response may therefore show
previously assigned profiles as changed/skipped; it does not prove they were
unchanged by the first request. Review current profiles when the outcome is
uncertain. A storage/lease-release error can also leave a row unconfirmed after
its write committed; a generic failure does not prove that row was unchanged.
There is no separate durable assignment receipt/history.

After a reported partial result, close and reopen to review current records
before a new assignment. Do not automatically refresh versions and replay the
old selection. If a saved proxy changed, review its current configuration first.
If its credentials are unavailable, restore the original manager encryption key.
An administrator/session revocation stops further work; committed assignments
remain intact. Experimental device synchronization stays explicitly opt-in.

The API exposes `POST /api/v1/proxies/assignment-preview` and
`POST /api/v1/proxies/assign-sequence` (with matching `/api` aliases). Both accept
`assignments`, an ordered list of `{profile: {id, row_version}, proxy: {id,
row_version}}`. Duplicate profile or proxy references and more than 100 pairs
are rejected. No provider credentials are accepted. Responses use `no-store`.
