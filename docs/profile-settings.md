# Profile settings

A profile's `browser_settings` control how Camoufox launches the browser. The
manager sets **high-level constraints** and lets Camoufox generate a consistent
fingerprint from them — it deliberately does not hand-pick the user agent or WebGL
renderer, because inconsistent values are easy to detect.

This document describes Camoufox **0.5.8 / 156.0.1-beta.36**. See
[Camoufox compatibility](camoufox-compatibility.md) for the supported pair and
upgrade procedure. Measurements from older builds are labelled separately.

## Fields

| Field                  | Type        | Notes                                         |
| ---------------------- | ----------- | --------------------------------------------- |
| `os`                   | str         | `windows`, `macos`, or `linux`                |
| `browser_ui`           | str         | `desktop` (ordinary controls) or `camoufox`   |
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
| `audio:seed`, `fonts` | `headers.*` |
| `mediaDevices:*`, `voices` | `window.history.length` |

Location and locale are deliberately left out: freezing them would pin a Berlin
timezone onto a profile that later moves to a Tokyo proxy, and a timezone that
contradicts the exit IP is easier to spot than no spoofing at all.

Legacy pins may contain `canvas:seed` or `fonts:spacing_seed` from older builds.
Camoufox 0.5.8 no longer generates those fields. Preserving an old config value
does not establish that the current engine consumes it, or that native canvas
pixels are identical on every host.

*Regenerate fingerprint* clears the pin, so the next launch assigns new hardware.
The pinned values are shown read-only in the profile form.

### Keeping a pin from going stale

A pin never ages by itself. A profile created on Firefox 152 still claims 152 a
year later, and a browser several releases behind is itself unusual — real
machines update.

*Update* in the Machine panel (or `POST /api/v1/profiles/{id}/refresh-browser`)
moves the pin onto the installed browser and changes **only** the stored browser
version fields. Screen, GPU, cores, fonts and existing identity seeds remain
unchanged. A newer engine or host renderer can still change rendered pixels;
preserving the config is not a pixel-equivalence guarantee. The button
appears only when the pin is behind the browser on disk.

The user agent is resolved for the OS **the pin describes**, taken from its
`navigator.platform`, not from the profile's OS setting. The two can disagree if
someone changed the dropdown after the machine was pinned, and following the
setting would put a macOS browser on Windows hardware.

### When the OS setting and the machine disagree

The pin is what a page sees, so changing the `os` dropdown on a pinned profile
changes nothing observable — the setting simply stops meaning anything. That is
allowed (it is often the first step towards regenerating, and refusing it would
break bulk edits), but it is no longer silent: the profile response reports
`fingerprint.pinned_os`, `fingerprint.settings_os` and `fingerprint.os_mismatch`,
the server logs a warning on the save, the form says so under the dropdown, and
the Machine panel shows a banner offering the two ways out.

| | What it does | What it costs |
| --- | --- | --- |
| **Keep this machine** | Puts the OS setting back to what the pin reports | Nothing — no fingerprint value changes |
| **New \<OS\> machine** | Pins a fresh machine resolved for the OS setting | New screen, GPU, cores, fonts and noise seeds; a warmed-up account is now on different hardware |

Neither is preselected, and `POST /api/v1/profiles/{id}/reconcile-os` refuses when
the two already agree, so a stale screen cannot replace the hardware of a healthy
profile.

## Where the profile appears to be

A pinned machine keeps the hardware honest. Geography is the other half, and it
is deliberately **not** pinned: a profile that keeps a Berlin timezone after
moving to a Tokyo proxy contradicts itself in a way any page can measure with two
lines of JavaScript.

So a new profile is created without a timezone and without coordinates, and
Camoufox derives both — plus the WebRTC address — from where the proxy comes out.
Set either field only when you want to override that.

Setting **coordinates** is the exception worth understanding. Camoufox overwrites
the geolocation keys from the exit address whenever its IP lookup is on, so the
only way to keep coordinates is to turn that lookup off — and the same branch is
what fills the timezone and the WebRTC address. This manager therefore fills
those two itself, from the same address and the same database Camoufox would have
used. Without that, Firefox falls back to *this computer's* timezone: measured,
a profile with Tokyo coordinates reported `Europe/Moscow`, the host's own zone.

Profiles created before this behaviour keep the timezone and coordinates they
were given, and those were picked from a random region. They are still not
migrated automatically — changing a stored fingerprint under a live account is
worse than leaving it — but the change is offered:

- **In the profile form**, under Geolocation, whenever the saved profile states a
  timezone or coordinates: *Clear both, follow the proxy*.
- **In the profiles list**, as a bulk action on the current selection. The button
  counts how many of the selected profiles actually state a location, so it says
  what it will really change.
- **`POST /api/v1/profiles/clear-geography`** with `{"profile_ids": [...]}`, which
  is what both use.

**There is no attempt to guess which values were chosen on purpose.** Nothing
recorded it, and the only distinguishable signal — values matching the old
generator's region table — is exactly what someone who deliberately picked that
city would have produced. So the action is offered wherever geography is set,
explained, and never applied on its own.

Only the timezone and the coordinates are cleared. Languages and `locale` are
left alone: Camoufox applies languages after its IP lookup regardless, an
English-language browser is unremarkable from any country, and changing them
would alter the profile's identity rather than free it to follow the proxy.

Clearing the coordinates is the part that matters beyond tidiness. Their presence
forces `geoip=False`, and that same branch is what fills the timezone and the
WebRTC address, so a profile carrying a random region derives none of the three
from its proxy. With both cleared, `geoip` is back on and all three follow the
exit address.

### Checking a proxy

*Check proxy* in the profile form (and in a profile's row menu) reaches the
internet through the proxy and reports the address it comes out at, the country
and timezone that address resolves to, and the round trip. It then lists anything
a page could notice:

| Reported as | When |
| --- | --- |
| Error | The profile cannot launch with the chosen settings — for example SOCKS4 credentials, unsupported by the launch integration |
| Warning | A page can see the contradiction: a timezone on a different clock, or coordinates more than 500 km from the exit |
| Note | Worth knowing, not detectable as a conflict: a different zone name on the same clock, or coordinates in a nearby city |

The check reads the form as it is on screen, so a proxy can be tested — and a
mismatch fixed and re-tested — before anything is saved.

## Real device presets

Camoufox bundles fingerprints captured from actual machines; the catalogue
depends on the installed wrapper version. A generated fingerprint is internally
consistent but still an assembly of parts; a preset is a combination that
genuinely exists.

Pick one when creating a profile (the form lists them by screen, CPU count and
GPU) and the profile is pinned to that device immediately. `GET
/api/v1/fingerprints/presets` returns the catalogue; ids look like `windows:42` and
are only a selector — the resolved fingerprint is stored, so a Camoufox update
that reshuffles the catalogue cannot move an existing profile.

A preset defines the hardware, so values the profile would otherwise generate
(CPU count, screen) are cleared when one is chosen. Values you set explicitly
still win.

## Moving a profile

`GET /api/v1/profiles/{id}/export` packs the profile record, its pinned fingerprint
and its browser data directory into a single zip; `POST /api/v1/profiles/import`
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

- **Canvas image export is randomised by default.** Current browser tests cover
  repeated launches and cross-site behavior on the supported pair:

  | Scope | Canvas hash |
  | ----- | ----------- |
  | Two tabs, same site, one session | **the same** |
  | Different sites, one session | different — deliberate, and what stops sites correlating you across the web |
  | Same site, same profile, next launch | different |

  **The `stable_canvas` setting disables that randomisation.** Enabling it
  launches with `privacy.baselineFingerprintingProtection = false`. The profile
  retains its font list, while native browser/OS rendering supplies the pixels.
  Camoufox 0.5.8 does not generate a canvas seed or glyph-spacing seed. Observed
  behavior after host renderer initialization:

  | | Same site, relaunched | Different sites |
  | --- | --- | --- |
  | `stable_canvas` off (default) | different every launch | different |
  | `stable_canvas` on | identical in the tested renderer environment | identical |

  The right-hand column is the cost: a stable canvas is the same everywhere, so
  two sites can tell they are looking at one machine. That is exactly what real
  hardware does. Choose per profile depending on the importance of continuity
  versus cross-site unlinkability. Use **Canvas** in the profile form or
  `browser_settings.stable_canvas` through the API.

  **A pin does not guarantee byte-identical canvas output on a cold Windows
  host or across different hosts, graphics drivers, fonts and browser engines.**
  A fresh Windows CI worker changed one pixel between its first render and
  restart; a native Firefox 153 control reproduced the same shift. CI records
  that cold-host variation separately, then checks exact restart equality with
  an initialized host renderer. This preparation exists only in CI: the app
  applies no cold-host warmup or graphics/font preference workaround. See
  [Windows canvas rendering](windows-canvas-rendering.md) and
  [issue #73](https://github.com/polyackiy/camoufox-profile-manager/issues/73).

  Historical reports on Camoufox 152 found `canvas:seed` inert and
  `window.setCanvasSeed()` absent. Upstream closed
  [issue #721](https://github.com/daijro/camoufox/issues/721) through its
  [documentation/dead-code audit](https://github.com/daijro/camoufox/pull/787);
  those reports do not describe a supported seed API in 0.5.8.
- **Touch-point overrides need current browser verification.** On Camoufox
  152, `navigator.maxTouchPoints` stayed `0` despite the override. That result
  is historical; this release has no acceptance test establishing that the
  override works on 0.5.8 / beta.36. The manager stores `max_touch_points` and
  forwards nonzero values, but it should not be relied on as a verified touch
  identity feature. Camoufox remains a desktop browser.
- **`screen` is not sent to the browser.** It is generated, stored and shown, but
  Camoufox derives the real screen metrics itself.
- **SOCKS4 credentials are unsupported.** Use SOCKS5 or HTTP/HTTPS for
  authenticated proxies. The manager handles SOCKS5 authentication through a
  session-scoped loopback relay; see [proxy-library.md](proxy-library.md).
- **Changing `os` does not recompute the rest.** Screen size, locale and fonts
  keep their previous values; use *Regenerate fingerprint* for a consistent set.
