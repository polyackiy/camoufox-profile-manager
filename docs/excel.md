# Excel import / export

Manage profiles in bulk via `.xlsx` files.

## Export

Export all profiles to a structured spreadsheet:

- **API:** `GET /api/profiles/export/excel`
- **Web UI:** the *Export* button on the Profiles page.

Each row is a profile; columns cover the core fields, browser settings, proxy, and
geolocation. The header row includes help text, and key columns have dropdown
validation.

## Import

Upload an edited spreadsheet to create profiles in bulk:

- **API:** `POST /api/profiles/import/excel` (multipart file upload)
- **Web UI:** the *Import* button on the Profiles page.

Import **always creates new profiles** with fresh auto-generated IDs — it does not
update existing ones. This keeps IDs unique and avoids accidental overwrites. The
response reports how many profiles were created and any per-row errors.

## Columns

The exported template documents every column inline. The main ones:

- `name`, `group`, `status`, `notes`
- `os`, `screen`, `window_width`, `window_height`, `languages`, `timezone`, `locale`
- `webrtc_mode`, `canvas_noise`, `webgl_noise`, `audio_noise`
- `hardware_concurrency`, `device_memory`, `max_touch_points`
- `proxy_type`, `proxy_server`, `proxy_username`, `proxy_password`
- `geo_mode`, `geo_latitude`, `geo_longitude`, `geo_accuracy`

The `id` column is read-only on export and ignored on import.
