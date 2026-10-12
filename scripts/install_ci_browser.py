#!/usr/bin/env python3
"""Install Camoufox's paired browser/resources and reject upstream false success.

CI provides its read-only GITHUB_TOKEN before starting this process. Camoufox's
existing token hook authenticates the default GitHub release API requests; its
ordinary browser, model, add-on and GeoIP downloads remain unauthenticated.
This helper is for fresh CI caches with the default upstream configuration.
"""

import sys


def install_browser() -> str:
    from camoufox.__main__ import cli
    from camoufox.browser_pin import load_pin

    from camoufox_pm.core.browser_install import ensure_browser_ready, installed_browser

    result = cli.main(args=["fetch"], prog_name="camoufox", standalone_mode=False)
    if result not in (None, 0):
        raise RuntimeError("The upstream browser setup command failed")
    # Upstream fetch can print a release-API error and return zero. Required
    # files, model hashes and interface compatibility remain the authority.
    ensure_browser_ready()
    pin = load_pin()
    installed, version = installed_browser()
    if pin is None or not installed or version != pin.spec:
        raise RuntimeError("CI browser setup did not install the wrapper's paired build")
    return version


def main() -> int:
    try:
        version = install_browser()
    except Exception:
        # Do not render arbitrary exception data/request headers into CI logs.
        print(
            "Camoufox CI setup failed; inspect the upstream fetch output and retry.",
            file=sys.stderr,
        )
        return 1
    print(f"Camoufox CI browser and resources are ready: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
