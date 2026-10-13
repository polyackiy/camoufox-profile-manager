# Stage three: shared-manager access and accountability

Tracking issue: [#77](https://github.com/polyackiy/camoufox-profile-manager/issues/77).

Stage two's accepted feature scope is recorded in [stage-two.md](stage-two.md).
This next stage starts with access controls for multiple users of one manager.
Cross-device synchronization requires its own data ownership/conflict design and
follows this work. This document is a proposed implementation sequence, not a
claim that the current manager already isolates users' profiles.

## Product contract

A person should see only profiles they can use. An administrator can grant access
without handing over the manager API key or host credentials. Sensitive actions
must be attributable to an actor without storing passwords, cookies, proxy
credentials, session tokens or profile contents in the audit log.

The initial roles are administrator, operator and viewer. An administrator manages
users, grants, system settings and recovery. An operator can run/edit assigned
profiles; a viewer can inspect permitted profile metadata. Launching a browser
reveals the logged-in account to its operator, so launch permission is already a
sensitive grant. Raw profile/cookie/proxy export needs a separate explicit grant;
view permission must not imply export or access to credentials.

Local host access remains privileged. This feature cannot protect profile data
from someone who can read the database or browser files directly. Existing API
keys are documented administrator credentials until scoped tokens are added.
Anonymous loopback mode remains a single-user deployment, with no misleading
team-security claim.

## Implementation sequence and acceptance

1. **Identify actors centrally.** Preserve existing authentication behavior while
   returning a typed principal for account sessions, legacy API keys and anonymous
   local use. Add a route inventory covering list/detail/export/import/launch,
   proxy library, scheduling, templates/extensions, system settings and recovery.
   Tests must prove unauthenticated/expired sessions still fail and tokens never
   enter log fields.
2. **Migrate roles and profile grants.** Use a numbered migration and explicit
   backwards compatibility: existing users retain administrator access, new users
   receive a deliberate role. Prevent removing the last administrator. Group
   membership is an organizational feature until its grant inheritance is defined.
3. **Enforce at the service/API boundary.** Filter collections and authorize every
   direct-object operation, bulk action, scheduled action and streaming/download
   path. Denial must not leak profile existence or credentials. Browser UI hiding
   alone is not authorization. Recovery/system update controls require an admin.
4. **Record security events.** Append actor, action, object ID, result and UTC time
   for grants, login/logout, launches, imports/exports and destructive operations.
   Bound retention; administrator export must exclude secrets. Record events only
   after/with the actual committed mutation, including failures where meaningful.
5. **Expose usable administration.** User/grant management, clear access-denied
   states and a searchable activity view. Document installation upgrades and
   recovery; run multi-user API/UI tests plus scheduled/bulk/streaming cases.

Each increment is independently reviewable. Do not advertise multi-user isolation
until steps 2–5 are integrated and all authorization paths are covered. Sync must
not start by copying open Firefox profiles or transferring unencrypted archives;
its design must address profile ownership, locking, credentials, merge conflicts,
server trust and rollback before implementation.

## Authorization coverage inventory

The following existing routes need explicit grants in the enforcement increment.
Both `/api` and `/api/v1` aliases must use the same policy.

| Surface | Required distinction |
| --- | --- |
| Profile lists, details, groups, tags, saved views | Filter collections; do not reveal another user's profile IDs or metadata |
| Create, edit, bulk updates, clone, Trash, permanent delete | Check each affected profile; return safe partial-result semantics |
| Browser launch/close, session listing, fingerprint refresh | Launch/edit grant; never expose another user's running session |
| Profile archives, Excel, JSON/Netscape cookies | Separate export grant; streaming/download must stay authorized throughout |
| Proxy library, health checks, bulk assignment | Separate credential/library administration from permitted profile use |
| Templates and profile-derived templates | Check source access and destination ownership; avoid copying secrets |
| Extension upload/delete and per-profile selection | Library administration versus assigned-profile editing |
| Schedules, run-now, run history | Persist actor/authority and recheck grants at execution, including revoked users |
| Backups, browser setup, settings, update/recovery actions | Administrator-only mutations; preserve offline recovery capability for the host owner |
| User/session management and audit view/export | Administrator-only grants; bounded access to activity records |

This is a coverage checklist for implementation, not an active permission matrix.
The first increment introduces a secret-free immutable request principal without
changing existing authorization. Account sessions identify the validated username;
legacy API-key and anonymous-local principals do not contain credentials. Roles,
profile grants and persistent security events remain subsequent increments.
