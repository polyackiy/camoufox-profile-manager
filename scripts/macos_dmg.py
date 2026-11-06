"""Create a macOS disk image without publishing partially written artifacts."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def create_dmg(stage: Path, output: Path) -> None:
    # macOS can report a transient Resource busy while hdiutil handles the
    # source folder. Retry only that error, on a fresh output path each time.
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix=".dmg-", dir=output.parent) as work:
            candidate = Path(work) / output.name
            result = subprocess.run(
                [
                    "hdiutil",
                    "create",
                    "-volname",
                    "Camoufox Profile Manager",
                    "-srcfolder",
                    str(stage),
                    "-format",
                    "UDZO",
                    str(candidate),
                ],
                env={**os.environ, "LC_ALL": "C"},
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            if result.returncode == 0:
                candidate.replace(output)
                return
            if attempt == 2 or "Resource busy" not in result.stderr:
                result.check_returncode()
        time.sleep(2 * (attempt + 1))
