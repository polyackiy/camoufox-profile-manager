# Automation workflows

Unreleased source work tracked in [#122](https://github.com/polyackiy/camoufox-profile-manager/issues/122).
The 0.6.0 installers do not contain this feature. No standalone release is
published for this change.

## Build and run

Open **Schedules → Automation workflows → New workflow**. Give it a name,
then add named **Navigate to URL** and **Wait** steps between **Open browser**
and **Close browser**. Reorder or remove the middle steps as needed. Saving the
definition does not run it.

Choose **Review and run**, select profiles, and check the selected names/IDs and
destinations. Searching hides unmatched choices but keeps selected targets in the
summary. **Start workflow** submits those profile IDs and their current versions.
The picker reads all pages up to 10,000 profiles; larger installations can use
the explicit-target API.

The executor starts a new managed session with an explicit neutral `about:blank`
tab, waits for the ready Playwright context,
then creates its tab and navigates. Destination URLs are never startup arguments.
This preserves the readiness boundary for authenticated proxies. Existing browsers
are skipped and remain untouched. The run closes its exact session after completion,
failure, cancellation or loss of authority; it cannot adopt a replacement browser.

For a declared profile screen, headed launches use Camoufox's public empty
`Screen` constraint. This prevents an implicit host-monitor bound from rejecting
fpgen's intermediate sample before the saved screen is applied. Profile hardware
and explicit caller constraints are preserved; there is no browser fork or launch
retry. A real-browser regression simulates the observed `RestrictiveConstraints`
failure and checks the declared screen and stored pin after launch/close.

| Resource | Limit |
| --- | --- |
| Definitions per installation | 100 |
| Targets per run | 100, each listed once |
| Steps | 2–12; launch first, close last |
| Concurrent runs | 4 per installation |
| Concurrent target executions | 1–4 per run, 4 globally |
| Wait | 1–60 seconds; cancellation wakes the wait |
| Navigation | HTTP(S), no embedded credentials; DOMContentLoaded, 45-second navigation timeout |
| Launch | 120-second budget, then safe teardown if interrupted |
| Detailed history | Active run first, plus latest 30 runs by completion per definition |

Teardown drains before the lease is released, including after timeouts. Closing
the manager requests cancellation, drains accepted work and closes managed browsers.
Session-owned SOCKS relays own accepted sockets before scheduling handlers, so
cancellation before a handler starts still closes the connection and discards
credentials.

## Progress, cancellation and retry

**Run history** shows each profile and step, with queued, running, completed,
failed, denied, skipped, cancelled, interrupted and missed outcomes. Reloading the
UI preserves receipts. Failure stops later steps for that target; other targets continue.

**Cancel remaining work** stops waiting profiles and later steps. An accepted
launch or navigation finishes or times out before the run closes its own browser.
Cancellation does not claim to undo accepted navigation. Manually closing its
browser ends that target.

**Review incomplete profiles** creates a new, explicitly confirmed run for selected
incomplete targets, starting at step one. Earlier completed steps within those
targets may repeat. Completed targets from the original run are excluded. A changed
definition requires a new manual review rather than mixing revisions during retry.

Each submission has a UUID request ID. Repeating the same request returns its
receipt without repeating actions. Compact request-ID tombstones survive detailed
history pruning: an expired receipt requires a new reviewed request ID. Deleting
an inactive definition deletes its history/tombstones and preserves profile data.

## Scheduling and restart

Scheduling is off by default. Enable it, choose an interval or daily time, and
select profiles explicitly. Times use the manager server's clock; choose weekdays
or leave them empty for every day (`0` Monday through `6` Sunday in the API). Scheduled work uses exact profile
versions: after editing a profile, review and save its workflow schedule again.

The next time advances before submission. Overlapping, unauthorized or stale-target
occurrences become missed records without automatic retry or backlog replay.
One executor owns an installation through the existing cross-process admission
lock. A second manager cannot reconcile live work as a crash or run competing workers.
Cancel a live run in its owning manager: a second manager returns a conflict
instead of acknowledging a cancellation it cannot deliver. A submission still
reviewing targets when its executor shuts down is refused before its receipt or
browser work is created.
On owning startup, unfinished receipts become **Interrupted**, preserving completed
steps. Missed schedule occurrences become one **Missed** record and wait for the
next occurrence. Neither case runs side effects automatically.

## Access and security

Definitions are private to their creator. Operators can use granted profiles;
viewers cannot start workflows. Creator identity includes the immutable user ID:
recreating a deleted username cannot inherit definitions or scheduled authority.
Anonymous-local and legacy API-key definitions retain their installation identity;
enabling account login does not silently transfer ownership to a new account.

The executor rechecks identity, role, grants and (for manual cookie runs) session
validity before steps and after creating a navigation tab. Scheduled work uses the
saved creator identity and current grants independently of login-session lifetime.
Anonymous execution stops when login/key protection is enabled. Legacy-key work
requires configured key authority; full-access API keys retain the existing
installation-wide trust boundary.

Profile admission is held across the sequence, with the existing browser lease
and sync fences. Other launches, edits, backups and sync transitions cannot race
that admission. Update maintenance waits for executions to drain. Cleanup can
close only the exact session created by the run, including after access revocation.

The declarative set accepts no shell commands, downloaded scripts, JavaScript
evaluation, custom launch arguments, cookie export or fingerprint regeneration.
HTTP(S) URLs are user-directed browser navigation and can reach local/internal
sites as manual browsing can; no privileged server HTTP client fetches them.

Definitions store explicitly entered URLs in the local database. Avoid secrets in
URLs or step names. Run responses allowlist names/actions/statuses and safe error
categories, excluding URLs, parameters, page content, cookies, proxy credentials,
internal owners and raw browser exceptions. Validation errors remove raw inputs
and exception context. Profile archives exclude workflows; whole-installation
update checkpoints include their database tables.

Failed target receipts may include a fixed `error_code` (`timeout`,
`browser_startup`, `proxy_connection`, `configuration`, `filesystem`,
`session_unavailable`, `operation_conflict` or `unexpected`) for troubleshooting.
These codes never contain exception text or arbitrary exception class names.

Schema **5** adds definitions, receipts and tombstones. Older schema-aware managers
refuse this database; application rollback needs its matching pre-upgrade data
checkpoint. Keep independent backups. Sync remains experimental and opt-in;
updater trust policy is unchanged.

## API and Playwright integration

Use the authenticated API (`/api/v1`, with `/api` as the existing alias):

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | `/workflows` | List private definitions / create |
| PUT | `/workflows/{id}` | Save with expected `revision` |
| DELETE | `/workflows/{id}?revision=N` | Delete an inactive definition |
| POST | `/workflows/{id}/runs` | Submit explicit targets and a request UUID |
| GET | `/workflows/{id}/runs` | Latest visible, sanitized receipts |
| GET | `/workflows/{id}/runs/{run_id}` | One visible receipt |
| POST | `/workflows/{id}/runs/{run_id}/cancel` | Cancel remaining work |

Example definition:

```json
{
  "name": "Check a public page",
  "concurrency": 1,
  "steps": [
    {"name": "Start", "action": "launch"},
    {"name": "Visit", "action": "navigate", "url": "https://example.com/"},
    {"name": "Observe", "action": "wait", "seconds": 5},
    {"name": "Finish", "action": "close"}
  ]
}
```

Submit `{"request_id":"<new UUID>","revision":1,"targets":[{"profile_id":"<id>","row_version":0}]}`
using the actual profile version. Keep the same request ID/body to recover a lost
response. Explicit replay uses a new UUID, `retry_of` referencing the finished
run, `replay_confirmed:true`, and only incomplete targets.

The integration uses the manager-owned [Playwright context](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-new-page)
and [Page navigation](https://playwright.dev/python/docs/api/class-page#page-goto).
No debugging port is exposed. External scripts can compose supported API steps
and poll receipts; executable code is not loaded into the manager.

## Reproducible acceptance

- Unit/API: `uv run pytest tests/unit/test_workflows.py tests/integration/test_api_workflows.py`.
  Tests idempotency/tombstones, versions, concurrency, cancellation, revocation,
  creator isolation, startup reconciliation and maintenance.
- UI: build the static export, then `uv run python web/tests/workflows_ui.py`.
  Twenty-one English/Russian/Chinese contracts cover editing, review, lost
  responses, retry consent, compact layout, keyboard and operator access.
- Real browser: `uv run pytest tests/browser/test_automation_workflow.py --no-network`.
  Headless and desktop authenticated HTTPS/SOCKS5 fixtures require a browser-executed receipt, with
  an ephemeral CA trusted only in disposable profiles.
  The Browser compatibility workflow also offers an `automation` dispatch suite
  for the four proxy cases and the declared-screen regression; it installs the
  same isolated-profile NSS tools. Full PR
  browser acceptance remains separate and required before merge.
- Installed packages: the four-platform installed proxy workflow also exercises
  a real authenticated HTTPS-proxy workflow and exports a sanitized summary.
  This distinguishes source/static checks from frozen-package proof.

Set `CPM_WORKFLOW_SCREENSHOTS` during UI acceptance to retain fictional screenshots.
Additional click/fill steps and an interactive multi-window synchronizer require
their own permission, cancellation and acceptance design.

Fictional-data screenshots: [target review](screenshots/automation/en-run-review.png),
[Russian compact editor](screenshots/automation/ru-editor.png),
[Chinese compact editor](screenshots/automation/zh-CN-editor.png).

The focused security review verified strict operation/URL validation, creator and
target isolation, revocation after launch/tab readiness, receipt retention without
implicit replay, exact-session cleanup, update admission and plain-text UI output.
The review added regression coverage for cancelled waits and retained request IDs.
Native CI results are recorded in the linked issue/PR; no external security audit
or promotion of experimental synchronization is implied by these checks.
