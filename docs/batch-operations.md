# Batch launch and stop

Select profiles in the main list and choose **Launch selected** or **Stop selected**.
The dialog previews the exact targets before sending any requests. Select up to
100 profiles and choose between one and four simultaneous operations (default:
two). Stopping browsers can interrupt unfinished work: review the list and save
work before confirming.

Concurrency limits simultaneous requests, not the total number of open browsers.
Successful launches keep their browsers open. Choose a batch size your computer
can support; this queue does not provide a memory-based browser quota.

Each profile has a waiting, in-progress, completed, failed or cancelled result.
The progress bar includes every terminal result; the separate counters distinguish
successes, failures and cancellations. One failure does not prevent unrelated
profiles from finishing. Existing running browsers are retained by the launch
API, and stopping an already stopped profile is a no-op.

**Cancel waiting** cancels requests that have not been sent. Requests already
accepted by the server continue to their result. **Close**, Escape, navigation
and logout also stop dispatching waiting requests. The queue belongs to this
open view; it is not a background schedule and does not resume or replay after
reopening the manager. Closing the manager itself retains its usual browser
shutdown behavior.

**Retry incomplete** explicitly retries only failed or cancelled targets. It
preserves successful results. If a request lost its response, it may have reached
the server: refresh running status and inspect the profile before retrying.
Duplicate launches are still guarded by the manager's profile/session ownership.

Administrators and granted operators can use this flow. Viewers have no batch
browser controls. The same launch/close APIs enforce current roles, grants,
profile leases and device synchronization ownership for every request. A 401/403
stops further dispatch; reopen the queue after restoring access. The queue never
changes a profile's proxy, fingerprint, cookies or synchronization enrollment.

Results use fixed recovery messages rather than displaying arbitrary server
exceptions, which could contain proxy details. The queue stores no credentials
and persists no target list in browser storage. It provides interactive results,
not a durable job history. Reusable background workflows and execution history
are tracked separately in [#122](https://github.com/polyackiy/camoufox-profile-manager/issues/122).
