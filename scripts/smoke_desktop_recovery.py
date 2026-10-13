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
        log = database.parent / "update-control" / "recovery-command.log"
        try:
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
        except subprocess.TimeoutExpired as exc:

            def decoded(value):
                return (
                    value.decode("utf-8", errors="replace")
                    if isinstance(value, bytes)
                    else value or ""
                )

            files = sorted(str(path.relative_to(work)) for path in work.rglob("*"))
            recovery_output = (
                log.read_text(encoding="utf-8", errors="replace")
                if log.exists()
                else "<no recovery log>"
            )
            print(f"Frozen recovery timed out after {exc.timeout}s", file=sys.stderr)
            print(f"Partial stdout:\n{decoded(exc.stdout)}", file=sys.stderr)
            print(f"Partial stderr:\n{decoded(exc.stderr)}", file=sys.stderr)
            print(f"Temporary recovery log:\n{recovery_output}", file=sys.stderr)
            print(f"Temporary directory inventory: {json.dumps(files)}", file=sys.stderr)
            print(
                f"Application database created: {database.exists()}; local key created: {(database.parent / 'secret.key').exists()}",
                file=sys.stderr,
            )
            raise RuntimeError(
                "Frozen recovery did not finish; timeout diagnostics retained above"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"Frozen recovery exited {result.returncode}: {result.stdout}\n{result.stderr}"
            )
        output = result.stdout + (log.read_text(encoding="utf-8") if log.exists() else "")
        status = read_status(output)
        if (
            status.get("automatic_install_available") is not False
            or status.get("checkpoints") != []
        ):
            raise RuntimeError("Frozen recovery reported unexpected installation state")
        if database.exists() or (database.parent / "secret.key").exists():
            raise RuntimeError("Frozen recovery created application data or an encryption key")
        # These must dispatch before GUI/bootstrap even in a windowed frozen app.
        # No trust policy or receipt is supplied: refusal is the expected outcome.
        for arguments in (
            ["--update-helper", "apply"],
            ["--update-validate", str(work / "missing-receipt.json")],
        ):
            refused = subprocess.run(
                [str(executable), *arguments],
                cwd=work,
                env=environment,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if refused.returncode == 0:
                raise RuntimeError("Frozen internal update command accepted absent trust/receipt")
            if database.exists() or (database.parent / "secret.key").exists():
                raise RuntimeError("Frozen internal update command initialized application data")
        print("Frozen status/helper/validation dispatch passed without creating data or a key")


if __name__ == "__main__":
    main()
