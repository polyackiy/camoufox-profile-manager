# Roadmap

This is a rough, non-binding plan. Priorities may change based on feedback.
Track progress in [GitHub Issues](https://github.com/polyackiy/camoufox-profile-manager/issues).

## Now (0.1.x)

- Stabilize the REST API and web UI.
- Broaden fingerprint coverage exposed through the profile settings.
- Fill gaps in test coverage (more integration and browser tests).

## Next

- Authentication for multi-user / hosted deployments (beyond the optional API key).
- Task scheduling and automated fingerprint rotation.
- Proxy testing and health checks in the UI.
- Windows App-Bound Encryption support for the Chrome-migration module.

## Later / ideas

- PyPI package once the browser-binary bootstrap is smoothed out.
- Docker images and a one-command deploy.
- Internationalized (i18n) web UI.
- PostgreSQL storage option for larger deployments.

## Non-goals

- Bundling or redistributing the Camoufox browser binary (fetched from upstream).
- Anything that facilitates violating a site's terms of service.
