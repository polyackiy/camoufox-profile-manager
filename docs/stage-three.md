# Stage three: roles, profile access and activity

Tracking issue: [#77](https://github.com/polyackiy/camoufox-profile-manager/issues/77).
Stage two acceptance is recorded in [stage-two.md](stage-two.md). This stage
controls multiple users of one manager; cross-device synchronization remains a
separate design.

## Getting started

Create the first administrator with `camoufox-pm user add <username>` and log in.
Open **Access** in the sidebar to add a user with an explicit role, select their
profiles, and grant export separately where needed. The Activity tab filters by
actor, action and result, pages through events and exports JSON. Password reset
and role changes invalidate that account's sessions; the user must log in again.

Existing accounts become administrators during the resumable schema 1 → 2
migration. The last administrator cannot be deleted or demoted, including from
the CLI. Keep a second administrator if you need to replace the first. Schema 2
adds roles, grants, activity and durable schedule authority. Older managers refuse
the newer schema: use the matching binary/data checkpoint for rollback, not an
old executable against the upgraded database.

## Permission matrix

| Capability | Administrator | Operator | Viewer |
| --- | --- | --- | --- |
| See profile metadata | All | Explicitly assigned | Explicitly assigned |
| Edit, launch/close, refresh fingerprint, cookies import, tags, Trash | All | Assigned profiles | No |
| Export archive/cookies/Excel | All | Separate export grant | Separate export grant |
| Create/import/duplicate profiles | Yes | No | No |
| Proxy credentials/configuration, group assignment | Yes | No | No |
| Shared proxy/template/extension libraries, saved views, groups | Yes | No | No |
| Backup, system/browser/update settings, user/grant administration | Yes | No | No |
| Activity view/export | Yes | No | No |

A profile grant supplies view access. The account's role adds edit/launch for an
operator. Export is independent and defaults off, including for operators.
Launching exposes the logged-in account to the operator; an export may contain
cookies and proxy credentials. Give either permission deliberately.

Nonadmin profile responses remove proxy credentials, IP-change URLs, storage
paths and sensitive diagnostics. Editing a redacted profile preserves its proxy;
only an administrator can change the proxy destination or credentials. Groups are
organizational, never an implicit grant. Visible groups/counts derive from the
user's assigned profiles. Global libraries and shared saved views are admin-only.
The API permits granted operators to manage profile schedules and assigned
extensions; the first nonadmin web UI concentrates on profile operation and shows
assigned extensions read-only.

## Enforcement and revocation

Both `/api` and `/api/v1` enforce the same policy. Collections, active sessions,
Trash and counts are filtered before pagination. Hidden and missing objects use
the same nonadmin response. Bulk operations check every selected object before
mutation and record individual successes. Excel contains only exportable profiles.
Downloads recheck roles, grants and session validity before each chunk; revocation
interrupts the remaining transfer. Bytes already delivered cannot be recalled.

Schedules retain actor kind, username and immutable account ID. The scheduler
checks current permissions before execution and timed close; revoked jobs are
refused and execution disables the schedule. Deleting and recreating the same
username does not restore old authority. Pre-upgrade schedules retain trusted
system authority. New schedules cannot supply their own actor through the API.

The public health probe returns no profile count when authentication is configured.
Administrator recovery controls retain read-only authentication during offline
maintenance; they do not write to the database being restored.

## Activity records

Events retain actor kind/name, fixed action, object ID, result and UTC time. Grant
records identify both recipient and profile. Passwords, cookies, tokens, proxy
URLs, request bodies and profile contents are excluded. User/grant changes write
the event in the storage transaction; API operation outcomes are recorded after
the response completes. Partial bulk outcomes and interrupted streams are not
reported as successful complete exports. Scheduled actions and timed closes have
separate events. The log retains the newest 10,000 events, including in exports.

Offline updater/recovery activity remains in its separate durable transaction
journal because writing the live activity database during restoration is unsafe.
The activity log is local, bounded operational history, not a tamper-proof external
compliance ledger. A host administrator can change the database or its backups.

## Deployment boundary and verification

Legacy API keys remain administrator credentials; do not share the manager's API
key with restricted users. Anonymous loopback mode is a trusted single-user mode.
These permissions protect access through the manager API/UI, not against people
who can read host files or control the OS/browser directly. They do not provide
cross-device sync or remote desktop isolation.

Tests cover migration/resume, last-admin protection, both API aliases, cross-user
object/list/bulk access, credential redaction, separate exports, session/grant
revocation during streaming, revoked/recreated scheduled actors and activity
outcomes. Browser UI contracts exercise account/grant/activity administration,
viewer/operator controls, safe redacted edits and access-loss state clearing.
