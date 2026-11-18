"""The optional host fails closed without launch ownership; no private diagnostics."""

import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from camoufox_pm.core import action_capture_host


@pytest.mark.parametrize(
    "config",
    [None, "{", "[]", "{}", '{"key":"zz"}', json.dumps({"key": "00" * 32, "addon_id": "expected"})],
)
def test_host_requires_private_launch_and_exact_extension(monkeypatch, config):
    if config is None:
        monkeypatch.delenv("CPM_ACTION_CAPTURE_PROBE", raising=False)
    else:
        monkeypatch.setenv("CPM_ACTION_CAPTURE_PROBE", config)
    monkeypatch.setattr(sys, "argv", ["native-host", "different"])
    transport = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(action_capture_host, "Client", transport)
    assert action_capture_host.main() == 1
    transport.assert_not_called()
    assert "CPM_ACTION_CAPTURE_PROBE" not in __import__("os").environ


def test_host_uses_framing_and_bound_launch_key_without_echo(monkeypatch):
    from camoufox_pm.core.action_capture import (
        decode_message,
        encode_message,
        message_proof,
        write_message,
    )

    monkeypatch.setattr(sys, "platform", "linux")
    key = b"k" * 32
    config = {
        "key": key.hex(),
        "addon_id": "expected",
        "address": "fixed-by-launch",
        "family": "AF_UNIX",
        "generation": "g",
    }
    monkeypatch.setenv("CPM_ACTION_CAPTURE_PROBE", json.dumps(config))
    monkeypatch.setattr(sys, "argv", ["native-host", "expected"])
    incoming = io.BytesIO()
    write_message(incoming, {"kind": "ready"})
    incoming.seek(0)
    output = io.BytesIO()
    monkeypatch.setattr(action_capture_host, "_NativeInput", lambda _peer: incoming)
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    sent = []
    responses = iter([{"challenge": "challenge"}, {"ok": True}, {"arm": True}])

    class Peer:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self, timeout):
            return True

        def recv_bytes(self, size):
            return encode_message(next(responses))

        def send_bytes(self, payload):
            sent.append(decode_message(payload))

    client = Mock(return_value=Peer())
    monkeypatch.setattr(action_capture_host, "Client", client)
    assert action_capture_host.main() == 1  # EOF revokes, no reconnect or replay
    client.assert_called_once_with("fixed-by-launch", family="AF_UNIX")
    assert len(sent) == 2
    for envelope in sent:
        assert envelope["proof"] == message_proof(key, "challenge", envelope["value"])
    assert key.hex().encode() not in output.getvalue()
