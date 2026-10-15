"""Run native installed acceptance in a disposable macOS default Keychain."""

import os
import secrets
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def security(*arguments):
    try:
        result = subprocess.run(
            ["security", *arguments],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Disposable CI Keychain command unavailable or timed out") from None
    if result.returncode:
        # Never echo command arguments: creation/unlock carry a disposable secret.
        raise RuntimeError("Disposable CI Keychain operation failed")
    return result.stdout


def main():
    if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Run only on a disposable macOS GitHub Actions worker")
    if sys.argv[1:2] != ["--"] or len(sys.argv) < 3:
        raise SystemExit("Expected -- followed by an acceptance command")
    search = shlex.split(security("list-keychains", "-d", "user"))
    default = shlex.split(security("default-keychain", "-d", "user"))
    if len(default) != 1:
        raise RuntimeError("Cannot preserve the CI default keychain")
    with tempfile.TemporaryDirectory(prefix="installed-native-keychain-") as directory:
        keychain = str(Path(directory) / "acceptance.keychain-db")
        password = secrets.token_urlsafe(32)
        security("create-keychain", "-p", password, keychain)
        try:
            security("set-keychain-settings", "-lut", "3600", keychain)
            security("unlock-keychain", "-p", password, keychain)
            security("list-keychains", "-d", "user", "-s", keychain)
            security("default-keychain", "-d", "user", "-s", keychain)
            environment = dict(os.environ, CPM_CI_ISOLATED_KEYCHAIN="1")
            subprocess.run(sys.argv[2:], env=environment, check=True, timeout=2100)
        finally:
            # Restore both settings even if one cleanup operation fails.
            import contextlib

            with contextlib.ExitStack() as cleanup:
                cleanup.callback(security, "delete-keychain", keychain)
                cleanup.callback(security, "default-keychain", "-d", "user", "-s", default[0])
                cleanup.callback(security, "list-keychains", "-d", "user", "-s", *search)
        if Path(keychain).exists():
            raise RuntimeError("Disposable CI keychain was not removed")
    print("Original keychain settings restored; disposable native keychain removed", flush=True)


if __name__ == "__main__":
    main()
