# REST API reference

The API is the same one the web UI uses. A running instance serves interactive
documentation at **`/docs`** (Swagger) and **`/redoc`**, generated from the code —
that is the authoritative schema. This page is the guided tour, and the
[stability contract](#stability-contract) below is what we promise to keep.

Base URL: `http://127.0.0.1:8000` by default. Endpoints live under **`/api/v1`**.

The unversioned `/api/...` paths from before 0.2 keep working as aliases — same
routes, same behaviour — but are left out of the OpenAPI schema so a generated
client sees each operation once. New code should use `/api/v1`.

## Authentication

None by default, because the app binds to loopback. Set `CPM_API_KEY` and every
request must carry it:

```bash
curl -H "X-API-Key: $CPM_API_KEY" http://localhost:8000/api/v1/profiles
```

The key is compared in constant time. The web UI sends it too — paste it into the
Settings screen, which stores it in that browser only.

**Set a key before binding to anything other than `127.0.0.1`.** Without one,
anybody who can reach the port can read your profiles, including proxy passwords.

## Conventions

- Request and response bodies are JSON, except file upload and download.
- Resource endpoints return the resource itself. Action and system endpoints
  that have no resource to return use the envelope
  `{"success": bool, "message": str, "data": ...}`.
- **A field you send is authoritative, including `null`, which clears it. A field
  you omit is left alone.** This matters most on `PUT /api/v1/profiles/{id}`:
  send `{"proxy_config": null}` to detach a proxy; omit the key to leave it.
- Statuses: `400` for a bad request, `401` for a missing or wrong API key,
  `404` for a missing resource, `409` for a state conflict (exporting a running
  profile), `422` for values that fail validation, `500` otherwise.

### Errors

Every non-2xx response has one shape:

```json
{
  "error": {
    "code": "not_found",
    "message": "Profile with ID x1 not found",
    "details": null
  },
  "detail": "Profile with ID x1 not found"
}
```

- `error.code` is a stable snake_case token: `bad_request`, `unauthorized`,
  `not_found`, `conflict`, `validation_error`, `internal_error`.
- `error.message` is always a single human-readable string — including for
  validation failures, which used to arrive as a list.
- `error.details` carries the structured specifics when there are any; for
  validation failures it is pydantic's error list, each entry with `loc`,
  `msg` and `type`.
- `detail` mirrors `error.message` for clients written against FastAPI's
  default shape. It is deprecated and goes away in 1.0; read `error.message`.

## Stability contract

`1.0` freezes, for the whole `1.x` line:

- the `/api/v1` paths, methods and status codes documented here;
- the names and types of every documented request and response field —
  fields are only ever *added*, never renamed or removed;
- the error shape above and its `error.code` values;
- the pagination shape on `GET /api/v1/profiles`.

New endpoints, new optional parameters and new response fields are **not**
breaking changes and can appear in any release. Anything that would break the
above waits for a `/api/v2`, at which point `/api/v1` keeps working.

Deliberately **outside** the contract, at 1.0 and after:

- the contents of the `fingerprint` summary on a profile — its values come from
  Camoufox's fingerprint catalogue and change when Camoufox does;
- `camoufox_options` in the launch response — launch internals, exposed for
  debugging;
- the entries of `actions` in profile statistics;
- the preset fields beyond `id` and `os` — they describe whatever Camoufox's
  captured devices carry.

### Deprecated, removed in 1.0

Everything here works throughout `0.1.x` and is marked deprecated in the
OpenAPI schema:

| Deprecated                                        | Use instead                          |
| ------------------------------------------------- | ------------------------------------ |
| Flattened `browser_*` fields on profile update    | the same keys in `browser_settings`  |
| `browser_session_id` in the launch response       | `profile_id` (it addresses the browser); the id was a random value nothing accepted |
| Top-level `detail` in error responses             | `error.message`                      |

The unversioned `/api/...` aliases outlive 1.0 — scripts against them keep
working for the whole `1.x` line — and are removed in 2.0.

## Profiles

### List

```http
GET /api/v1/profiles?page=1&per_page=25&status=active&group=<id>&search=shop
```

Returns `{profiles, total, page, per_page, has_next, has_prev}`. `search` matches
the name. This is the only paginated collection: groups and browsers stay small,
so those lists are returned whole.

### Create

```http
POST /api/v1/profiles
```

```json
{
  "name": "shop-de-01",
  "group": "<group id>",
  "notes": "free text",
  "generate_fingerprint": true,
  "fingerprint_preset": "windows:42",
  "browser_settings": {
    "os": "windows",
    "timezone": "Europe/Berlin",
    "languages": ["de-DE", "de"],
    "hardware_concurrency": 8,
    "window_width": 1280,
    "window_height": 720,
    "webrtc_mode": "replace",
    "geolocation": {"lat": 52.52, "lon": 13.405}
  },
  "proxy_config": {
    "type": "socks5", "server": "host:1080",
    "username": "u", "password": "p"
  }
}
```

Only `name` is required. Everything under `browser_settings` that you leave out is
generated as a consistent fingerprint.

`fingerprint_preset` pins the profile to a real device from
`GET /api/v1/fingerprints/presets`; the preset then defines the hardware, so
values it owns are not also generated. This needs the browser installed and
returns `400` if it is not, rather than creating a profile with a different
machine than the one asked for.

Responds `201` with the profile, including a `fingerprint` summary once one is
pinned.

### Read, update, delete

```http
GET    /api/v1/profiles/{id}
PUT    /api/v1/profiles/{id}
DELETE /api/v1/profiles/{id}
```

`PUT` takes the same shape as create. `browser_settings` is **merged** over what
is stored, so sending one field does not reset the rest of the fingerprint. The
older flattened form (`browser_os`, `browser_timezone`, …) still works and is
merged the same way, but is deprecated — it goes away in 1.0.

### Other actions

```http
POST /api/v1/profiles/{id}/clone              {"new_name": "..."}
POST /api/v1/profiles/{id}/reset-fingerprint
GET  /api/v1/profiles/{id}/stats
```

`reset-fingerprint` generates new settings **and drops the pinned machine**, so
the next launch assigns fresh hardware.

```http
POST /api/v1/profiles/{id}/refresh-browser
```

Moves the pinned machine onto the installed browser version, changing only the
browser: the hardware and the noise seeds are kept. Use it when a profile's pin
has fallen behind the browser on disk — `fingerprint.browser_outdated` in the
profile response says when. Returns `400` if the profile has no pin yet.

The response's `fingerprint` summary carries `browser_major`, `installed_major`
and `browser_outdated` so a client does not have to parse the user agent.

## Checking a proxy

```http
POST /api/v1/profiles/{id}/check-proxy
POST /api/v1/proxy/check      {"proxy_config": {...}, "browser_settings": {...}}
```

Both reach the internet through the proxy and return the same shape: `reachable`,
`error`, `latency_ms`, the `location` it comes out at (`ip`, `country`,
`timezone`, `latitude`, `longitude`) and a list of `findings`, each with a
`level` of `error`, `warning` or `info`, the `field` it concerns and a message.

A proxy that does not answer is reported with `reachable: false` and an `error`,
not raised as a 5xx. The second form takes an unsaved proxy, which is what the
profile form uses so a proxy can be checked before the profile exists; omit
`proxy_config` to check the direct connection.

## Running browsers

```http
POST /api/v1/profiles/{id}/launch    {"headless": false, "window_size": "1280x720"}
POST /api/v1/profiles/{id}/close
GET  /api/v1/browsers/active
POST /api/v1/browsers/close-all
```

Launch responds with `status` (`launched`, or `already_running` if the profile's
browser is already up), a `message` and the `process_id`. Closing a browser that
is not running is a no-op reported as `status: "not_running"`, not an error.

The first launch of a profile resolves its fingerprint and stores it; every
launch after replays it. Closing the browser window yourself is detected and the
session is cleaned up, so `/api/v1/browsers/active` reflects reality without
polling the process table.

## Moving a profile

```http
GET  /api/v1/profiles/{id}/export      → application/zip
POST /api/v1/profiles/import           multipart: file=<archive.zip>[&name=...]
```

The archive carries the profile, its pinned fingerprint and its browser data
(cookies, storage, history, saved logins). Import assigns a new id, so an archive
can be restored next to the profile it came from.

- Exporting a **running** profile returns `409` — its databases would be copied
  mid-write.
- The archive is **not encrypted** and holds live session cookies and the proxy
  password. Treat it like the account itself.

## Device presets

```http
GET /api/v1/fingerprints/presets?os=windows
```

The fingerprints Camoufox captured from real machines, as
`{id, os, screen, hardware_concurrency, gpu, vendor, user_agent}`. Pass an `id`
as `fingerprint_preset` when creating a profile.

The catalogue reads without the browser installed; pinning one does not.

## Groups

```http
GET    /api/v1/groups
POST   /api/v1/groups            {"name": "Client A", "description": "..."}
GET    /api/v1/groups/{id}
PUT    /api/v1/groups/{id}
DELETE /api/v1/groups/{id}
```

Deleting a group keeps its profiles; they become ungrouped.

## Bulk editing

```http
GET  /api/v1/profiles/export/excel     → .xlsx
POST /api/v1/profiles/import/excel     multipart: file=<sheet.xlsx>
```

Import always creates new profiles rather than updating existing ones. The export
contains proxy passwords in clear text. See [excel.md](excel.md).

## System

```http
GET  /health                                liveness and database state
GET  /api/v1/system/status                  counts, load, memory, disk, uptime
GET  /api/v1/system/info                    name, version, uptime
GET  /api/v1/system/config                  effective configuration, no secret values
GET  /api/v1/system/profiles/diagnostic     profiles whose storage looks wrong
POST /api/v1/system/profiles/cleanup        remove orphaned profile directories
POST /api/v1/system/restart                 close all browsers, ready for a restart
```

`/health` is deliberately unversioned, so probes survive API versions. An
unhealthy instance answers with the same body under a `503`, so a load balancer
or container healthcheck sees the failure without parsing anything.

`/api/v1/system/config` reports whether encryption and the API key are on, the
bind address, the database path and whether the browser is installed — it never
returns the values of secrets. It backs the Settings screen.

`/api/v1/system/restart` does **not** restart the process; it closes every
browser so you can restart it cleanly yourself.

## Example: create a profile and run it

```bash
API=http://localhost:8000

ID=$(curl -s -X POST $API/api/v1/profiles \
      -H 'Content-Type: application/json' \
      -d '{"name":"demo","browser_settings":{"os":"windows"}}' \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

curl -s -X POST $API/api/v1/profiles/$ID/launch \
     -H 'Content-Type: application/json' -d '{"headless":false}'

curl -s $API/api/v1/browsers/active
curl -s -X POST $API/api/v1/profiles/$ID/close
```
