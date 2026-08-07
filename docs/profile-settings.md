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
| `max_touch_points`     | int         | Stored intent; see the limitations below      |
| `webrtc_mode`          | str         | `replace`, `real`, `forward`, or `none`       |
| `webrtc_public_ip`     | str \| null | Public IP reported over WebRTC                |
| `fonts`                | list \| null| Font families to advertise                    |

## How settings reach the browser

`Profile.to_camoufox_launch_options()` builds the arguments passed to
`AsyncCamoufox`:

- Passed as launch parameters: `os`, `fonts`, `geoip`, `humanize`,
  `persistent_context`, `user_data_dir`, `proxy`, and `window` — the window size
  as a `(width, height)` tuple, not a `"1280x720"` string.
- `locale` is passed as the comma-joined `languages` list, which is the form
  Camoufox expects.
- `webrtc_mode: "none"` sets `block_webrtc=True`, which removes
  `RTCPeerConnection` from the page. The other modes leave WebRTC in place and
  let Camoufox report the proxy's address.
- Explicit overrides are emitted through Camoufox's `config` dict, using its
  property keys:
  - `geolocation:latitude`, `geolocation:longitude`, `geolocation:accuracy`
  - `navigator.hardwareConcurrency`, `navigator.maxTouchPoints`
  - `webrtc:ipv4`
  - `timezone`

Setting `geolocation` turns `geoip` off, so the coordinates are used as given
instead of being derived from the proxy's IP.

Camoufox owns canvas/WebGL/audio spoofing and the user agent, so those are not
set per-field here.

## The pinned machine

Camoufox resolves a fresh fingerprint on every launch. That suits a privacy tool,
but a profile manager needs the opposite: an account opened from a machine with
12 cores and an NVIDIA GPU must not come back tomorrow with 32 cores and an AMD
one. Without pinning, the same profile is measurably different hardware each
session — verified, and guarded by `tests/browser/test_fingerprint_stability.py`.

So the **first launch resolves the fingerprint once and stores it** on the
profile (the `fingerprint` column). Every later launch replays it. Camoufox fills
a config key only when it is absent, so a stored value always wins over a freshly
generated one, and the profile's own overrides still win over both.

Only the machine is frozen:

| Frozen — the identity | Dynamic — follows the proxy and the profile |
| --------------------- | ------------------------------------------- |
| `navigator.*` (user agent, platform, CPU cores, oscpu) | `geolocation:*`, `timezone`, `locale:*` |
| `screen.*`, `window.*` geometry | `webrtc:*` |
| `webGl*` (vendor, renderer, parameters, extensions) | `navigator.language`, `navigator.languages` |
| `canvas:seed`, `audio:seed`, `fonts`, `fonts:spacing_seed` | `headers.*` |
| `mediaDevices:*`, `voices` | `window.history.length` |

Location and locale are deliberately left out: freezing them would pin a Berlin
timezone onto a profile that later moves to a Tokyo proxy, and a timezone that
contradicts the exit IP is easier to spot than no spoofing at all.

*Regenerate fingerprint* clears the pin, so the next launch assigns new hardware.
The pinned values are shown read-only in the profile form.

## Known limitations

These are properties of Camoufox and Firefox, not of this manager:

- **The canvas hash still varies.** Camoufox randomises canvas noise per browsing
  context to defeat cross-site tracking, and pinning `canvas:seed` does not stop
  it: two tabs on the same site produce different hashes, as does each launch. A
  real browser is stable here, so this remains a detectable difference. It cannot
  be fixed from this side — it is a deliberate part of how Camoufox works.
- **`max_touch_points` has no effect.** Camoufox 152 lists
  `navigator.maxTouchPoints` as a supported property but does not apply it; the
  page keeps reporting `0` on every OS, with or without the touch-events pref.
  The field is stored so it starts working if upstream does.
- **`screen` is not sent to the browser.** It is generated, stored and shown, but
  Camoufox derives the real screen metrics itself.
- **SOCKS proxies cannot use credentials.** Firefox refuses to authenticate to a
  SOCKS proxy, so a profile with a SOCKS proxy plus a username or password fails
  to launch. Use HTTP/HTTPS for authenticated proxies.
- **Changing `os` does not recompute the rest.** Screen size, locale and fonts
  keep their previous values; use *Regenerate fingerprint* for a consistent set.
