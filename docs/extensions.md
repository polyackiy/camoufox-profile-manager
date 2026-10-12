# Managed Firefox extensions

Open **Extensions** to upload a local Firefox `.xpi` file. Upload stores an extension in the library; it does not execute it. Review its add-on ID, version, API permissions and site-access patterns before assigning it. Assign or remove extensions through **Manage profile extensions** or a profile's **Extensions** row-menu action. Stop the profile first.

Each profile owns an unpacked snapshot below its browser-data directory, with a relative `.cpm-addons/<sha256>` reference. Profiles keep working after library entries are deleted. An extension missing from the library remains visible as a profile snapshot and may be kept or removed. Selecting another version of the same add-on requires removing the older selection. Removing every selected extension disables managed extensions on the next launch.

Camoufox loads the snapshot through its supported `addons` option. No Camoufox fork, remote extension fetch, automatic update or global browser installation is involved. Managed-extension launches disable Firefox extension updates. An explicitly assigned uBlock Origin version replaces Camoufox’s default uBlock add-on for that profile; removing it restores the default on the next launch. Uploading a new XPI and assigning it is an explicit version change.

The manager validates local archive structure and metadata, including ZIP member paths, symlinks, duplicate/case-colliding entries, conflicting file/directory paths, ZIP CRCs, compression ratios, manifest versions, names, numeric versions, explicit Firefox add-on IDs and bounded permissions. Limits are 16 MB per uploaded XPI, 64 MB unpacked per extension, 2,048 archive entries and 16 extensions / 128 MB unpacked per profile. Only TCP/browser traffic settings remain controlled by the existing proxy handling; an extension's declared permissions can allow it to change browser behavior or contact services.

Validation checks packaging, not the trustworthiness of executable extension code. Install extensions from sources you trust. The app displays permissions before assignment and does not silently install uploads into profiles.

## Portability and persistence

Profile archives, clones and Settings profile backups include assigned extension snapshot files and relative references. Imports validate those paths and files again before launching, so an archive cannot point the browser at arbitrary host directories. Template snapshots are independent of library entries.

The reusable library and templates live in SQLite; Settings profile backups do not back up those unassigned records. Preserve the database when transferring the complete library.

## API

Routes use `/api/v1` and the existing authentication guard.

| Method | Route | Input |
| --- | --- | --- |
| GET | `/extensions` | Public library metadata, no executable bytes |
| POST | `/extensions` | Multipart `file` upload; identical bytes deduplicate |
| DELETE | `/extensions/{id}?row_version=N` | Delete library entry only |
| GET | `/profiles/{id}/extensions` | Assigned metadata and current profile `row_version` |
| PUT | `/profiles/{id}/extensions` | `row_version`, `extension_ids`, optional `retained_refs` for currently assigned snapshots |

An assignment replaces the selection atomically under the profile's cross-instance lease and version guard. Running/leased profiles and stale edits return 409. Malformed archives return 422; oversized uploads return 413. Caller cancellation drains file preparation before releasing its protective lease and rolls back newly created, unassigned files.

References: [Camoufox add-ons](https://camoufox.com/fingerprint/addons/) and [Mozilla Firefox manifest settings](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/browser_specific_settings).
