# Proxy library

Open **Proxies** to save a named proxy once, check its connection and assign it
to several closed profiles. The library belongs to this manager instance.

![Proxy library with saved entries and a completed check](screenshots/proxy-library.png)

Screenshots use fictional hosts and accounts.

## Save and check

1. Choose **Add proxy**, name it and select its protocol.
2. Enter `host:port` (or `[IPv6]:port`). Enter credentials in their separate fields.
3. Save, then run **Check**. The result includes reachability, exit IP, location
   and latency when available. Checks run only when requested.

Passwords are write-only: reopening an entry never retrieves its password.
Leaving the password unchanged preserves it; explicitly clearing it removes it.
Saving passwords requires the manager's encryption key. Desktop setup creates
the key automatically; server installations configure `CPM_SECRET_KEY`.
Keep the original key when moving or restoring the database.

## Import a list

Paste one proxy per line, up to 100 nonempty lines and 64,000 characters:

```text
proxy.example:8080
https://user:password@proxy.example:443
socks5://user:password@proxy.example:1080
socks5://[2001:db8::1]:1080
```

A bare server uses HTTP. Percent-encode special characters in credentials:
`p%40ss%3Aword` represents `p@ss:word`. Paths, query strings and fragments are
not accepted. Validation checks the complete list before saving anything and
identifies invalid lines without repeating credentials. Exact duplicates are
skipped; the result reports created entries and skipped line numbers.

## Assign to profiles

![Selecting closed profiles and confirming proxy replacement](screenshots/proxy-assignment.png)

Choose a saved proxy, select closed profiles and confirm replacement of their
proxy settings. Up to 100 profiles can be assigned in one operation. Running,
leased, deleted or concurrently edited profiles are skipped with a reason.
Reload before retrying stale entries.

Assignment copies the proxy configuration into each profile. Editing or deleting
a library entry does not change profiles that already received it. Assign again
when you want to apply a changed configuration. Existing profile exports and
backups therefore keep their proxy configuration independently of the library.

Library health describes the connection itself. Run a profile's proxy check
after assignment to check its timezone and other geography settings too.

## Storage and recovery

Library passwords are encrypted in the instance database. List and edit responses
contain only `has_password`, never the password. Users with manager access can
assign or use these proxies; this library does not introduce per-user ownership.

Profile backups cover the assigned copies, **not the proxy library**. Preserve
the instance database and its encryption key to recover unassigned entries.
Imported lists contain credentials: do not commit them or include them in bug
reports. Reports should use fictional hosts and passwords.

## SOCKS5 for manual browsing

Select **SOCKS5** and enter the provider's username/password just as you would
for HTTP. Both credentials must contain 1–255 UTF-8 bytes, as required by SOCKS5
username/password authentication. The manager starts a private loopback relay for that browser session,
authenticates to the upstream SOCKS5 server and sends destination hostnames to
it for remote DNS resolution. There is no extension to configure and no modified
Camoufox binary to maintain. Provider credentials stay in manager memory while
the session runs; the browser receives a loopback address without credentials.

The relay does not connect directly to destination sites if the upstream fails.
It closes with the browser session. It binds only to `127.0.0.1`, but other
processes on the same computer can reach it while active: it is not an isolation
boundary against untrusted local users or software.

This covers TCP browser connections. It is not a system VPN and does not add
SOCKS UDP support or replace the profile's WebRTC settings. SOCKS4 has narrower
support: credentials are unsupported and the health checker does not support
that protocol. Prefer SOCKS5 for a checked, authenticated SOCKS configuration.

### Why a relay instead of an extension?

Firefox's supported [ProxyInfo API](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/proxy/ProxyInfo)
does support authenticated SOCKS5. The limitation is Playwright's direct proxy
launch path, not Firefox as a whole. A managed extension worked in a persistent
Camoufox test, but needs startup coordination and credential provisioning.
The relay is ready before browser startup and avoids writing provider credentials into generated
extension files. Both approaches can be implemented without forking Camoufox;
the relay is the manager's current integration choice.

For authenticated SOCKS5, the manager resolves the exit IP through the original
upstream proxy before normal GeoIP setup, then passes the literal IP to Camoufox.
This avoids local DNS resolution of the IP-check service and cached results tied
to a reused loopback port. A failed lookup stops that launch with an actionable
error. Profiles with explicit coordinates retain their existing geography logic.
