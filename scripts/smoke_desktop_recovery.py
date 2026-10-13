#!/usr/bin/env python3
"""CI-only smoke of the built desktop recovery dispatcher; no browser launch."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def executable_path() -> Path:
    if sys.platform == "darwin":
        return ROOT / "dist" / "Camoufox Profile Manager.app" / "Contents" / "MacOS" / "camoufox-pm"
    name = "camoufox-pm.exe" if sys.platform == "win32" else "camoufox-pm"
    return ROOT / "dist" / "camoufox-pm" / name


def read_status(output: str) -> dict:
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("state") == "idle":
            return value
    raise RuntimeError("Frozen recovery command did not emit an idle JSON status")


def main() -> None:
    executable = executable_path()
    if not executable.is_file():
        raise RuntimeError(f"Built desktop executable not found: {executable}")
    with tempfile.TemporaryDirectory(prefix="desktop-recovery-smoke-") as temporary:
        work = Path(temporary)
        database = work / "installation data" / "profiles.db"
        environment = os.environ.copy()
        for key in tuple(environment):
            if key.startswith("CPM_"):
                del environment[key]
        environment["CPM_DB_PATH"] = str(database)
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [str(executable), "--desktop", "update-recovery", "status"],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Frozen recovery exited {result.returncode}: {result.stdout}\n{result.stderr}"
            )
        log = database.parent / "update-control" / "recovery-command.log"
        output = result.stdout + (log.read_text(encoding="utf-8") if log.exists() else "")
        status = read_status(output)
        if (
            status.get("automatic_install_available") is not False
            or status.get("checkpoints") != []
        ):
            raise RuntimeError("Frozen recovery reported unexpected installation state")
        if database.exists() or (database.parent / "secret.key").exists():
            raise RuntimeError("Frozen recovery created application data or an encryption key")
        print("Frozen desktop recovery status passed without creating application data or a key")


if __name__ == "__main__":
    main()
