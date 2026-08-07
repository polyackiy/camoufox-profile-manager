# Releasing

Releases are cut by pushing a tag. The `release.yml` workflow then builds a wheel
(with the web UI bundled in), attaches it to the GitHub Release, and — if enabled
— publishes to PyPI.

## Cut a release

1. Update `CHANGELOG.md` (move items from `Unreleased` into the new version) and
   bump `version` in `pyproject.toml`.
2. Tag and push:
   ```bash
   git tag -a v0.1.1 -m "v0.1.1"
   git push origin v0.1.1
   ```
3. The workflow builds `camoufox_profile_manager-<version>-py3-none-any.whl` (with
   the bundled UI) and attaches it to the Release. Users can then:
   ```bash
   pip install https://github.com/polyackiy/camoufox-profile-manager/releases/download/v0.1.1/camoufox_profile_manager-0.1.1-py3-none-any.whl
   camoufox-pm
   ```
   No Node.js needed — the UI is inside the wheel.

## Publishing to PyPI (optional)

PyPI publishing is off by default and uses **Trusted Publishing** (OIDC — no API
tokens). One-time setup by a maintainer:

1. Create the project on PyPI (or reserve the name `camoufox-profile-manager`).
2. On PyPI → the project → *Publishing* → add a **GitHub Actions** trusted
   publisher:
   - Owner: `polyackiy`
   - Repository: `camoufox-profile-manager`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. In the GitHub repo → *Settings* → *Environments*, create an environment named
   `pypi` (add reviewers if you want manual approval).
4. In the GitHub repo → *Settings* → *Secrets and variables* → *Actions* →
   *Variables*, set `PUBLISH_TO_PYPI = true`.

After that, every tagged release also uploads the wheel + sdist to PyPI, enabling:

```bash
pip install camoufox-profile-manager
camoufox-pm
```
