# Profile templates

Templates save reusable browser settings, notes, tags and extension snapshots. Create one in **Templates**, or choose **Save as template** from a stopped profile's row menu. Profiles protected by another instance's lease cannot be used as a source.

Templates do not contain cookies, browsing history, saved logins, proxy configuration, passwords or pinned fingerprints. **Create profile** gives each result a new ID, a new data directory and no proxy. Its hardware fingerprint is generated independently on its first launch. Editing or deleting a template does not change existing profiles.

**Create batch** adds numbered naming and explicit saved-proxy selection for up
to 25 fresh profiles. Review the server-validated preview before creating them;
recent-batch receipts let interrupted requests resume without recreating committed
profiles. See [batch creation and recovery](template-batches.md).

The editor exposes common browser settings and an optional **All browser settings** JSON editor for the remaining supported settings. Unknown fields, including cookies, fingerprints and proxy configuration, are rejected. A conflict requires reloading the saved template before retrying. Changing the notes or settings keeps the existing extension snapshots unless **Replace saved extension snapshots** is selected.

Extension snapshots belong to the template, so deleting a library extension does not break it. Upload a newer version explicitly, then replace the template's snapshots if wanted. Updates never occur automatically.

Templates and the unassigned extension library live in the app's SQLite database. Profile exports and Settings profile backups preserve assigned profile snapshots, not the reusable template/library records. Preserve the database separately when moving the entire library. Profile archives retain their normal sensitive-data handling.

## API

All routes below use `/api/v1` (legacy `/api` aliases remain available), the existing authentication guard and the standard `ApiResponse` envelope.

| Method | Route | Input |
| --- | --- | --- |
| GET | `/templates` | List template metadata and settings |
| POST | `/templates` | `name`, `browser_settings`, optional `notes`, `tags`, `extension_ids` |
| POST | `/templates/from-profile` | `name`, `profile_id`, `profile_row_version` |
| PATCH | `/templates/{id}` | `row_version` plus changed fields; extension IDs replace snapshots only when present |
| DELETE | `/templates/{id}?row_version=N` | Version required |
| POST | `/templates/{id}/profiles` | `row_version`, new profile `name`, optional `group` |
| POST | `/templates/{id}/batch-preview` | Batch UUID, template version, names, optional group and versioned proxy references; no creation |
| POST | `/templates/{id}/profile-batches` | Same exact preview intent; returns per-row results and failed positions |
| GET | `/template-profile-batches` | Latest 20 local batch results; administrator only |
| GET | `/template-profile-batches/{uuid}` | Saved intent and current per-row results; no automatic resume |

Stale versions return 409. A source profile must be stopped and available for an exclusive operation. Fresh profiles are published only after their extension files are prepared; no other instance can discover a partially assigned profile.
