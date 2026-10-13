"""Durable CAS control metadata, outside the rollbackable application database.

Only public metadata belongs here. Private keys, passwords, session content and
recovery secrets belong in a vault. This store guarantees local serialization,
not freshness after disk rollback or protection against the local OS account.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from camoufox_pm.core.update_permissions import (
    create_private_directory,
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)

MAX_PAYLOAD = 65536
MAX_BATCH = 128
MAX_REVISION = 2**53 - 1
_ERROR = "Protected sync control operation failed"
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")
_SCHEMA = "CREATE TABLE records (key TEXT PRIMARY KEY NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), payload TEXT NOT NULL)"
_APPLICATION_ID = 0x43504D53


class ControlError(ValueError):
    """Invalid or unavailable storage; errors contain no private details."""


class ControlConflict(ControlError):
    """A record already exists or its expected revision no longer matches."""


def _key(value: str, *, prefix: bool = False) -> str:
    if type(value) is not str or not ((prefix and value == "") or _KEY.fullmatch(value)):
        raise ControlError(_ERROR)
    return value


def _revision(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_REVISION:
        raise ControlError(_ERROR)
    return value


def _walk(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ControlError(_ERROR)
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ControlError(_ERROR)
            _walk(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _walk(child, depth + 1)
    elif value is None or type(value) in (str, bool):
        pass
    elif type(value) is int:
        if not -MAX_REVISION <= value <= MAX_REVISION:
            raise ControlError(_ERROR)
    elif type(value) is float:
        if not math.isfinite(value):
            raise ControlError(_ERROR)
    else:
        raise ControlError(_ERROR)


def _encode(payload: dict[str, Any]) -> str:
    if type(payload) is not dict:
        raise ControlError(_ERROR)
    _walk(payload)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) > MAX_PAYLOAD:
        raise ControlError(_ERROR)
    return encoded


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControlError(_ERROR)
        result[key] = value
    return result


def _decode(payload: str) -> dict[str, Any]:
    if type(payload) is not str or len(payload) > MAX_PAYLOAD:
        raise ControlError(_ERROR)
    result = json.loads(payload, object_pairs_hook=_pairs)
    _encode(result)
    return result


@dataclass(frozen=True, init=False)
class ControlRecord:
    key: str
    revision: int
    _encoded: str

    def __init__(self, key: str, revision: int, payload: dict[str, Any]):
        object.__setattr__(self, "key", _key(key))
        object.__setattr__(self, "revision", _revision(revision))
        object.__setattr__(self, "_encoded", _encode(payload))

    @property
    def payload(self) -> dict[str, Any]:
        """A detached copy; mutation cannot change the immutable record."""
        return _decode(self._encoded)


@dataclass(frozen=True)
class ControlMutation:
    key: str
    payload: dict[str, Any]
    expected_revision: int | None = None


class ControlStore:
    def __init__(self, directory: Path):
        try:
            self.directory = directory.absolute()
            reject_reparse_paths(self.directory)
            if not self.directory.exists():
                try:
                    create_private_directory(self.directory)
                except FileExistsError:
                    pass
            verify_private_path(self.directory)
            if not self.directory.is_dir():
                raise ControlError(_ERROR)
            self.path = self.directory / "control.sqlite3"
            created = False
            # Avoid CREATE_NEW against an existing Windows credential file:
            # native wrappers may surface that as a generic protection failure.
            # Existing files still undergo all ownership/ACL/schema checks.
            if not self.path.exists():
                try:
                    fd = create_private_file(self.path)
                except FileExistsError:
                    pass
                else:
                    os.close(fd)
                    created = True
            self._paths()
            with closing(self._connect(validate=False)) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    if created:
                        if db.execute("PRAGMA user_version").fetchone()[0] != 0:
                            raise ControlError(_ERROR)
                        db.execute(_SCHEMA)
                        db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                        db.execute("PRAGMA user_version=1")
                    self._schema(db)
                    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ControlError(_ERROR)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            self._paths()
            if created and os.name != "nt":
                fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except Exception:
            raise ControlError(_ERROR) from None

    def _paths(self) -> None:
        verify_private_path(self.directory)
        if not self.directory.is_dir():
            raise ControlError(_ERROR)
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            reject_reparse_paths(path)
            try:
                info = path.lstat()
            except FileNotFoundError:
                if not suffix:
                    raise ControlError(_ERROR) from None
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ControlError(_ERROR)
            try:
                verify_private_path(path, require_protected=not suffix)
            except FileNotFoundError:
                # Another connection may have committed and removed its journal.
                if not suffix:
                    raise
                continue
            if suffix in {"-wal", "-shm"}:
                raise ControlError(_ERROR)

    @staticmethod
    def _schema(db: sqlite3.Connection) -> None:
        if (
            db.execute("PRAGMA user_version").fetchone()[0] != 1
            or db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
        ):
            raise ControlError(_ERROR)
        rows = db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        if rows != [("table", "records", _SCHEMA)]:
            raise ControlError(_ERROR)

    def _connect(self, *, validate: bool = True) -> sqlite3.Connection:
        self._paths()
        db = sqlite3.connect(
            self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None
        )
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ControlError(_ERROR)
            if validate:
                self._schema(db)
            return db
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _record(row: tuple[Any, ...]) -> ControlRecord:
        return ControlRecord(row[0], row[1], _decode(row[2]))

    def get(self, key: str) -> ControlRecord | None:
        try:
            key = _key(key)
            with closing(self._connect()) as db:
                row = db.execute(
                    "SELECT key,revision,payload FROM records WHERE key=?", (key,)
                ).fetchone()
            return self._record(row) if row else None
        except Exception:
            raise ControlError(_ERROR) from None

    def list(self, prefix: str = "") -> tuple[ControlRecord, ...]:
        try:
            prefix = _key(prefix, prefix=True)
            with closing(self._connect()) as db:
                rows = db.execute(
                    "SELECT key,revision,payload FROM records WHERE substr(key,1,?)=? ORDER BY key",
                    (len(prefix), prefix),
                ).fetchmany(10001)
            if len(rows) > 10000:
                raise ControlError(_ERROR)
            return tuple(self._record(row) for row in rows)
        except Exception:
            raise ControlError(_ERROR) from None

    def create(self, key: str, payload: dict[str, Any]) -> ControlRecord:
        return self.batch((ControlMutation(key, payload),))[0]

    def update(self, key: str, payload: dict[str, Any], expected_revision: int) -> ControlRecord:
        # None must not accidentally switch an update into a create operation.
        try:
            _revision(expected_revision)
        except Exception:
            raise ControlError(_ERROR) from None
        return self.batch((ControlMutation(key, payload, expected_revision),))[0]

    def batch(self, mutations: Sequence[ControlMutation]) -> tuple[ControlRecord, ...]:
        """Atomically create/CAS records; duplicate keys and partial success forbidden.

        Consume an invitation with an expected-revision update plus enrollment
        creation in one batch. Its consumed tombstone remains durable.
        """
        try:
            if not 1 <= len(mutations) <= MAX_BATCH:
                raise ControlError(_ERROR)
            prepared = []
            for mutation in mutations:
                key = _key(mutation.key)
                revision = mutation.expected_revision
                if revision is not None:
                    _revision(revision)
                    if revision == MAX_REVISION:
                        raise ControlError(_ERROR)
                prepared.append((key, _encode(mutation.payload), revision))
            if len({item[0] for item in prepared}) != len(prepared):
                raise ControlError(_ERROR)
            records = []
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    for key, payload, revision in prepared:
                        if revision is None:
                            cursor = db.execute(
                                "INSERT INTO records(key,revision,payload) VALUES(?,1,?) ON CONFLICT(key) DO NOTHING",
                                (key, payload),
                            )
                        else:
                            cursor = db.execute(
                                "UPDATE records SET revision=revision+1,payload=? WHERE key=? AND revision=?",
                                (payload, key, revision),
                            )
                        if cursor.rowcount != 1:
                            raise ControlConflict(_ERROR)
                        records.append(
                            ControlRecord(
                                key, 1 if revision is None else revision + 1, _decode(payload)
                            )
                        )
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            return tuple(records)
        except ControlConflict:
            raise ControlConflict(_ERROR) from None
        except Exception:
            raise ControlError(_ERROR) from None
