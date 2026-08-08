# Releasing

Releases are cut by pushing a tag. The `release.yml` workflow then builds a wheel
(with the web UI bundled in), attaches it to the GitHub Release, and — if enabled
— publishes to PyPI.

## Cut a release

1. Update `CHANGELOG.md` (move items from `Unreleased` into the new version) and
   bump `version` in `pyproject.toml` and `web/package.json`. Nothing else carries
   the number: the package reads it from its own installed metadata, so there is
   no third copy to forget.
2. Tag and push, substituting the version you just set:
   ```bash
   VERSION=0.2.0
   git tag -a "v$VERSION" -m "v$VERSION"
   git push origin "v$VERSION"
   ```
3. The workflow builds `camoufox_profile_manager-<version>-py3-none-any.whl` (with
   the bundled UI) and attaches it to the Release. Users can then install that
   wheel's URL from the [releases
   page](https://github.com/polyackiy/camoufox-profile-manager/releases) and run
   `camoufox-pm` — no Node.js needed, the UI is inside the wheel.

   Worth doing once per release: install the built wheel into a clean environment
   and ask it what it is. `curl localhost:8000/health` reporting the wrong version
   is how the stale `__version__` copy was caught.

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
