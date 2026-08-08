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

### Keeping a pin from going stale

A pin never ages by itself. A profile created on Firefox 152 still claims 152 a
year later, and a browser several releases behind is itself unusual — real
machines update.

*Update* in the Machine panel (or `POST /api/profiles/{id}/refresh-browser`)
moves the pin onto the installed browser and changes **only** the browser
version. Screen, GPU, cores, fonts and the noise seeds stay exactly as they were,
which is what a real computer looks like after a browser update. The button
appears only when the pin is behind the browser on disk.

The user agent is resolved for the OS **the pin describes**, taken from its
`navigator.platform`, not from the profile's OS setting. The two can disagree if
someone changed the dropdown after the machine was pinned, and following the
setting would put a macOS browser on Windows hardware.

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

- **The canvas hash changes between sessions, by default.** Measured on
  Camoufox 152:

  | Scope | Canvas hash |
  | ----- | ----------- |
  | Two tabs, same site, one session | **the same** |
  | Different sites, one session | different — deliberate, and what stops sites correlating you across the web |
  | Same site, same profile, next launch | **different every time** |

  The first two are what a privacy browser should do. The third is the problem: a
  real browser returns the same canvas to a site for years, so a site that records
  it sees a new machine every session.

  What is randomised is **image export** — `toDataURL()` and `toBlob()` — from a
  2D *or* a WebGL canvas. Raw pixel readback (`getImageData`, `readPixels`) is
  stable, and so is `measureText`.

  `canvas:seed` is listed as a supported property but is **not honoured**: pinning
  it changes nothing. It is declared in Camoufox's property manifest and emitted
  by its Python layer, but its C++ config reader never reads it, and no patch in
  the repository implements it. `window.setCanvasSeed()`, documented in Camoufox's
  per-context notes, is `undefined` in the shipped build while its siblings
  (`setNavigatorUserAgent`, `setWebGLVendor`) are defined. Both are worth fixing
  upstream, and the seed is the better long-term mechanism because it would give
  each profile its own canvas value rather than one shared true render.

  **The `stable_canvas` setting turns this off, and it is a genuine trade.**
  Enabling it launches with `privacy.baselineFingerprintingProtection = false`,
  which stops the pixel randomisation; combined with the pinned
  `fonts:spacing_seed` a profile already stores, the canvas becomes fully
  reproducible. Both halves are needed — text rendering follows the font seed
  rather than the canvas path, so the pref alone leaves a text-bearing canvas
  drifting. Measured:

  | | Same site, relaunched | Different sites |
  | --- | --- | --- |
  | `stable_canvas` off (default) | different every launch | different |
  | `stable_canvas` on | **identical** | **identical** |

  The right-hand column is the cost: a stable canvas is the same everywhere, so
  two sites can tell they are looking at one machine. That is exactly what real
  hardware does, and exactly what the randomisation existed to prevent.

  Choose per profile. One long-lived account wants it on, because looking like
  new hardware every visit is the bigger tell. Browsing the open web unlinkably
  wants the default. Set it in the profile form under **Canvas**, or as
  `browser_settings.stable_canvas` through the API.
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
