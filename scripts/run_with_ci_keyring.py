"""Run a Linux acceptance command with a real, disposable Secret Service.

Only for ephemeral GitHub runners. Browser caches remain available; credential
storage/configuration and the D-Bus session are private to this command.
"""

import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path


def isolated_environment(directory):
    env = dict(os.environ)
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
        path = directory / name.lower()
        path.mkdir(mode=0o700)
        env[name] = str(path)
    for name in ("DBUS_SESSION_BUS_ADDRESS", "GNOME_KEYRING_CONTROL", "PYTHON_KEYRING_BACKEND"):
        env.pop(name, None)
    return env


def run_session(command):
    daemon = subprocess.Popen(
        ["gnome-keyring-daemon", "--foreground", "--components=secrets", "--unlock"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
    )
    try:
        daemon.stdin.write(secrets.token_hex(32).encode())
        daemon.stdin.close()
        subprocess.run(
            ["gdbus", "wait", "--session", "org.freedesktop.secrets"],
            check=True,
            timeout=20,
        )
        subprocess.run(command, check=True, timeout=2100)
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait(timeout=10)


def main():
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Run only on a disposable Linux GitHub Actions worker")
    command = sys.argv[1:]
    if command[:1] == ["--session"]:
        run_session(command[1:])
        return
    if command[:1] != ["--"] or len(command) < 2:
        raise SystemExit("Expected -- followed by an acceptance command")
    with tempfile.TemporaryDirectory(prefix="installed-native-keyring-") as temporary:
        subprocess.run(
            [
                "dbus-run-session",
                "--",
                sys.executable,
                str(Path(__file__).resolve()),
                "--session",
                *command[1:],
            ],
            env=isolated_environment(Path(temporary)),
            check=True,
            timeout=2160,
        )
    print("Disposable Secret Service session removed", flush=True)


if __name__ == "__main__":
    main()
