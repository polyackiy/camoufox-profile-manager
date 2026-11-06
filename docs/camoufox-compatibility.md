# Camoufox compatibility and upstream changes

Reviewed against the official
[browser releases](https://github.com/daijro/camoufox/releases) and
[Python package](https://pypi.org/project/camoufox/0.5.9/).

The project lockfile uses **Camoufox 0.5.9**, **Playwright 1.62.0** and the
wrapper's paired [**158.0.1-beta.41** browser](https://github.com/daijro/camoufox/releases/tag/v158.0.1-beta.41).
Upstream now ships uBlock Origin inside each browser archive. The manager checks
the executable and bundled add-on manifest together before reporting readiness.

## What changed since our 0.5.4 / beta.29 integration

| Upstream change | Manager consequence |
| --- | --- |
| [Firefox 156 and native identity work](https://github.com/daijro/camoufox/releases/tag/v156.0.1-beta.32) | Recheck pinned identities, page-visible values, fonts and extension behavior on the new engine |
| [Explicit wrapper/browser pairing](https://github.com/daijro/camoufox/pull/810) | Follow the wrapper's tested browser by default; do not select unrelated newest binaries |
| [GeoIP All-in-One default](https://github.com/daijro/camoufox/pull/820) | Prepare the selected dataset during setup; retain proxy-based location checks |
| [Proxy encoding, input events and explicit locales](https://github.com/daijro/camoufox/pull/827) | Retest authenticated proxies and user-selected locale settings |
| [Toggle preferences written each launch](https://github.com/daijro/camoufox/pull/808) | Keep desktop/minimal chrome as upstream settings; test switching on a persistent profile |
| [fpgen-backed wrapper](https://pypi.org/project/camoufox/0.5.8/) | Package the verified model in desktop builds and prepare it during source-install setup |

The manager retains its session-owned authenticated SOCKS5 relay: a newer wrapper
does not by itself establish that Firefox supports SOCKS username/password auth.
No change to the Camoufox engine is needed for managed extensions, cookies or
ordinary desktop tabs.

With automatic location enabled and no proxy, SDK 0.5.9 also checks the host's
public IPv6 when the location lookup uses IPv4. It uses that result to decide
whether to keep IPv6 enabled in the browser. The SDK skips this direct IPv6
lookup when a proxy is configured or automatic location is disabled.

Firefox 158's baseline fingerprinting mode can vary Canvas reads on the same
page. To preserve per-site, per-session randomization, the manager uses the
keyed Firefox protection mode with the same three desktop targets as
[baseline mode](https://searchfox.org/firefox-main/source/toolkit/components/resistfingerprinting/RFPTargetsDefaultBaseline.inc).
Profiles that explicitly enable stable canvas keep that choice. This uses
Firefox preferences and does not patch the browser.

## Installation and upgrades

After updating a source checkout, run `uv sync --extra dev`, then use **Settings →
Install browser** or `uv run camoufox fetch`. The first launch remains gated on
setup readiness; it must not silently fetch a browser or fingerprint model.

If you previously selected a browser explicitly, upstream preserves that choice.
An older build without bundled add-ons needs a compatible selection before launch.
To use the browser paired with the current wrapper, run:

```bash
uv run camoufox set --release
uv run camoufox fetch
```

Close browsers and back up profiles before opening existing data in a newer
engine. Updating a binary is not permission to regenerate a stored fingerprint.
For browser downgrades, restore a matching data checkpoint rather than force
opening a profile migrated by newer Firefox. See [coordinated update design](update-design.md).

## Validation

The release acceptance record belongs in [stage two](stage-two.md). It includes
real browser tests for fingerprint stability, desktop chrome, authenticated
SOCKS5, cookie lifetime and extension assignment/removal, plus the combined
template/archive workflow. Unit tests alone do not establish browser compatibility.

The Windows canvas checks require an initialized native renderer. A cold-host
one-pixel transition also reproduces in unmodified Firefox; it is tracked openly
in [the rendering limitation](windows-canvas-rendering.md), not claimed fixed by
this wrapper update.
