"""The capture gate authenticates its native host, never a page-supplied event."""

import hashlib
import hmac
import io
import json
import struct

import pytest

from camoufox_pm.core.action_capture import (
    CaptureGate,
    CaptureRejected,
    read_message,
    write_message,
)

KEY = b"k" * 32
GENERATION = "a" * 32
CHALLENGE = "b" * 64


def event(**changes):
    return dict(
        generation=GENERATION,
        sequence=1,
        kind="click",
        tab=3,
        origin="https://example.test",
        width=900,
        height=700,
        x=20,
        y=30,
        **changes,
    )


def signed(value):
    return hmac.new(
        KEY,
        CHALLENGE.encode()
        + json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
        hashlib.sha256,
    ).hexdigest()


def gate(clock=lambda: 1):
    return CaptureGate(
        KEY,
        GENERATION,
        CHALLENGE,
        tab=3,
        origin="https://example.test",
        width=900,
        height=700,
        expires_at=10,
        clock=clock,
    )


def test_accepted_message_needs_exact_capability_and_order():
    value = event()
    accepted = gate()
    assert accepted.accept(value, signed(value)) == value
    with pytest.raises(CaptureRejected):
        accepted.accept(value, signed(value))
    with pytest.raises(CaptureRejected):
        accepted.accept(value, signed(value))


@pytest.mark.parametrize(
    "change",
    [
        {"sequence": 2},
        {"generation": "c" * 32},
        {"tab": 4},
        {"origin": "https://other.test"},
        {"origin": "https://example.test/path?secret=1"},
        {"width": 901},
        {"height": 701},
        {"x": -1},
        {"x": 900},
        {"y": 700},
        {"x": True},
        {"x": 1.5},
        {"kind": "text"},
        {"text": "password"},
        {"kind": "scroll", "delta_x": 0, "delta_y": 3000},
    ],
)
def test_invalid_capture_invalidates_the_entire_generation(change):
    value = {**event(), **change}
    accepted = gate()
    with pytest.raises(CaptureRejected):
        accepted.accept(value, signed(value))
    with pytest.raises(CaptureRejected):
        accepted.accept(event(), signed(event()))


def test_wrong_key_expiry_and_stop_are_not_resumable():
    for accepted, proof in [(gate(), "0" * 64), (gate(clock=lambda: 10), signed(event()))]:
        with pytest.raises(CaptureRejected):
            accepted.accept(event(), proof)
    accepted = gate()
    accepted.stop()
    with pytest.raises(CaptureRejected):
        accepted.accept(event(), signed(event()))


def test_scroll_does_not_carry_text_or_dom():
    value = {**event(), "kind": "scroll", "delta_x": 0, "delta_y": -250, "delta_mode": 0}
    assert gate().accept(value, signed(value)) == value


@pytest.mark.parametrize(
    "payload", [b"", b"{", b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}']
)
def test_native_message_parser_rejects_ambiguous_or_non_object_input(payload):
    with pytest.raises(CaptureRejected):
        read_message(io.BytesIO(struct.pack("=I", len(payload)) + payload))


def test_message_frames_are_bounded_before_reading_the_body():
    with pytest.raises(CaptureRejected):
        read_message(io.BytesIO(struct.pack("=I", 4097)))
    with pytest.raises(CaptureRejected):
        read_message(io.BytesIO(b"\x01\x00"))
    with pytest.raises(CaptureRejected):
        read_message(io.BytesIO(struct.pack("=I", 4) + b"{}"))
    stream = io.BytesIO()
    write_message(stream, {"ok": True})
    stream.seek(0)
    assert read_message(stream) == {"ok": True}


@pytest.mark.parametrize("mode,delta", [(0, 250), (1, 3)])
def test_scroll_preserves_units_instead_of_treating_lines_as_pixels(mode, delta):
    value = {**event(), "kind": "scroll", "delta_x": 0, "delta_y": delta, "delta_mode": mode}
    assert gate().accept(value, signed(value)) == value


@pytest.mark.parametrize("mode,delta", [(2, 1), (True, 3), (1, 61), (0, 2001)])
def test_scroll_rejects_unknown_units_and_unit_specific_overflow(mode, delta):
    value = {**event(), "kind": "scroll", "delta_x": 0, "delta_y": delta, "delta_mode": mode}
    with pytest.raises(CaptureRejected):
        gate().accept(value, signed(value))
