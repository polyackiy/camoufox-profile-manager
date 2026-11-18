"""Disposable native-messaging gate, never a controller for real user profiles.

This harness creates disposable profiles, an add-on and an owned temporary
per-user native-host registration, preserving unrelated registrations.
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

import psutil

from camoufox_pm.core.action_capture import (
    CaptureGate,
    CaptureRejected,
    decode_message,
    encode_message,
    message_proof,
)
from camoufox_pm.core.action_capture_launch import native_registration, prepare_capture_addon
from camoufox_pm.core.update_permissions import create_private_directory


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

    def _receive(self, peer, timeout=15):
        deadline = time.monotonic() + timeout
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
                    value, proof = self._receive(peer, timeout=120)
                    self.phase = "event-validation"
                    if browser_ancestor(hello["pid"], self.profile, self.executable) != identity:
                        raise CaptureRejected()
                    accepted = self.gate.accept(value, proof)
                    self.events.put_nowait(accepted)
                    self.phase = "capture"
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


def prepare_addon(root: Path, broker: ProbeBroker, host: str, addon_id: str) -> Path:
    return prepare_capture_addon(
        root, origin=broker.origin, generation=broker.generation, host=host, addon_id=addon_id
    )


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
