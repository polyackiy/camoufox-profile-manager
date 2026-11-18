"""Bounded, authenticated input for the disabled interactive-capture feasibility gate.

No HTTP endpoint or normal profile launch uses this module. A native host signs
messages with a per-launch key that never enters extension/page storage. The
caller must additionally verify native-host ancestry and current browser identity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import struct
import time
from collections.abc import Callable
from typing import BinaryIO

MAX_MESSAGE_BYTES = 4096


class CaptureRejected(ValueError):
    """Fixed diagnostic: never include event content or launch credentials."""

    def __init__(self) -> None:
        super().__init__("Capture channel is no longer valid")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise CaptureRejected()
        result[key] = value
    return result


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise CaptureRejected()
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    if isinstance(value, list):
        for item in value:
            _finite(item)


def decode_message(payload: bytes) -> dict:
    try:
        if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
            raise CaptureRejected()
        value = json.loads(payload, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise CaptureRejected()
        _finite(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise CaptureRejected() from None


def encode_message(value: dict) -> bytes:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
            raise CaptureRejected()
        return payload
    except (TypeError, ValueError, RecursionError):
        raise CaptureRejected() from None


def _read(stream: BinaryIO, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = stream.read(length - len(result))
        if not chunk:
            raise CaptureRejected()
        result.extend(chunk)
    return bytes(result)


def read_message(stream: BinaryIO) -> dict:
    length = struct.unpack("=I", _read(stream, 4))[0]
    if not 0 < length <= MAX_MESSAGE_BYTES:
        raise CaptureRejected()
    return decode_message(_read(stream, length))


def write_message(stream: BinaryIO, value: dict) -> None:
    payload = encode_message(value)
    stream.write(struct.pack("=I", len(payload)) + payload)
    stream.flush()


def message_proof(key: bytes, challenge: str, value: dict) -> str:
    return hmac.new(key, challenge.encode() + encode_message(value), hashlib.sha256).hexdigest()


class CaptureGate:
    """One generation, one top-level tab/origin/viewport, no retries or resumption."""

    def __init__(
        self,
        key: bytes,
        generation: str,
        challenge: str,
        *,
        tab: int,
        origin: str,
        width: int,
        height: int,
        expires_at: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = key
        self._generation = generation
        self._challenge = challenge
        self._binding = {"tab": tab, "origin": origin, "width": width, "height": height}
        self._expires_at = expires_at
        self._clock = clock
        self._sequence = 0
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        self._key = b""

    def accept(self, value: dict, proof: str) -> dict:
        try:
            if self._stopped or self._clock() >= self._expires_at:
                raise CaptureRejected()
            if not isinstance(proof, str) or not hmac.compare_digest(
                message_proof(self._key, self._challenge, value), proof
            ):
                raise CaptureRejected()
            common = {
                "generation",
                "sequence",
                "kind",
                "tab",
                "origin",
                "width",
                "height",
                "x",
                "y",
            }
            kind = value.get("kind")
            if kind == "scroll":
                common |= {"delta_x", "delta_y"}
                for name in ("delta_x", "delta_y"):
                    if type(value.get(name)) is not int or not -2000 <= value[name] <= 2000:
                        raise CaptureRejected()
            elif kind != "click":
                raise CaptureRejected()
            if set(value) != common:
                raise CaptureRejected()
            if value["generation"] != self._generation or value["sequence"] != self._sequence + 1:
                raise CaptureRejected()
            for name, expected in self._binding.items():
                if type(value[name]) is not type(expected) or value[name] != expected:
                    raise CaptureRejected()
            for name, bound in (("x", value["width"]), ("y", value["height"])):
                if type(value[name]) is not int or not 0 <= value[name] < bound:
                    raise CaptureRejected()
            if type(value["sequence"]) is not int:
                raise CaptureRejected()
            self._sequence += 1
            return value.copy()
        except (ValueError, TypeError, KeyError):
            self.stop()
            raise CaptureRejected() from None
