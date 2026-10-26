# Create profiles in batches

In **Templates**, choose **Create batch** on a saved template. Configure a name
pattern (for example `Work {n}`), a count from 1 to 25, a starting number and
number width. Choose an optional group. Existing names are rejected by default;
allow duplicates explicitly if you need them. Existing profiles are never overwritten.

Optionally select one distinct saved proxy for each profile. Selection order
defines the mapping; selecting fewer proxies than profiles is rejected rather
than silently sharing one endpoint. Select no proxies for a direct connection.
Passwords stay hidden. A preview checks saved proxy versions and access to the
encryption key, not network connectivity: use **Check** in the proxy library for
connection tests.

**Preview batch** validates all names, the group and proxy mapping on the server
without creating profiles or a saved batch. Inspect the exact names and endpoints,
then confirm creation. Each profile receives fresh browser data, its own ID and
directory, copied template settings/tags/notes and independent extension snapshots.
It does not copy cookies, sessions or pinned fingerprints. As with single-profile
template creation, the fresh identity is resolved independently on first launch.

Template and proxy versions are checked again before each profile is published.
If a referenced entry changes or is deleted, completed profiles remain intact;
review a new preview for rows that could not be created. Editing library entries
later does not change proxy copies in already-created profiles.

## Lost response, retry and restart

Each confirmed preview has a random batch identifier bound to its exact intent.
The manager commits the new profile and its per-row receipt in the same SQLite
transaction. **Retry same batch** or **Resume incomplete** skips every committed
row. Reusing the identifier with different names, settings versions or proxy
references is rejected. Concurrent copies of the same request cannot publish the
same row twice. Profiles subsequently moved to Trash or permanently removed are
reported as such and are never recreated by a retry.

Creation can continue after the dialog closes or a response is lost. Open
**Recent batches** before starting another batch. This lists the newest 20 batches
on this manager; opening a result does not automatically resume anything. Receipts
remain in the instance database after restart; older batches can also be queried
by identifier through the API. Full instance database checkpoints retain them;
individual profile archives and device handoffs do not carry this local operation
history. Replacing/resetting the instance database also replaces that history.

The operation is sequential and bounded, not a background scheduler. An interrupted
server leaves unpublished rows available for explicit retry. Normal cancellation
drains file preparation and removes unpublished files. An OS/process kill before
publication can leave an orphan directory, handled by the existing storage
diagnostic/cleanup flow; the receipt guarantee prevents duplicate published rows,
not an atomic transaction across SQLite and the filesystem.

## Access and secret handling

Only administrators may preview, create or inspect batches. Authority is rechecked
before each row and after file preparation. Operators/viewers cannot read history
or submit batches through either API alias. Partial results are audited as partial;
actual profile creations have individual activity records.

Requests and receipts contain names, group/proxy references and safe endpoint
metadata, never provider usernames/passwords or browser contents. Proxy passwords
are copied through the existing encrypted storage path and require the original
manager key. Errors use fixed recovery messages. These are additive local operation
tables; profile formats, schema-4 sync fences and old-manager profile semantics do
not change, and the experimental synchronization setting stays opt-in.
