# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-07

First public release after a comprehensive revamp.

### Added
- Opt-in API-key authentication (`CPM_API_KEY`) and environment-based settings.
- Proxy credentials are encrypted at rest with Fernet (`CPM_SECRET_KEY`).
- Test suite: unit, API integration, and opt-in real-browser smoke tests.
- GitHub Actions CI (Python 3.10–3.13, frontend build) and release workflow.
- `.github` issue/PR templates, `CONTRIBUTING`, `SECURITY`, and `CODE_OF_CONDUCT`.
- Next.js dev proxy for the API (no CORS in local development).

### Changed
- Upgraded Camoufox `0.4.11` → `0.5.4` (Firefox 135 → 152).
- Adopted a `src/` layout with the `camoufox_pm` package and `pyproject.toml` + uv.
- The API now binds to `127.0.0.1` by default with configurable CORS origins.
- Browser lifecycle extracted into a dedicated `BrowserSessionManager`.
- The whole codebase and web UI are now English-only and linted with ruff.

### Fixed
- Anti-detect settings now actually reach the browser — `to_camoufox_config()`
  previously returned an empty dict, so geolocation, WebRTC, and hardware
  overrides were silently ignored.
- Server-side profile search (`name` filter) now works instead of returning
  everything.
- Missing web API client (`@/lib/api`) restored, so the frontend builds again.
- Cloning or resetting a non-existent profile returns `404` instead of `500`.
- Browser PID tracking no longer falls back to a fake placeholder value.

### Removed
- Fingerprint generator no longer hand-crafts stale user-agent and WebGL values;
  Camoufox owns fingerprint generation for consistency.
- Committed profile data, leaked proxy credentials, and duplicate/backup files.

[Unreleased]: https://github.com/polyackiy/camoufox-profile-manager/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.1.0
