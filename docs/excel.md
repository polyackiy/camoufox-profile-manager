# Excel import / export

Bulk-edit profiles in a spreadsheet. For moving a *single* profile between
machines — with its cookies and pinned fingerprint — use the profile archive
instead ([profile-settings.md](profile-settings.md#moving-a-profile)); a
spreadsheet carries settings only.

## Export

- **Web UI:** the download icon in the toolbar on the Profiles page.
- **API:** `GET /api/profiles/export/excel`

Each row is a profile. The header row carries help text and key columns have
dropdown validation, so the file doubles as a template.

> **The file contains proxy passwords in clear text.** It has to, for import to
> restore them. Store it as carefully as you would store the passwords. The UI
> says so before the download starts.

## Import

- **Web UI:** the upload icon in the toolbar.
- **API:** `POST /api/profiles/import/excel` (multipart file upload)

Import **always creates new profiles** with fresh IDs — it never updates existing
ones. That keeps IDs unique and avoids overwriting a profile by accident. The
response reports how many were created and any per-row errors.

Imported profiles have no pinned machine: the spreadsheet does not carry one.
Each gets its fingerprint on first launch, or you can pin a real device by
creating the profile through the UI instead.

## Columns

| Column | Notes |
| ------ | ----- |
| Profile ID | Read-only. Written on export, ignored on import. |
| Profile name | Required. |
| Group | Group name. |
| Status | `active`, `inactive`, `blocked`, `maintenance`. |
| Operating system | `windows`, `macos`, `linux`. |
| Screen resolution | e.g. `1920x1080`. Stored and shown; Camoufox derives the real screen itself. |
| Window width / Window height | Browser window size. |
| Browser languages | Comma-separated, e.g. `en-US, en`. |
| Timezone | e.g. `Europe/Berlin`. |
| Locale | e.g. `en_US`. |
| WebRTC mode | `replace`, `real`, `forward`, `none`. `none` disables WebRTC. |
| Canvas noise / WebGL noise / Audio noise | Stored intent; Camoufox owns this spoofing. |
| CPU cores | Reported as `navigator.hardwareConcurrency`. |
| Device memory | Stored; not all values map onto Camoufox. |
| Touch points | Stored, but Camoufox 152 ignores it — see the limitations in [profile-settings.md](profile-settings.md#known-limitations). |
| Proxy type | `http`, `https`, `socks4`, `socks5`. |
| Proxy server | `host:port`. |
| Proxy username / Proxy password | Firefox cannot authenticate to a SOCKS proxy — use HTTP/HTTPS when credentials are needed. |
| Geolocation mode | `auto` follows the proxy IP; `manual` uses the coordinates below. |
| Latitude / Longitude / Geolocation accuracy | Used when the mode is `manual`. |
| Notes | Free text. |
