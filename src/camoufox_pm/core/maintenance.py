"""Durable admission gate for cooperating processes sharing an installation.

The gate database lives outside the application database, so restoring a data
checkpoint cannot accidentally re-enable writers. Interrupted owners remain
blocked until explicit recovery; elapsed time alone is never permission to write.
"""

import asyncio
import os
import socket
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

from .update_permissions import create_private_file, protect_path


class MaintenanceBlocked(RuntimeError):
    pass


_active: ContextVar[tuple[str, str, tuple[int, int | None]] | None] = ContextVar(
    "maintenance_operation", default=None
)


class MaintenanceGate:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        protect_path(self.path.parent)
        if not self.path.exists():
            try:
                os.close(create_private_file(self.path))
            except FileExistsError:
                pass
        protect_path(self.path)
        with closing(self._connect()) as db, db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS gate (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT); INSERT OR IGNORE INTO gate VALUES (1, NULL); CREATE TABLE IF NOT EXISTS operations (token TEXT PRIMARY KEY, pid INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS managers (pid INTEGER PRIMARY KEY, created REAL NOT NULL);"
            )
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(operations)")}
            for name, type_name in (("created", "REAL"), ("machine", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE operations ADD COLUMN {name} {type_name}")
        protect_path(self.path)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @contextmanager
    def operation(self):
        key = str(self.path)
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        identity = (threading.get_ident(), id(task) if task else None)
        if _active.get() and _active.get()[0] == key and _active.get()[2] == identity:
            yield
            return
        token = uuid.uuid4().hex
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT owner FROM gate WHERE id=1").fetchone()[0]:
                raise MaintenanceBlocked("Update maintenance is active; recovery may be required")
            import psutil

            db.execute(
                "INSERT INTO operations(token,pid,created,machine) VALUES (?,?,?,?)",
                (token, os.getpid(), psutil.Process().create_time(), socket.gethostname()),
            )
        context_token = _active.set((key, token, identity))
        try:
            yield
        finally:
            _active.reset(context_token)
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM operations WHERE token=?", (token,))

    def acquire(self, owner: str):
        if not owner:
            raise ValueError("Maintenance owner is required")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT owner FROM gate WHERE id=1").fetchone()[0]
            if current not in (None, owner):
                raise MaintenanceBlocked("Another update owns maintenance")
            db.execute("UPDATE gate SET owner=? WHERE id=1", (owner,))
        # Acquisition closes admission before checking participants. Keep it
        # closed when busy so callers can drain and retry with the same owner.
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            import psutil

            for token, pid, created, machine in db.execute(
                "SELECT token,pid,created,machine FROM operations"
            ).fetchall():
                if machine != socket.gethostname() or created is None:
                    continue
                try:
                    dead = abs(psutil.Process(pid).create_time() - created) >= 0.01
                except psutil.NoSuchProcess:
                    dead = True
                except psutil.AccessDenied:
                    dead = False
                if dead:
                    db.execute("DELETE FROM operations WHERE token=?", (token,))
            if db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]:
                raise MaintenanceBlocked(
                    "Waiting for in-flight operations; close other manager instances if an operation was interrupted"
                )

    def release(self, owner: str):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT owner FROM gate WHERE id=1").fetchone()[0] != owner:
                raise MaintenanceBlocked("Only the maintenance owner can release the gate")
            db.execute("UPDATE gate SET owner=NULL WHERE id=1")

    @property
    def owner(self) -> str | None:
        with closing(self._connect()) as db, db:
            return db.execute("SELECT owner FROM gate WHERE id=1").fetchone()[0]

    @property
    def blocked(self) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute("SELECT owner FROM gate WHERE id=1").fetchone()[0] is not None

    def register_manager(self):
        import psutil

        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO managers VALUES (?,?)",
                (os.getpid(), psutil.Process().create_time()),
            )

    def unregister_manager(self):
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM managers WHERE pid=?", (os.getpid(),))

    def managers_stopped(self) -> bool:
        import psutil

        with closing(self._connect()) as db, db:
            rows = db.execute("SELECT pid,created FROM managers").fetchall()
        for pid, created in rows:
            try:
                if abs(psutil.Process(pid).create_time() - created) < 0.01:
                    return False
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                return False
        return True


def configured_gate() -> MaintenanceGate:
    from camoufox_pm.config import get_settings

    return MaintenanceGate(
        Path(get_settings().db_path).resolve().parent / "update-control" / "maintenance.sqlite3"
    )
