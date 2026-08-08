# Security Policy

## Supported versions

This project is pre-1.0 and under active development. Security fixes are applied
to the latest release only.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

## Reporting a vulnerability

Please report security issues privately using GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" under the repository's **Security** tab), rather than
opening a public issue.

Please include a description of the issue, the affected component, and steps to
reproduce. We aim to acknowledge reports within a few days.

## Handling secrets

- **Never commit real proxy credentials, API keys, or profile data.** The
  repository ships only synthetic examples. `data/`, `*.db`, `.env`, and profile
  directories are git-ignored.
- Proxy passwords are encrypted at rest when `CPM_SECRET_KEY` is set (see
  `.env.example`). Generate a key with:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- If a credential is ever committed by mistake, **rotate it immediately** — removing
  it from the latest commit does not remove it from git history.

## Authentication

The API has three configuration states, decided by what exists rather than by a
switch, so the configuration cannot contradict the database:

1. **Nothing configured** — no user accounts, no `CPM_API_KEY`. The API is
   open. This is the default and is acceptable only because the app binds to
   `127.0.0.1`; it is how most single-person installs run.
2. **`CPM_API_KEY` set** — every request must carry the key in `X-API-Key`,
   compared in constant time. This is the machine-to-machine path: one shared
   secret, no identity.
3. **User accounts exist** (`camoufox-pm user add <name>`) — humans log in with
   a username and password; a valid session **or** the API key passes. Accounts
   are created, changed and removed only from the CLI — there is no
   self-registration, so creating an account requires shell access to the host.
   Removing the last account turns login back off.

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
