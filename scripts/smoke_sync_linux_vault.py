"""Exercise real Secret Service in a disposable CI D-Bus session."""

import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path


def session():
    smoke = Path(__file__).with_name("smoke_sync_native_vault.py")
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
        subprocess.run([sys.executable, str(smoke)], check=True, timeout=30)
        # A fresh process must fail closed when the bus disappears. No keyring
        # backend override is used in either case: test normal backend selection.
        unavailable = dict(os.environ)
        unavailable["DBUS_SESSION_BUS_ADDRESS"] = (
            "unix:path=" + os.environ["XDG_RUNTIME_DIR"] + "/missing-bus"
        )
        subprocess.run(
            [sys.executable, str(smoke), "--unavailable"],
            env=unavailable,
            check=True,
            timeout=20,
        )
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait(timeout=10)


def main():
    if sys.platform != "linux" or os.environ.get("CI") != "true":
        raise SystemExit("Run only on a disposable Linux CI runner")
    if sys.argv[1:] == ["--session"]:
        session()
        return
    with tempfile.TemporaryDirectory(prefix="sync-secret-service-") as directory:
        env = dict(os.environ)
        for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            path = Path(directory) / name.lower()
            path.mkdir(mode=0o700)
            env[name] = str(path)
        for name in ("DBUS_SESSION_BUS_ADDRESS", "GNOME_KEYRING_CONTROL", "PYTHON_KEYRING_BACKEND"):
            env.pop(name, None)
        subprocess.run(
            ["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "--session"],
            env=env,
            check=True,
            timeout=120,
        )
    print("Isolated Linux Secret Service acceptance passed; temporary keyring removed")


if __name__ == "__main__":
    main()
