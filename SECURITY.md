# Security Policy

## Supported versions

This project is pre-1.0 and under active development. Security fixes target the
[latest stable release](https://github.com/polyackiy/camoufox-profile-manager/releases/latest).
Upgrade to that release; older release lines do not receive separate backports.

| Version | Supported |
| ------- | --------- |
| Latest stable release | Yes |
| Earlier releases | No separate backports |
| Previews and `main` | Development/testing; no separate supported release line |

Fixes may land on `main` before the next release. A development commit or preview
is not evidence that a fix is already in the stable installers. Release notes
identify the changes delivered by each published version.

## Reporting a vulnerability

Use this repository's [private vulnerability reporting form](https://github.com/polyackiy/camoufox-profile-manager/security/advisories/new)
(**Security → Report a vulnerability**). Private reporting is enabled. Avoid
public issues for unpatched vulnerabilities. See
[GitHub's reporting instructions](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
if you cannot find the form.

Please include a description of the issue, the affected component, and steps to
reproduce with the manager/browser version and OS. Use synthetic profiles and
redacted examples; do not attach working proxy credentials, session cookies,
recovery packages or unredacted databases/logs. We aim to acknowledge reports
within a few days.

## Handling secrets

- **Never commit real proxy credentials, API keys, or profile data.** The
  repository ships only synthetic examples. `data/`, `*.db`, `.env`, and profile
  directories are git-ignored.
- By default, a new desktop installation creates a local `secret.key` next to the database.
  Source/server deployments encrypt proxy passwords at rest when
  `CPM_SECRET_KEY` is configured (see `.env.example`). Generate a key with:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Keep the database and its key together for recovery. Key encryption of proxy
  passwords does not encrypt the entire browser profile. Profile exports,
  backups, cookie files and Excel exports can contain active sessions or proxy
  passwords in clear text. Protect them as credentials; see
  [backup and restore instructions](docs/getting-started.md#protect-and-recover-your-data).
- If a credential is ever committed by mistake, **rotate it immediately** — removing
  it from the latest commit does not remove it from git history.

## Authentication

The API has three configuration states, decided by what exists rather than by a
switch, so the configuration cannot contradict the database:

1. **Nothing configured** — no user accounts, no `CPM_API_KEY`. Requests have
   local open-mode administrator authority. The app binds to `127.0.0.1` by
   default. Keep an unconfigured instance on loopback; open mode must not be
   exposed to other machines.
2. **`CPM_API_KEY` set, no accounts** — protected requests must carry the key in `X-API-Key`,
   compared in constant time. This is the machine-to-machine path: one shared
   secret with administrator authority, not a per-user or per-profile scoped
   token. Do not give this key to restricted operators/viewers.
3. **User accounts exist** (`camoufox-pm user add <name>`) — humans log in with
   a username and password; a valid session **or** the API key passes. Accounts
   are managed through the administrator **Access** screen/API or the CLI.
   In open mode the first account can be created through Access or
   `camoufox-pm user add <name>` and must be an administrator. Once accounts
   exist, user/grant administration requires administrator authority. The last
   administrator cannot be deleted or demoted, including through the CLI.

Administrators control the installation. Operators can view/edit/launch granted
profiles; viewers can view granted profiles. Export requires a separate grant
and defaults off for both roles. Proxy secrets and sensitive diagnostics are
redacted for nonadministrators. See the complete
[permission matrix and revocation rules](docs/stage-three.md#permission-matrix).

Roles guard manager requests. They do not isolate mutually hostile processes
running under the same OS account, nor revoke browser-session data already
exported or copied. A user allowed to launch a profile can use its logged-in
website sessions. The activity journal excludes credentials but is a bounded
local operational log, not an immutable external audit service.

What protects a session:

- Passwords are hashed with **argon2id** (`argon2-cffi` defaults); plaintext
  passwords are never stored or logged, and hashes never leave the database.
- The session token is random (256 bits) and stored **only as its SHA-256**, so
  a copy of the database contains nothing replayable.
- The cookie is **HttpOnly** (page scripts cannot read it), **SameSite=Lax**
  (cross-site POSTs do not carry it), and **Secure** when served over HTTPS or
  when `CPM_SECURE_COOKIES=1`.
- Logout deletes the server-side row: a logged-out token is invalid even if an
  attacker kept a copy. Sessions expire after `CPM_SESSION_TTL_HOURS`
  (default 168).
- A failed login does not reveal whether the username exists — same status,
  same body, the same argon2 work either way — and is delayed half a second as
  brute-force friction. There is **no lockout or rate limit** beyond that; if
  the login page is reachable from the internet, put fail2ban or your reverse
  proxy's rate limiting in front of it.

## Deployment notes

- The API binds to `127.0.0.1` by default. To expose it beyond localhost:
  create a user account (`camoufox-pm user add <name>`) for humans and/or set
  `CPM_API_KEY` for machine clients, terminate TLS in a reverse proxy, and set
  `CPM_SECURE_COOKIES=1` so the session cookie is never sent over plain HTTP
  (the app itself does not serve TLS, so it cannot infer this behind a proxy).
- Set `CPM_CORS_ORIGINS` to the specific origins you trust; do not use a wildcard
  together with credentials.
- The proxy check connects to whatever host a request names and reports whether it
  answered and how fast. On an instance reachable beyond localhost without any
  authentication configured, that is an unauthenticated way to probe hosts and
  ports the server can reach but the caller cannot.

## Synchronization and update boundaries

Device synchronization is experimental and requires explicit administrator/device
setup and per-profile opt-in. The remaining external protocol/cryptographic
review gates promotion beyond experimental status; ordinary manager releases
retain that opt-in. See [the threat model](docs/stage-four-sync.md) and
[recovery guidance](docs/sync-recovery-guide.md).

Ordinary desktop releases are unsigned; publisher certificates and notarization
are not planned. Checksums verify download integrity, not publisher identity.
Automatic application replacement remains disabled under its separate production
trust/acceptance requirements. See [the release policy](docs/signing.md) and
[update/recovery contract](docs/update-recovery.md).
