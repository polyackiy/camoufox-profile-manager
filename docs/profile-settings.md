# Profile settings

A profile's `browser_settings` control how Camoufox launches the browser. The
manager sets **high-level constraints** and lets Camoufox generate a consistent
fingerprint from them — it deliberately does not hand-pick the user agent or WebGL
renderer, because inconsistent values are easy to detect.

## Fields

| Field                  | Type        | Notes                                         |
| ---------------------- | ----------- | --------------------------------------------- |
| `os`                   | str         | `windows`, `macos`, or `linux`                |
| `screen`               | str         | e.g. `1920x1080`                              |
| `languages`            | list[str]   | e.g. `["en-US", "en"]`                        |
| `timezone`             | str \| null | e.g. `Europe/Berlin`; forwarded to Camoufox   |
| `locale`               | str \| null | e.g. `en_US`                                  |
| `window_width/height`  | int \| null | Browser window size                           |
| `geolocation`          | dict \| null| `{lat, lon, accuracy?}`; enables spoofed geo  |
| `hardware_concurrency` | int \| null | CPU cores reported to the page                |
| `device_memory`        | int \| null | Stored intent (not all values map to Camoufox)|
| `max_touch_points`     | int         | Touch points (0 for desktop)                  |
| `webrtc_public_ip`     | str \| null | Public IP reported over WebRTC                |
| `fonts`                | list \| null| Font families to advertise                    |

## How settings reach the browser

`Profile.to_camoufox_launch_options()` builds the arguments passed to
`AsyncCamoufox`:

- High-level params — `os`, `screen`, `locale`, `fonts`, `geoip`, `humanize`,
  `proxy`, window size — are passed directly.
- Explicit overrides are emitted through Camoufox's `config` dict, using its
  property keys, for example:
  - `geolocation:latitude`, `geolocation:longitude`, `geolocation:accuracy`
  - `navigator.hardwareConcurrency`, `navigator.maxTouchPoints`
  - `webrtc:ipv4`
  - `timezone`

Camoufox owns canvas/WebGL/audio spoofing and the user agent, so those are not
set per-field here.
