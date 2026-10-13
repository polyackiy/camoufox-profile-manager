"""Protected storage and recovery adversarial tests (no real keychain writes)."""

import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

pytest.importorskip("cryptography.hazmat.primitives.kdf.argon2")
from camoufox_pm.sync import vault

SLOT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
PASSWORD = "independently chosen password"


def test_recovery_roundtrip_and_independent_secret():
    package, secret = vault.seal_recovery(b"owner recovery material")
    assert b"owner recovery material" not in package
    assert secret.encode() not in package
    assert vault.open_recovery(package, secret) == b"owner recovery material"
    _, other_secret = vault.seal_recovery(b"x")
    with pytest.raises(vault.VaultError):
        vault.open_recovery(package, other_secret)


@pytest.mark.parametrize(
    "transform",
    [
        lambda p: p[:-1],
        lambda p: p.replace(b'"version":1', b'"version":true'),
        lambda p: p.replace(b'"version":1', b'"version":2'),
        lambda p: p.replace(b'"version":1', b'"version":1,"version":1'),
        lambda p: p.replace(b'"version":1', b'"version":1,"iterations":999999999'),
        lambda p: b"[" * 2000,
        lambda p: b" " * (vault.MAX_PACKAGE + 1),
    ],
)
def test_recovery_malformed(transform):
    package, secret = vault.seal_recovery(b"secret")
    with pytest.raises(vault.VaultError):
        vault.open_recovery(transform(package), secret)


def test_recovery_ciphertext_tamper():
    package, secret = vault.seal_recovery(b"secret")
    obj = json.loads(package)
    ciphertext = vault._decode(obj["ciphertext"])
    obj["ciphertext"] = vault._encode(bytes([ciphertext[0] ^ 1]) + ciphertext[1:])
    with pytest.raises(vault.VaultError):
        vault.open_recovery(json.dumps(obj).encode(), secret)


@pytest.mark.parametrize(
    "payload",
    [b"", b"x" * (vault.MAX_PAYLOAD + 1), "not bytes"],
    ids=["empty", "oversize", "wrong-type"],
)
def test_payload_bounds(payload):
    with pytest.raises(vault.VaultError):
        vault.seal_recovery(payload)


def test_recovery_max_size():
    payload = b"x" * vault.MAX_PAYLOAD
    package, secret = vault.seal_recovery(payload)
    assert vault.open_recovery(package, secret) == payload


def test_file_vault_lifecycle(tmp_path):
    directory = tmp_path.resolve() / "vault"
    store = vault.EncryptedFileVault(directory, PASSWORD)
    assert store.get(SLOT) is None
    store.put(SLOT, b"private material")
    raw = (directory / (SLOT + ".key")).read_bytes()
    assert b"private material" not in raw and PASSWORD.encode() not in raw
    assert store.get(SLOT) == b"private material"
    store.put(SLOT, b"replacement")
    assert vault.EncryptedFileVault(directory, PASSWORD).get(SLOT) == b"replacement"
    with pytest.raises(vault.VaultError):
        vault.EncryptedFileVault(directory, "wrong password here").get(SLOT)
    store.delete(SLOT)
    store.delete(SLOT)
    assert store.get(SLOT) is None
    assert not list(directory.iterdir())


def test_slot_substitution_fails(tmp_path):
    directory = tmp_path.resolve() / "vault"
    store = vault.EncryptedFileVault(directory, PASSWORD)
    store.put(SLOT, b"authority")
    (directory / (SLOT + ".key")).rename(directory / (OTHER + ".key"))
    with pytest.raises(vault.VaultError):
        store.get(OTHER)


def test_fixed_kdf_rejects_injected_cost_before_derivation(tmp_path, monkeypatch):
    directory = tmp_path.resolve() / "vault"
    store = vault.EncryptedFileVault(directory, PASSWORD)
    store.put(SLOT, b"authority")
    path = directory / (SLOT + ".key")
    obj = json.loads(path.read_bytes())
    obj["memory_cost"] = 99999999999
    path.write_text(json.dumps(obj))
    monkeypatch.setattr(store, "_key", lambda _: pytest.fail("must validate before KDF"))
    with pytest.raises(vault.VaultError):
        store.get(SLOT)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode test")
def test_file_permissions_and_symlinks(tmp_path):
    directory = tmp_path.resolve() / "vault"
    store = vault.EncryptedFileVault(directory, PASSWORD)
    store.put(SLOT, b"authority")
    path = directory / (SLOT + ".key")
    assert path.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    path.chmod(0o644)
    with pytest.raises(vault.VaultError):
        store.get(SLOT)
    path.unlink()
    outside = tmp_path.resolve() / "outside"
    outside.write_bytes(b"untouched")
    path.symlink_to(outside)
    for operation in [
        lambda: store.get(SLOT),
        lambda: store.put(SLOT, b"key"),
        lambda: store.delete(SLOT),
    ]:
        with pytest.raises(vault.VaultError):
            operation()
    assert outside.read_bytes() == b"untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX special file test")
def test_special_file_no_block(tmp_path):
    directory = tmp_path.resolve() / "vault"
    store = vault.EncryptedFileVault(directory, PASSWORD)
    os.mkfifo(directory / (SLOT + ".key"), 0o600)
    with pytest.raises(vault.VaultError):
        store.get(SLOT)


def test_atomic_write_failure_preserves_previous(tmp_path, monkeypatch):
    directory = tmp_path.resolve() / "vault"
    store = vault.EncryptedFileVault(directory, PASSWORD)
    store.put(SLOT, b"previous")

    def fail(*args):
        raise OSError("SECRET path payload")

    monkeypatch.setattr(vault.os, "replace", fail)
    with pytest.raises(
        vault.VaultError, match="^Protected sync storage is unavailable or invalid$"
    ):
        store.put(SLOT, b"new")
    assert store.get(SLOT) == b"previous"
    assert len(list(directory.iterdir())) == 1


def test_concurrent_replace_is_complete(tmp_path):
    store = vault.EncryptedFileVault(tmp_path.resolve() / "vault", PASSWORD)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda data: store.put(SLOT, data), [b"first", b"second"]))
    assert store.get(SLOT) in (b"first", b"second")
    assert len(list(store.directory.iterdir())) == 1


@pytest.mark.parametrize(
    "slot", ["../escape", "authority", str(uuid.UUID(int=0)), SLOT.upper() + "x"]
)
def test_invalid_slots(tmp_path, slot):
    store = vault.EncryptedFileVault(tmp_path.resolve() / "vault", PASSWORD)
    for operation in [
        lambda: store.get(slot),
        lambda: store.put(slot, b"key"),
        lambda: store.delete(slot),
    ]:
        with pytest.raises(vault.VaultError):
            operation()


def fake_backend(monkeypatch, platform="darwin", selected=None):
    class Native:
        def __init__(self):
            self.values = {}

        def set_password(self, service, slot, password):
            self.values[service, slot] = password

        def get_password(self, service, slot):
            return self.values.get((service, slot))

        def delete_password(self, service, slot):
            del self.values[service, slot]

    native = Native()
    selected = native if selected is None else selected
    monkeypatch.setattr(sys, "platform", platform)

    def imported(module):
        if module == "keyring":
            return SimpleNamespace(get_keyring=lambda: selected)
        return SimpleNamespace(Keyring=Native, WinVaultKeyring=Native)

    monkeypatch.setattr(vault.importlib, "import_module", imported)
    return native


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_os_vault_supported_backend(monkeypatch, platform):
    native = fake_backend(monkeypatch, platform)
    store = vault.OSVault(SLOT)
    store.put(SLOT, b"device key")
    store.put(OTHER, b"authority key")
    assert store.get(SLOT) == b"device key"
    assert store.get(OTHER) == b"authority key"
    assert len({service for service, _ in native.values}) == 2
    if platform == "win32":
        assert native.persist == "local machine"
    store.delete(SLOT)
    assert store.get(SLOT) is None
    assert store.get(OTHER) == b"authority key"


def test_os_rejects_unknown_or_chained_backend(monkeypatch):
    fake_backend(monkeypatch, selected=SimpleNamespace())
    with pytest.raises(vault.VaultError):
        vault.OSVault(SLOT)


def test_os_locked_failure_no_fallback(monkeypatch):
    native = fake_backend(monkeypatch)
    store = vault.OSVault(SLOT)

    def locked(*args):
        raise RuntimeError("private username and payload")

    native.set_password = locked
    with pytest.raises(
        vault.VaultError, match="^Protected sync storage is unavailable or invalid$"
    ):
        store.put(SLOT, b"private key")
    assert native.values == {}


def test_file_oversize_fails_before_kdf_or_write(tmp_path, monkeypatch):
    store = vault.EncryptedFileVault(tmp_path.resolve() / "vault", PASSWORD)
    monkeypatch.setattr(store, "_key", lambda _: pytest.fail("KDF must not run"))
    with pytest.raises(vault.VaultError):
        store.put(SLOT, b"x" * (vault.MAX_PAYLOAD + 1))
    assert not list(store.directory.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode test")
def test_nonprivate_directory_rejected_without_chmod(tmp_path):
    directory = tmp_path.resolve() / "vault"
    directory.mkdir(mode=0o755)
    with pytest.raises(vault.VaultError):
        vault.EncryptedFileVault(directory, PASSWORD)
    assert directory.stat().st_mode & 0o777 == 0o755


def test_recovery_noncanonical_secret_rejected():
    package, secret = vault.seal_recovery(b"key")
    with pytest.raises(vault.VaultError):
        vault.open_recovery(package, secret + "=")


def test_recovery_and_file_domains_are_separate():
    package, secret = vault.seal_recovery(b"key")
    obj = json.loads(package)
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with pytest.raises(InvalidTag):
        AESGCM(vault._decode(secret)).decrypt(
            vault._decode(obj["nonce"]),
            vault._decode(obj["ciphertext"]),
            vault._FILE_AAD + SLOT.encode(),
        )


def test_os_read_error_does_not_look_like_empty_store(monkeypatch):
    native = fake_backend(monkeypatch)
    store = vault.OSVault(SLOT)

    def locked(*args):
        raise RuntimeError("locked")

    native.get_password = locked
    with pytest.raises(vault.VaultError):
        store.get(SLOT)
