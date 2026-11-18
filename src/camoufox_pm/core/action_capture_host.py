"""Native-messaging console host for explicit action capture launches.

This executable accepts no destinations or program paths from extension input.
Its launch environment supplies one ephemeral IPC address and signing key. The
controller must verify the actual host/browser ancestry before trusting its HMAC.
"""

from __future__ import annotations

import json
import os
import select
import sys
from multiprocessing.connection import Client

from .action_capture import (
    CaptureRejected,
    decode_message,
    encode_message,
    message_proof,
    read_message,
    write_message,
)


class _NativeInput:
    """Read Firefox frames without parking the host after manager revocation."""

    def __init__(self, peer) -> None:
        self._peer = peer
        self._fd = sys.stdin.fileno()

    def read(self, size: int) -> bytes:
        if sys.platform == "win32":
            import _winapi
            import msvcrt

            handle = msvcrt.get_osfhandle(self._fd)
            while not self._peer.poll(0.025):
                available, _ = _winapi.PeekNamedPipe(handle)
                if available:
                    return os.read(self._fd, min(size, available))
        else:
            while not self._peer.poll(0):
                if select.select([self._fd], [], [], 0.025)[0]:
                    return os.read(self._fd, size)
        # IPC must be silent while waiting for the next Firefox message. EOF
        # or unexpected readability ends the host and disconnects the add-on.
        raise CaptureRejected()


def main() -> int:
    try:
        config = json.loads(os.environ.pop("CPM_ACTION_CAPTURE_PROBE"))
        key = bytes.fromhex(config["key"])
        if len(key) != 32 or sys.argv[-1] != config["addon_id"]:
            raise CaptureRejected()
        if sys.platform == "win32":
            import msvcrt

            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        with Client(config["address"], family=config["family"]) as peer:
            if not peer.poll(5):
                raise CaptureRejected()
            challenge = decode_message(peer.recv_bytes(4096))["challenge"]
            hello = {"kind": "host", "pid": os.getpid(), "generation": config["generation"]}
            peer.send_bytes(
                encode_message({"value": hello, "proof": message_proof(key, challenge, hello)})
            )
            if not peer.poll(5) or decode_message(peer.recv_bytes(4096)) != {"ok": True}:
                raise CaptureRejected()
            # One message/ack at a time; a dropped response never resends input.
            native = _NativeInput(peer)
            for index in range(64):
                value = read_message(native)
                peer.send_bytes(
                    encode_message({"value": value, "proof": message_proof(key, challenge, value)})
                )
                # The first ready message can wait for the manager's review.
                # All ordinary acknowledgements retain the short timeout.
                wait_seconds = 65 if index == 0 and value.get("kind") == "ready" else 5
                if not peer.poll(wait_seconds):
                    raise CaptureRejected()
                response = decode_message(peer.recv_bytes(4096))
                write_message(sys.stdout.buffer, response)
        return 0
    except (OSError, EOFError, KeyError, TypeError, ValueError):
        # No private values, exceptions or tracebacks on either output stream.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
