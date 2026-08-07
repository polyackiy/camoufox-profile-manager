#!/usr/bin/env python3
"""Build the web UI as a static export and bundle it into the Python package.

Runs ``npm run build:static`` in ``web/`` and copies the result into
``src/camoufox_pm/webui/`` so ``camoufox-pm`` (and an installed wheel) can serve
the UI on the same origin as the API. Requires Node.js.

    python scripts/build_webui.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = WEB / "out"
DEST = ROOT / "src" / "camoufox_pm" / "webui"


def _npm() -> str:
    """Resolve the npm executable (npm.cmd on Windows)."""
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm not found — install Node.js 20+")
    return npm


def main() -> int:
    if not (WEB / "package.json").exists():
        print("web/ not found; run from the repository root", file=sys.stderr)
        return 1

    npm = _npm()
    if not (WEB / "node_modules").exists():
        print("Installing web dependencies...")
        subprocess.run([npm, "ci"], cwd=WEB, check=True)

    print("Building static export...")
    # Pass NEXT_EXPORT via the environment (cross-platform) rather than an inline
    # shell prefix, so the build works on Windows too.
    env = {**os.environ, "NEXT_EXPORT": "1"}
    subprocess.run([npm, "run", "build:static"], cwd=WEB, check=True, env=env)

    if not OUT.is_dir():
        print("Expected export at web/out was not produced", file=sys.stderr)
        return 1

    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(OUT, DEST)
    print(f"Bundled web UI into {DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
