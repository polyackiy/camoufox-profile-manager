"""Durable preparation orchestration; replacement belongs to the offline helper."""

import json
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from .maintenance import MaintenanceBlocked, MaintenanceGate
from .update_checkpoint import create_checkpoint
from .update_permissions import create_private_file, protect_path


def _serialized(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self._control():
            return method(self, *args, **kwargs)

    return run


class UpdateService:
    def __init__(self, root: Path, gate: MaintenanceGate):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        protect_path(self.root)
        self.gate = gate
        self.journal = self.root / "transaction.json"

    @contextmanager
    def _control(self):
        # A separate lock database keeps journal reads responsive while another
        # process takes a checkpoint. Crashed connections release this lock.
        try:
            with closing(sqlite3.connect(self.root / "control.sqlite3", timeout=0.1)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                yield
        except sqlite3.OperationalError as exc:
            raise MaintenanceBlocked("An update operation is already running") from exc

    def status(self) -> dict:
        if not self.journal.exists():
            owner = self.gate.owner
            return {
                "state": "checkpoint_interrupted"
                if owner and re.fullmatch(r"checkpoint-[a-f0-9]{32}", owner)
                else "idle",
                "automatic_install_available": False,
            }
        try:
            value = json.loads(self.journal.read_text())
            if not isinstance(value, dict) or not value.get("transaction_id"):
                raise ValueError("Invalid update journal")
            owner = self.gate.owner
            if (
                value.get("state") in ("idle", "cancelled", "committed")
                and owner
                and re.fullmatch(r"checkpoint-[a-f0-9]{32}", owner)
            ):
                value = {**value, "state": "checkpoint_interrupted"}
            return value
        except (ValueError, OSError) as exc:
            raise MaintenanceBlocked(
                "Update journal cannot be read; explicit recovery is required"
            ) from exc

    def _save(self, value: dict):
        temporary = self.root / f"transaction-{uuid.uuid4().hex}.tmp"
        with os.fdopen(create_private_file(temporary), "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.journal)
        if os.name != "nt":
            fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @_serialized
    def prepare(
        self,
        *,
        db_path: Path,
        paths: Mapping[str, Path],
        verified_package: Path,
        verify_package: Callable[[Path], None],
        offline_check: Callable[[], bool],
        browser_version: str,
        target_version: str,
        required_paths: tuple[str, ...] = (),
        metadata: dict | None = None,
    ) -> dict:
        """Require authenticated package and a caller's complete configured data map.

        offline_check must check all profile leases/processes, not only browsers
        owned by this instance. No gate is released after a complete checkpoint.
        """
        if not browser_version or not target_version:
            raise ValueError("A tested manager/browser pair is required")
        current = self.status()
        if current["state"] not in ("idle", "cancelled", "committed", "rolled_back"):
            raise MaintenanceBlocked("An existing update must be resolved first")
        verify_package(verified_package)
        transaction = {
            "transaction_id": uuid.uuid4().hex,
            "state": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "target_version": target_version,
            "browser_version": browser_version,
            "package": str(Path(verified_package).resolve()),
            "db_path": str(Path(db_path).resolve()),
            "paths": {key: str(Path(path).resolve()) for key, path in paths.items()},
            "automatic_install_available": False,
        }
        if metadata:
            transaction.update(metadata)
        self._save(transaction)  # Persist ownership before closing admission.
        owner = str(transaction["transaction_id"])
        try:
            self.gate.acquire(owner)
            if not offline_check():
                raise MaintenanceBlocked(
                    "Close every running profile and other manager instance before applying"
                )
            checkpoint = create_checkpoint(
                self.root / "checkpoints" / owner,
                db_path=db_path,
                paths=paths,
                required_paths=required_paths,
                maintenance_check=lambda: self.gate.blocked and offline_check(),
            )
            checkpoint_record = json.loads((checkpoint / "journal.json").read_text())
            transaction.update(
                state="prepared",
                checkpoint=str(checkpoint),
                checkpoint_timestamp=checkpoint_record["created_at"],
            )
            self._save(transaction)
            return transaction
        except BaseException:
            # Keep ownership durable on interrupted/partial preparation. Explicit
            # cancellation is safe because replacement has not begun.
            raise

    @_serialized
    def cancel_preparation(self):
        transaction = self.status()
        if transaction.get("browser_install_started"):
            raise MaintenanceBlocked(
                "Browser installation has begun; resume or restore the checkpoint"
            )
        if (self.root / "replacement.json").exists():
            raise MaintenanceBlocked(
                "A replacement helper journal exists; complete offline recovery before resuming writes"
            )
        if transaction["state"] not in ("preparing", "prepared"):
            raise MaintenanceBlocked("Replacement/recovery cannot be cancelled")
        if self.gate.blocked:
            self.gate.release(transaction["transaction_id"])
        transaction["state"] = "cancelled"
        self._save(transaction)

    @_serialized
    def cancel_abandoned_checkpoint(self):
        """Release only an interrupted read-only offline checkpoint owner.

        The cross-process control lock proves no checkpoint creation is still
        running. Copies are retained; restoring/replacing is never cancellable.
        """
        owner = self.gate.owner
        if not owner or not re.fullmatch(r"checkpoint-[a-f0-9]{32}", owner):
            raise MaintenanceBlocked("Only an abandoned offline checkpoint can be cancelled")
        if (self.root / "replacement.json").exists():
            raise MaintenanceBlocked(
                "A replacement helper journal exists; offline recovery is required"
            )
        if self.status()["state"] != "checkpoint_interrupted":
            raise MaintenanceBlocked(
                "Resolve the update transaction before cancelling its maintenance"
            )
        self.gate.release(owner)
