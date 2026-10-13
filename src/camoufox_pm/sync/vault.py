"""Explicit protected stores for opaque sync keys; never silently fall back.

These adapters do not establish identity, freshness or authorization. The caller
must authenticate the owner ceremony before using authority-key slots. Python
cannot guarantee erasure of immutable bytes from process memory.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import stat
import sys
import threading
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from camoufox_pm.core.update_permissions import (
    create_private_directory,
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)

MAX_PAYLOAD = 65536
MAX_PACKAGE = 90000
_ERROR = "Protected sync storage is unavailable or invalid"
_RECOVERY_AAD = b"camoufox-pm/sync/recovery/v1\x00"
_FILE_AAD = b"camoufox-pm/sync/key-vault/v1\x00"


class VaultError(ValueError):
    """Operation failed without exposing private material or filesystem paths."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: Any, size: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_PACKAGE:
        raise VaultError(_ERROR)
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(raw) != value or (size is not None and len(raw) != size):
        raise VaultError(_ERROR)
    return raw


def _payload(value: bytes) -> None:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_PAYLOAD:
        raise VaultError(_ERROR)


def _identifier(value: str) -> str:
    if type(value) is not str or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise VaultError(_ERROR)
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise VaultError(_ERROR)
        result[key] = value
    return result


def _parse(package: bytes, fields: set[str]) -> dict[str, Any]:
    if type(package) is not bytes or not 1 <= len(package) <= MAX_PACKAGE:
        raise VaultError(_ERROR)
    obj = json.loads(package, object_pairs_hook=_pairs)
    if (
        type(obj) is not dict
        or set(obj) != fields
        or type(obj.get("version")) is not int
        or obj["version"] != 1
    ):
        raise VaultError(_ERROR)
    return obj


def _serialize(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("ascii")


def seal_recovery(payload: bytes) -> tuple[bytes, str]:
    """Return encrypted package and independent random 256-bit recovery secret."""
    try:
        _payload(payload)
        key, nonce = os.urandom(32), os.urandom(12)
        encrypted = AESGCM(key).encrypt(nonce, payload, _RECOVERY_AAD)
        return _serialize(
            {"version": 1, "nonce": _encode(nonce), "ciphertext": _encode(encrypted)}
        ), _encode(key)
    except Exception:
        raise VaultError(_ERROR) from None


def open_recovery(package: bytes, secret: str) -> bytes:
    try:
        obj = _parse(package, {"version", "nonce", "ciphertext"})
        ciphertext = _decode(obj["ciphertext"])
        if not 17 <= len(ciphertext) <= MAX_PAYLOAD + 16:
            raise VaultError(_ERROR)
        result = AESGCM(_decode(secret, 32)).decrypt(
            _decode(obj["nonce"], 12), ciphertext, _RECOVERY_AAD
        )
        _payload(result)
        return result
    except Exception:
        raise VaultError(_ERROR) from None


class OSVault:
    """Native keyring only. Backend selection errors never choose a file store."""

    def __init__(self, namespace: str):
        try:
            self.service = "camoufox-pm.sync." + _identifier(namespace)
            module, name = {
                "darwin": ("keyring.backends.macOS", "Keyring"),
                "win32": ("keyring.backends.Windows", "WinVaultKeyring"),
                "linux": ("keyring.backends.SecretService", "Keyring"),
            }[sys.platform]
            keyring = importlib.import_module("keyring")
            backend = keyring.get_keyring()
            if type(backend) is not getattr(importlib.import_module(module), name):
                raise VaultError(_ERROR)
            if sys.platform == "win32":
                backend.persist = "local machine"
            self._backend = backend
        except Exception:
            raise VaultError(_ERROR) from None

    def put(self, slot: str, payload: bytes) -> None:
        try:
            _payload(payload)
            self._backend.set_password(
                self.service + "." + _identifier(slot), slot, _encode(payload)
            )
            if self.get(slot) != payload:
                raise VaultError(_ERROR)
        except Exception:
            raise VaultError(_ERROR) from None

    def get(self, slot: str) -> bytes | None:
        try:
            result = self._backend.get_password(self.service + "." + _identifier(slot), slot)
            if result is None:
                return None
            raw = _decode(result)
            _payload(raw)
            return raw
        except Exception:
            raise VaultError(_ERROR) from None

    def delete(self, slot: str) -> None:
        try:
            if self.get(slot) is not None:
                self._backend.delete_password(self.service + "." + _identifier(slot), slot)
            if self.get(slot) is not None:
                raise VaultError(_ERROR)
        except Exception:
            raise VaultError(_ERROR) from None


class EncryptedFileVault:
    """Explicit password-unlocked store; fixed Argon2id, encrypted-only writes.

    Each slot replacement is atomic, last-writer-wins. Ceremony journals must
    serialize multi-slot transactions. The password is retained only in memory
    for this object's lifetime; dropping it does not guarantee memory erasure.
    """

    def __init__(self, directory: Path, password: str):
        try:
            if type(password) is not str or not 12 <= len(password) <= 1024:
                raise VaultError(_ERROR)
            self._password = password.encode("utf-8")
            self._lock = threading.RLock()
            self.directory = directory.absolute()
            reject_reparse_paths(self.directory)
            if not self.directory.exists():
                create_private_directory(self.directory)
            self._verify_directory()
        except Exception:
            raise VaultError(_ERROR) from None

    def _verify_directory(self) -> None:
        verify_private_path(self.directory)
        if not self.directory.is_dir():
            raise VaultError(_ERROR)

    def _key(self, salt: bytes) -> bytes:
        return Argon2id(salt=salt, length=32, iterations=3, lanes=1, memory_cost=65536).derive(
            self._password
        )

    def _path(self, slot: str) -> Path:
        self._verify_directory()
        return self.directory / (_identifier(slot) + ".key")

    def _sync_directory(self) -> None:
        if os.name != "nt":
            fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def put(self, slot: str, payload: bytes) -> None:
        with self._lock:
            return self._put(slot, payload)

    def _put(self, slot: str, payload: bytes) -> None:
        temporary = None
        try:
            _payload(payload)
            path = self._path(slot)
            reject_reparse_paths(path)
            if path.exists():
                verify_private_path(path)
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise VaultError(_ERROR)
            salt, nonce = os.urandom(16), os.urandom(12)
            ciphertext = AESGCM(self._key(salt)).encrypt(nonce, payload, _FILE_AAD + slot.encode())
            package = _serialize(
                {
                    "version": 1,
                    "salt": _encode(salt),
                    "nonce": _encode(nonce),
                    "ciphertext": _encode(ciphertext),
                }
            )
            temporary = self.directory / (".pending-" + str(uuid.uuid4()))
            fd = create_private_file(temporary)
            with os.fdopen(fd, "wb") as stream:
                stream.write(package)
                stream.flush()
                os.fsync(stream.fileno())
            self._verify_directory()
            reject_reparse_paths(path)
            os.replace(temporary, path)
            temporary = None
            self._sync_directory()
        except Exception:
            raise VaultError(_ERROR) from None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def get(self, slot: str) -> bytes | None:
        with self._lock:
            return self._get(slot)

    def _get(self, slot: str) -> bytes | None:
        try:
            path = self._path(slot)
            reject_reparse_paths(path)
            if not path.exists():
                return None
            verify_private_path(path)
            if not stat.S_ISREG(path.lstat().st_mode):
                raise VaultError(_ERROR)
            fd = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PACKAGE:
                    raise VaultError(_ERROR)
                if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
                    raise VaultError(_ERROR)
                obj = _parse(
                    stream.read(MAX_PACKAGE + 1), {"version", "salt", "nonce", "ciphertext"}
                )
            ciphertext = _decode(obj["ciphertext"])
            if not 17 <= len(ciphertext) <= MAX_PAYLOAD + 16:
                raise VaultError(_ERROR)
            result = AESGCM(self._key(_decode(obj["salt"], 16))).decrypt(
                _decode(obj["nonce"], 12), ciphertext, _FILE_AAD + slot.encode()
            )
            _payload(result)
            return result
        except Exception:
            raise VaultError(_ERROR) from None

    def delete(self, slot: str) -> None:
        with self._lock:
            return self._delete(slot)

    def _delete(self, slot: str) -> None:
        try:
            path = self._path(slot)
            reject_reparse_paths(path)
            path.unlink(missing_ok=True)
            self._sync_directory()
        except Exception:
            raise VaultError(_ERROR) from None
