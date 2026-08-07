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

## Deployment notes

- The API binds to `127.0.0.1` by default. Only expose it beyond localhost behind
  authentication (set `CPM_API_KEY`) and a TLS-terminating reverse proxy.
- Set `CPM_CORS_ORIGINS` to the specific origins you trust; do not use a wildcard
  together with credentials.
