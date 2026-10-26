# Proxy protocol support and acceptance

This describes the unreleased 0.7.0 source work tracked in
[#116](https://github.com/polyackiy/camoufox-profile-manager/issues/116).
Stable 0.6.0 does not include the new SOCKS4 health checker or URI aliases.
A dropdown is configuration, not evidence that every provider or installed
platform has passed acceptance. See the reproducible tests and remaining gates
below before publishing installers.

## Connection matrix

| Selected protocol | Authentication | Destination DNS | IPv4 destination | IPv6 destination | Manager integration |
| --- | --- | --- | --- | --- | --- |
| HTTP | None or username/password (Basic) | Proxy handles destination hostname | Yes | Protocol supports it; local transport test | Native browser HTTP proxy; HTTPS sites use CONNECT |
| HTTPS | None or username/password (Basic) | Proxy handles destination hostname | Yes | Protocol supports it; local transport test | TLS connection to the proxy, then HTTP forwarding/CONNECT |
| SOCKS4 | None; empty USERID | Proxy resolves names using SOCKS4a | Yes | Unsupported, fails closed | Session-owned SOCKS5-to-SOCKS4a relay |
| SOCKS5 | None | Remote | Yes | Protocol supports it; local transport/wire tests | Native browser SOCKS5 with remote DNS forced |
| SOCKS5 | Username/password, each 1–255 UTF-8 bytes | Remote | Yes | Protocol supports it; local transport/wire tests | Session-owned authenticated SOCKS5 relay |

The proxy server's own hostname still needs resolution on the manager's computer.
Remote **destination** DNS does not turn a TCP proxy into a system VPN. UDP,
arbitrary tunnel protocols and automatic unique exit IPs are outside this matrix.
WebRTC remains governed by the profile's explicit WebRTC settings.

Import accepts `socks4a://` and `socks5h://` as aliases. They normalize to existing
`socks4`/`socks5` database values; both already use remote destination DNS in this
manager. There is no implicit fallback to local DNS for a SOCKS4 server that cannot
handle SOCKS4a. SOCKS4's USERID is not username/password authentication; this
manager sends an empty USERID and rejects supplied credentials. Previously saved
unsupported records remain readable so users can repair them, but cannot launch
or be assigned as usable proxy copies.

```text
http://user:password@proxy.example:8080
https://user:password@proxy.example:443
socks5h://user:p%40ss%3Aword@proxy.example:1080
socks4a://proxy.example:1080
socks5://[2001:db8::1]:1080
```

Username/password values are preserved literally, including leading/trailing
spaces; SOCKS5 limits count UTF-8 bytes rather than visible characters.
Use complete URLs only in **Import**. The Server field takes `host:port` or
`[IPv6]:port`; credentials belong in their separate fields. Percent encoding is
supported by the importer. Credentials and raw provider responses are not suitable
for issue reports; use fictional values like the examples above.

## TLS, geography and failure behavior

An HTTP CONNECT proxy can carry HTTPS sites without being an HTTPS proxy itself.
Select HTTPS only when the connection **to the proxy** uses TLS. The manager does
not downgrade protocols or disable certificate validation after a failure.
Health responses are streamed as at most 256 raw, uncompressed bytes; oversized
or compressed responses cannot become an exit address. Both the health request
and browser connection verify their TLS peer, including
hostname checks. Trust for a private provider CA must be configured deliberately
in the relevant manager/browser trust store; a successful manager health check
does not prove the browser trusts the same CA.

Normal proxied launches resolve a fresh exit address with verified TLS before
handing a literal address to Camoufox. This avoids upstream SDK lookup caching and
unverified lookup behavior. Failed lookups stop the launch. Explicit coordinates
remain intact; if they still require a proxy-derived timezone/WebRTC address,
failed lookup or missing location resources also blocks the proxied launch.
Direct profiles retain their previous behavior. Hardware fingerprints and UI
language are independent of these connection checks.

Relays bind only to `127.0.0.1`, have bounded negotiation time and connection count,
and are owned by a single session (or by one short-lived health check). They never
connect directly to destination sites if the upstream fails. They close after
normal browser exit, failed launch or cancellation. Provider passwords stay in
memory; generated extensions or profile preferences do not contain them. Other
local processes can reach an active unauthenticated loopback listener: it is not
an isolation boundary against untrusted software on the same computer.

## Reproduce acceptance

Install the locked development dependencies and paired browser/resources:

```sh
uv sync --locked --extra dev --extra sync
uv run python scripts/install_ci_browser.py
uv run pytest tests/unit/test_proxy_protocols.py tests/unit/test_proxy_check.py tests/unit/test_socks_relay.py --no-network
```

For real-browser TLS tests, install **NSS certutil**, not Windows' unrelated system
utility. Linux uses `libnss3-tools`, macOS uses Homebrew `nss`; CI uses the official
MSYS2 `mingw-w64-x86_64-nss` package on Windows. Point `CPM_TEST_NSS_CERTUTIL` at its
binary when it is not on PATH (required on Windows):

```sh
CPM_TEST_NSS_CERTUTIL=/path/to/nss/certutil uv run pytest tests/browser/test_proxy_protocols.py --no-network
```

The fixtures generate a temporary CA, serve local HTTP/HTTPS origins and local
HTTP/HTTPS/SOCKS proxies, and route synthetic destination names only to those
origins. Only an isolated temporary browser profile trusts the CA; no OS trust
store or ordinary user profile is changed. Verification is never disabled.
Tests assert HTTP and HTTPS destinations, refused authentication/connections,
remote SOCKS hostname forwarding, invalid certificates, cleanup, cancellation,
and no successful origin access after proxy refusal. Transport tests also cover
IPv6 where supported and reject SOCKS4 IPv6 destinations. Python's `--no-network`
guard blocks non-loopback connections; browsers receive synthetic test URLs only.

[Browser compatibility CI](https://github.com/polyackiy/camoufox-profile-manager/actions/workflows/browser-compatibility.yml)
installs the test-only NSS tools and runs the matrix on Linux, Windows and macOS.
Results apply to the exact source revision and wrapper/browser pair recorded in
the run. Provider smoke checks are additional evidence, not deterministic tests.

## Installed-package acceptance

[Installed desktop proxy acceptance](https://github.com/polyackiy/camoufox-profile-manager/actions/workflows/installed-proxy.yml)
builds normal unsigned packages on Windows x64, Linux x64 and macOS Intel/Apple
Silicon. It installs a DMG/NSIS package or extracts the Linux portable distribution
into a disposable directory, starts the frozen manager and loads its bundled UI.
No development server or source manager runs in that installed process.

The harness exercises the seven protocol/authentication combinations above with
both HTTP and HTTPS pages. A same-origin JavaScript receipt proves each page
body executed in Camoufox. Normal positive launches use the manager's fresh
verified exit-IP lookup. The test proxy intercepts only the fixed health-service
hostnames and routes standard ports to local origins; it never dials those
services. The test IP is a fixed address known to the local GeoIP database.

An observer page on an explicit loopback exception waits for the manager launch
to finish before opening the HTTP/HTTPS probe frames. This avoids a proxy-auth
challenge before Playwright's authentication handler is ready. The synthetic
destination hostname always traverses the proxy; the loopback observer is not a
fallback for destination connections.

Refused-upstream checks and browser attempts must not reach a destination origin.
Separate browser TLS probes require a rejection receipt for an untrusted
destination and an untrusted HTTPS proxy. Matched positive controls must fetch
the exact response body successfully through the same observer/CORS path, so a
blocked observer or CORS error cannot satisfy TLS acceptance. These TLS trials
and refusal launches specify a literal GeoIP address so they reach browser
verification instead of stopping at the earlier health lookup. Normal positive
launches retain fresh verified GeoIP. There is no TLS bypass. Tests close
browsers through the installed manager, require session relay listeners to
disappear, check unchanged hardware/settings and repeat authenticated SOCKS5
after restarting the installed process.

Run only on disposable Actions workers after building the installer and preparing
the paired browser/resources, Chromium and NSS tools:

```sh
uv run --no-sync python -m scripts.installed_proxy_acceptance --output installed-proxy.json
# Linux: run the same command under xvfb-run -a.
```

The script deliberately refuses ordinary user machines. Its ephemeral CA is
trusted only by the disposable installed copy's CA bundle and synthetic NSS
profiles, never by the OS. On macOS the modified copy is re-signed ad hoc; this
tests application behavior, not Developer ID, notarization or quarantine prompts.
Reports include the revision, architecture, package SHA-256, public versions and
allowlisted outcomes. Raw API responses, credentials, profile data, screenshots
and private logs are not uploaded. Unexpected fixture failures fail acceptance.

These same-runner trials do not prove every provider, physical computer, native
window-manager behavior or an independent security audit. Linux portable trials
do not replace the separate clean-system DEB install/remove/dependency checks.
Re-run the matrix at the release candidate revision; old green reports do not
certify a later installer.

## Remaining release gate

Before closing #116 or publishing the 0.7.0 installer block, record the final CI
results and exercise installed managers on Windows, macOS Intel/Apple Silicon and
Linux against the same controlled scenarios. Keep source acceptance, installer
acceptance and independent security review clearly separate. Do not describe
unrun package/platform combinations as verified. Experimental synchronization
continues to require explicit opt-in; this work does not change its safety gate.

Protocol references: [SOCKS5 (RFC 1928)](https://www.rfc-editor.org/rfc/rfc1928),
[SOCKS5 password authentication (RFC 1929)](https://www.rfc-editor.org/rfc/rfc1929),
[curl's SOCKS4a/remote-DNS definitions](https://curl.se/libcurl/c/CURLOPT_PROXYTYPE.html)
and [Firefox ProxyInfo](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/proxy/ProxyInfo).
