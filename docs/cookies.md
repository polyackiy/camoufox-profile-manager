# Cookie import and export

Open a profile's action menu and choose **Transfer cookies**. Close that profile's
browser first. Cookie transfer uses the installed Camoufox browser; install it
through Settings if setup has not completed.

Choose **Browser cookie JSON** or **Netscape cookie text**, then paste the contents
or select a file. Click **Validate cookies** to see the number of importable,
expired, duplicate, and session cookies. Previews and error responses show counts
and row numbers, never cookie values. An invalid row rejects the whole input
before the profile's cookie store changes.

**Merge with existing cookies** updates cookies with the same name, domain, and
path while preserving other cookies. **Replace existing cookies** removes the
existing cookie jar. Replacement requires an explicit confirmation and a
successful complete profile backup. Recover that backup through
**Settings → Profile backups → Restore as new profile**.

Imported session cookies are applied at the next successful profile launch. They
remain session cookies and disappear when that browser session closes. A failed
or interrupted launch before application retains the transfer for retry. A
successful handoff is acknowledged separately, so later launches and exports do
not resurrect an already-consumed imported session. Reimporting the same file is
an explicit new transfer.

**Export cookies** deliberately downloads a sensitive attachment. Export includes
persistent cookies and any imported session cookies still waiting for their first
launch. It does not include local storage, passwords, or the rest of the profile;
use a full profile archive for those. Export and import never operate on a
running profile or another instance's leased profile. If the profile was edited
elsewhere, close the dialog and reload it before retrying.

| Format | Preserved fields | Limits |
| --- | --- | --- |
| Browser JSON array | Name, value, domain/host-only scope, path, Secure, HttpOnly, SameSite, expiry/session state | Playwright cookie arrays and common EditThisCookie fields supported |
| Netscape seven-column text | Name, value, domain/host-only scope, path, Secure, expiry/session state; `#HttpOnly_` preserves HttpOnly | SameSite cannot be represented; importing this format uses Lax |

Example JSON (synthetic values only):

```json
[
  {
    "name": "example_token",
    "value": "synthetic-value",
    "domain": "localhost",
    "path": "/",
    "expires": 1800000000,
    "httpOnly": true,
    "secure": false,
    "sameSite": "Strict"
  }
]
```

A leading dot in `domain` allows subdomains; a domain without that dot is
host-only. Single-label names such as `localhost` must be host-only; Firefox
[rejects them as domain cookies](https://searchfox.org/firefox-main/source/netwerk/cookie/CookieCommons.cpp).
An explicit `hostOnly` field takes precedence. `expirationDate` is
accepted as an alias for `expires`. Set `expires` to `-1`, omit expiry, or use
`session: true` for a session cookie. `sameSite` accepts Strict, Lax, None, and
EditThisCookie's `no_restriction`/`unspecified` spellings. SameSite=None requires
Secure. The `__Secure-` and `__Host-` prefix rules are validated.
Playwright's `url` form is also accepted: the URL supplies the host, its directory
path, and the Secure flag for HTTPS. Supply either `url` or `domain`/`path`.

Netscape rows contain seven tab-separated columns:

```text
# Netscape HTTP Cookie File
#HttpOnly_localhost	FALSE	/	FALSE	1800000000	example_token	synthetic-value
localhost	FALSE	/	FALSE	0	example_session	synthetic-value
```

Expiry timestamps use Unix **seconds**, not milliseconds. Netscape expiry `0`
means a session cookie. Expired cookies are reported as skipped. When the input
contains duplicate name/domain/path combinations, the last nonexpired entry
wins and earlier entries are reported as skipped.

Replace sample persistent timestamps with a future timestamp within the installed
browser's expiry limit. Firefox's default client-side lifetime cap is
[400 days](https://searchfox.org/firefox-main/source/modules/libpref/init/StaticPrefList.yaml).
If the browser clamps or rejects a requested cookie attribute, the whole import
is rejected before publication; the original profile cookie store is preserved.

Transfers are bounded to 2 MiB, 10000 cookies, and 4096 UTF-8 bytes per combined
cookie name/value. Invalid flags, cookie prefixes, domains, paths, expiries,
control characters, partitioned cookies, and container cookies are rejected.
Transfers use the default, unpartitioned browser cookie jar. Use a full profile
archive to preserve partitioned/container storage. JSON is
the preferred portable format when SameSite matters.

The implementation lets the installed browser create and migrate a temporary
copy of the cookie store, verifies the requested attributes through the browser
API, then atomically publishes the completed store under the profile lease and
optimistic version guard. It does not invent Firefox cookie-table columns or
write a live browser database. Deferred session data and its acknowledgment
travel with full profile archives.

Keep cookie downloads private: they can grant account access. The UI clears
pasted/file contents after a successful import and when the dialog closes.
