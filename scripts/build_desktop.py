#!/usr/bin/env python3
"""Build the standalone desktop app with PyInstaller.

Builds the web UI into the package, then runs PyInstaller against
``packaging/camoufox-pm.spec``. On macOS this also produces a ``.app`` bundle.
The Camoufox browser binary is not bundled — it is fetched at first run.

    python scripts/build_desktop.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_webui.py")], check=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(ROOT / "packaging" / "camoufox-pm.spec"),
            "--noconfirm",
            "--clean",
        ],
        cwd=ROOT,
        check=True,
    )
    print("Desktop build complete — see dist/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
