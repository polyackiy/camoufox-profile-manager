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

## Real device presets

Camoufox bundles fingerprints captured from actual machines — 180 Windows, 67
macOS and 65 Linux at the time of writing. A generated fingerprint is internally
consistent but still an assembly of parts; a preset is a combination that
genuinely exists.

Pick one when creating a profile (the form lists them by screen, CPU count and
GPU) and the profile is pinned to that device immediately. `GET
/api/fingerprints/presets` returns the catalogue; ids look like `windows:42` and
are only a selector — the resolved fingerprint is stored, so a Camoufox update
that reshuffles the catalogue cannot move an existing profile.

A preset defines the hardware, so values the profile would otherwise generate
(CPU count, screen) are cleared when one is chosen. Values you set explicitly
still win.

## Moving a profile

`GET /api/profiles/{id}/export` packs the profile record, its pinned fingerprint
and its browser data directory into a single zip; `POST /api/profiles/import`
restores it under a new id. In the UI: *Export…* in a profile's row menu, and the
import button in the toolbar.

This is the only way to move an account without losing it. A warmed-up profile is
mostly its cookies, storage and history — exporting the settings alone would move
the costume without the memory.

- The browser must be closed. Exporting a running profile is refused (409),
  because its databases would be copied mid-write.
- Disposable caches are excluded, which is most of the size: a 51 MB profile
  packs to about 16 MB.
- **The archive is not encrypted.** It contains live session cookies, saved
  logins and the proxy password, so it is exactly as sensitive as the account it
  belongs to.

## Known limitations

These are properties of Camoufox and Firefox, not of this manager:

- **The canvas hash changes between sessions.** Measured on Camoufox 152:

  | Scope | Canvas hash |
  | ----- | ----------- |
  | Two tabs, same site, one session | **the same** |
  | Different sites, one session | different — deliberate, and what stops sites correlating you across the web |
  | Same site, same profile, next launch | **different every time** |

  The first two are what a privacy browser should do. The third is the problem: a
  real browser returns the same canvas to a site for years, so a site that records
  it sees a new machine every session.

  `canvas:seed` is listed as a supported property but is **not honoured** for 2D
  canvas readback in this build — pinning it changes nothing, and neither does
  turning off Firefox's own anti-fingerprinting preferences. Camoufox's newer
  per-context patches add a `window.setCanvasSeed()` call that would fix this, but
  it is not exposed in the current release. Nothing in this project can change
  that; it needs the browser. See
  [roadmap.md](roadmap.md#known-limitation-we-cannot-fix-here).
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
