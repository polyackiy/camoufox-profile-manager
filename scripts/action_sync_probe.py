"""Disposable native-messaging gate, never a controller for real user profiles.

This harness creates its own browser home, add-on and native-host registration.
All captured input stays in memory; output is allowlisted pass/fail metadata.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import shlex
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from multiprocessing.connection import Client, Listener
from pathlib import Path
from urllib.parse import urlsplit

import psutil

from camoufox_pm.core.action_capture import (
    CaptureGate,
    CaptureRejected,
    decode_message,
    encode_message,
    message_proof,
)
from camoufox_pm.core.update_permissions import create_private_directory, reject_reparse_paths

EXTENSION_FILES = Path(__file__).with_name("action_capture_extension")


def browser_ancestor(pid: int, profile: Path, executable: Path) -> tuple[int, float]:
    """Bind to an actual browser ancestor, not a claimed profile/extension ID."""
    try:
        for process in psutil.Process(pid).parents():
            if Path(process.exe()).resolve() != executable.resolve():
                continue
            args = process.cmdline()
            if "-profile" not in args:
                continue
            index = args.index("-profile") + 1
            if index < len(args) and Path(args[index]).resolve() == profile.resolve():
                return process.pid, process.create_time()
    except (psutil.Error, OSError, ValueError):
        pass
    raise CaptureRejected()


class ProbeBroker:
    """One leader, one connection, one bounded in-memory stream. No HTTP."""

    def __init__(self, root: Path, profile: Path, executable: Path, origin: str) -> None:
        self.key = secrets.token_bytes(32)
        self.generation = secrets.token_hex(16)
        self.challenge = secrets.token_hex(32)
        self.profile = profile
        self.executable = executable
        self.origin = origin
        self.family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
        self.address = (
            rf"\\.\pipe\cpm-capture-{self.generation}"
            if sys.platform == "win32"
            else str(root / "ipc")
        )
        self.events: queue.Queue = queue.Queue(maxsize=16)
        self.accepted = threading.Event()
        self.phase = "waiting-host"
        self.armed = threading.Event()
        self.closed = threading.Event()
        self.stop_requested = threading.Event()
        self.listener = Listener(self.address, family=self.family)
        if self.family == "AF_UNIX":
            Path(self.address).chmod(0o600)
        self.gate = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def environment(self, addon_id: str) -> str:
        return json.dumps(
            {
                "address": self.address,
                "family": self.family,
                "key": self.key.hex(),
                "generation": self.generation,
                "addon_id": addon_id,
            }
        )

    def _receive(self, peer):
        deadline = time.monotonic() + 15
        while not peer.poll(0.1):
            if self.stop_requested.is_set() or time.monotonic() >= deadline:
                raise CaptureRejected()
        envelope = decode_message(peer.recv_bytes(4096))
        if set(envelope) != {"value", "proof"}:
            raise CaptureRejected()
        value, proof = envelope["value"], envelope["proof"]
        if (
            not isinstance(value, dict)
            or not isinstance(proof, str)
            or not hmac.compare_digest(proof, message_proof(self.key, self.challenge, value))
        ):
            raise CaptureRejected()
        return value, proof

    def _serve(self):
        try:
            with self.listener.accept() as peer:
                self.accepted.set()
                self.phase = "host-authentication"
                peer.send_bytes(encode_message({"challenge": self.challenge}))
                hello, _ = self._receive(peer)
                if (
                    set(hello) != {"kind", "pid", "generation"}
                    or hello["kind"] != "host"
                    or hello["generation"] != self.generation
                    or type(hello["pid"]) is not int
                ):
                    raise CaptureRejected()
                identity = browser_ancestor(hello["pid"], self.profile, self.executable)
                peer.send_bytes(encode_message({"ok": True}))
                self.phase = "waiting-tab"
                ready, _ = self._receive(peer)
                self.phase = "tab-validation"
                if (
                    set(ready) != {"kind", "tab", "generation", "origin", "width", "height"}
                    or ready["kind"] != "ready"
                    or ready["origin"] != self.origin
                    or ready["generation"] != self.generation
                ):
                    raise CaptureRejected()
                if (
                    any(type(ready[name]) is not int for name in ("tab", "width", "height"))
                    or ready["width"] != 900
                    or ready["height"] != 700
                ):
                    raise CaptureRejected()
                self.phase = "arming"
                self.gate = CaptureGate(
                    self.key,
                    self.generation,
                    self.challenge,
                    tab=ready["tab"],
                    origin=self.origin,
                    width=900,
                    height=700,
                    expires_at=time.monotonic() + 120,
                )
                peer.send_bytes(encode_message({"arm": True}))
                self.armed.set()
                self.phase = "capture"
                while not self.stop_requested.is_set():
                    value, proof = self._receive(peer)
                    if browser_ancestor(hello["pid"], self.profile, self.executable) != identity:
                        raise CaptureRejected()
                    accepted = self.gate.accept(value, proof)
                    self.events.put_nowait(accepted)
                    peer.send_bytes(encode_message({"accepted": True}))
        except (OSError, ValueError, EOFError, queue.Full):
            pass
        finally:
            if self.gate is not None:
                self.gate.stop()
            self.key = b""
            self.closed.set()

    def close(self):
        self.stop_requested.set()
        # Wake an unused accept without a public listener or unbounded join.
        if self.thread.is_alive() and not self.accepted.is_set():
            try:
                with Client(self.address, family=self.family):
                    pass
            except OSError:
                pass
        self.thread.join(3)
        self.listener.close()
        if self.thread.is_alive():
            raise RuntimeError("Capture probe did not stop")
        while not self.events.empty():
            self.events.get_nowait()


@contextmanager
def native_registration(root: Path, host: str, addon_id: str, executable: Path):
    """Register only a new random host; never replace another install's manifest."""
    # Firefox on macOS resolves its real per-user native-host directory even
    # when HOME is overridden. Register one random name there explicitly, and
    # remove only our own identical manifest. Browser profiles remain disposable.
    home = Path.home().resolve()
    if sys.platform == "darwin":
        directory = home / "Library/Application Support/Mozilla/NativeMessagingHosts"
    elif sys.platform == "win32":
        directory = root / "native-manifests"
    else:
        directory = home / ".mozilla/native-messaging-hosts"
    reject_reparse_paths(directory)
    created = []
    missing = directory
    while not missing.exists():
        created.append(missing)
        missing = missing.parent
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / (host + ".json")
    payload = json.dumps(
        {
            "name": host,
            "description": "CPM disposable capture gate",
            "path": str(executable),
            "type": "stdio",
            "allowed_extensions": [addon_id],
        }
    )
    with manifest.open("x") as stream:
        stream.write(payload)
    registry_path = rf"Software\Mozilla\NativeMessagingHosts\{host}"
    registered = False
    try:
        if sys.platform == "win32":
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path):
                    raise RuntimeError("Native-host name is already registered")
            except FileNotFoundError:
                pass
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
            registered = True
        yield home
    finally:
        if registered:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
                if winreg.QueryValueEx(key, "")[0] != str(manifest):
                    raise RuntimeError("Native-host registration changed during the probe")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)
        if manifest.exists() and manifest.read_text() == payload:
            manifest.unlink()
        for path in created:
            try:
                path.rmdir()
            except OSError:
                break


def prepare_addon(root: Path, broker: ProbeBroker, host: str, addon_id: str) -> Path:
    addon = root / "addon"
    addon.mkdir()
    parsed = urlsplit(broker.origin)
    pattern = f"{parsed.scheme}://{parsed.hostname}/*"
    manifest = {
        "manifest_version": 2,
        "name": "CPM disposable action capture",
        "version": "0.1",
        "browser_specific_settings": {"gecko": {"id": addon_id}},
        "permissions": ["nativeMessaging", "tabs", pattern],
        "background": {"scripts": ["config.js", "background.js"]},
        "content_scripts": [
            {
                "matches": [pattern],
                "js": ["content.js"],
                "run_at": "document_idle",
                "all_frames": False,
            }
        ],
    }
    (addon / "manifest.json").write_text(json.dumps(manifest))
    (addon / "config.js").write_text(
        "const CPM_CAPTURE_CONFIG = "
        + json.dumps({"host": host, "generation": broker.generation, "origin": broker.origin})
        + ";"
    )
    for name in ("background.js", "content.js"):
        (addon / name).write_bytes((EXTENSION_FILES / name).read_bytes())
    return addon


def source_host(root: Path) -> Path:
    if sys.platform == "win32":
        raise RuntimeError("Windows native messaging requires the frozen host executable")
    executable = root / "native-host"
    repository = Path(__file__).resolve().parents[1]
    executable.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(repository / "scripts/action_capture_probe_entry.py"))
        + ' "$@"\n'
    )
    executable.chmod(0o700)
    return executable


@contextmanager
def capture_probe(origin: str, *, frozen_host: Path | None = None):
    from camoufox.multiversion import get_active_path
    from camoufox.pkgman import launch_path

    active = get_active_path()
    if active is None:
        raise RuntimeError("Install the paired test browser before the capture probe")
    with tempfile.TemporaryDirectory(prefix="cp-") as temporary:
        root = Path(temporary).resolve() / "r"
        create_private_directory(root)
        profile = root / "leader"
        host = "cpm.capture." + secrets.token_hex(8)
        addon_id = "capture-" + secrets.token_hex(8) + "@camoufox-pm.local"
        executable = frozen_host or source_host(root)
        broker = ProbeBroker(root, profile, Path(launch_path(active)), origin)
        try:
            with native_registration(root, host, addon_id, executable) as home:
                addon = prepare_addon(root, broker, host, addon_id)
                yield (
                    root,
                    broker,
                    {
                        "addons": [str(addon)],
                        "user_data_dir": str(profile),
                        "env": dict(
                            os.environ,
                            HOME=str(home),
                            PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
                            CPM_ACTION_CAPTURE_PROBE=broker.environment(addon_id),
                        ),
                    },
                )
        finally:
            broker.close()
