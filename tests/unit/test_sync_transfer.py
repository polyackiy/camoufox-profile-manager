# ruff: noqa: F811
"""Real cryptography consumes the durable engine's selected signed commit."""

import pytest

from camoufox_pm.core.update_permissions import create_private_directory, verify_private_path
from camoufox_pm.sync.engine import EngineError
from camoufox_pm.sync.identity import generate_device
from camoufox_pm.sync.transfer import SnapshotTransfer
from tests.unit.test_sync_engine import held, system  # noqa: F401


@pytest.fixture
def transfer_area(system):
    engine, server, authority, temporary = system
    # pytest tmp_path has a private POSIX mode but does not establish the
    # protected Windows DACL required by the production crypto staging contract.
    # Runtime transfers always create an explicit private directory first.
    private = temporary / "transfers"
    create_private_directory(private)
    verify_private_path(private)
    return engine, server, authority, private


def test_encrypted_snapshot_is_selected_from_verified_history(transfer_area, monkeypatch):
    system = transfer_area
    engine, server, _, path = system
    held(system)
    source = path / "private.zip"
    source.write_bytes(b"session-cookies-and-local-storage")
    cipher = path / "cipher"
    transfer = SnapshotTransfer(engine, engine.client.keys)
    manifest, signature = transfer.encrypt("profile1", source, cipher)
    engine.publish("profile1", manifest, signature, cipher)
    selected, _, context, writer = transfer.selected("profile1")
    assert selected == manifest
    assert context.revision_id == manifest.revision_id
    assert writer.device_id == engine.device_id

    def download(revision, destination, sha, length):
        assert revision == manifest.revision_id
        destination.write_bytes(cipher.read_bytes())

    monkeypatch.setattr(engine.client, "download", download, raising=False)
    result = path / "restored.zip"
    assert transfer.download("profile1", path / "received", result) == manifest.revision_id
    assert result.read_bytes() == source.read_bytes()
    assert source.read_bytes() not in cipher.read_bytes()


def test_wrong_device_keys_rejected(system):
    engine, _, _, _ = system
    with pytest.raises(EngineError):
        SnapshotTransfer(engine, generate_device())


def test_relay_truncated_history_cannot_select_old_snapshot(transfer_area):
    system = transfer_area
    engine, server, _, path = system
    held(system)
    transfer = SnapshotTransfer(engine, engine.client.keys)
    source = path / "source"
    source.write_bytes(b"private")
    manifest, signature = transfer.encrypt("profile1", source, path / "cipher")
    engine.publish("profile1", manifest, signature, path / "cipher")
    server.events.pop()
    with pytest.raises(EngineError):
        transfer.selected("profile1")
