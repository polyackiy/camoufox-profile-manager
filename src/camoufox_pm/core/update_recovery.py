"""Offline complete recovery for the supported configured local layout."""

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from camoufox_pm.config import get_settings

from .maintenance import MaintenanceBlocked, configured_gate
from .update_checkpoint import create_checkpoint, restore_checkpoint
from .update_permissions import create_private_file
from .update_service import UpdateService


def configured_layout(*, require_existing: bool = True):
    from camoufox.multiversion import CONFIG_FILE
    from camoufox.pkgman import INSTALL_DIR

    settings = get_settings()
    database = Path(settings.db_path).expanduser().resolve()
    key = database.parent / "secret.key"
    paths = {
        "profiles": database.parent / "profiles",
        "browser": Path(INSTALL_DIR),
        "secret_key": key,
        "environment": Path.cwd() / ".env",
    }
    required = ["profiles", "browser"]
    if require_existing and not CONFIG_FILE.exists():
        raise ValueError("A configured versioned browser installation is required")
    if key.exists():
        required.append("secret_key")
        if (
            require_existing
            and settings.secret_key
            and key.read_text().strip() != settings.secret_key
        ):
            raise ValueError(
                "Local and configured encryption keys differ; this layout requires manual recovery"
            )
    if Path.cwd().joinpath(".env").exists():
        required.append("environment")
    reference = hashlib.sha256((settings.secret_key or "").encode()).hexdigest()
    return database, paths, tuple(required), reference


def _profiles_stopped(database: Path):
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        # Even expired claims are refused: expiry is not proof the browser died.
        return not db.execute(
            "SELECT 1 FROM profiles WHERE locked_by IS NOT NULL LIMIT 1"
        ).fetchone()


def _legacy_processes_stopped() -> bool:
    import psutil

    # Previous versions do not register lifetimes. Conservatively inspect local
    # same-user manager/browser processes in addition to the durable registry.
    current = psutil.Process()
    user = current.username()
    for process in psutil.process_iter():
        if process.pid == os.getpid():
            continue
        try:
            if process.username() != user:
                continue
            command = " ".join(process.cmdline()).lower()
            name = process.name().lower()
            if any(
                marker in command
                for marker in ("camoufox_pm", "camoufox-pm", "camoufox profile manager")
            ) or name in ("camoufox", "firefox", "firefox.exe"):
                return False
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            return False
    return True


def offline_checkpoint() -> str:
    gate = configured_gate()
    service = UpdateService(gate.path.parent, gate)
    database, paths, required, key_reference = configured_layout()
    checkpoint_id = uuid.uuid4().hex
    owner = f"checkpoint-{checkpoint_id}"
    with service._control():
        if service.status()["state"] not in ("idle", "cancelled", "committed"):
            raise MaintenanceBlocked(
                "Resolve the existing update before taking a recovery checkpoint"
            )
        try:
            gate.acquire(owner)

            def check():
                return gate.blocked and gate.managers_stopped() and _legacy_processes_stopped()

            if not check() or not _profiles_stopped(database):
                raise MaintenanceBlocked(
                    "Close all manager instances and browsers before checkpointing"
                )
            root = service.root / "checkpoints" / checkpoint_id
            create_checkpoint(
                root,
                db_path=database,
                paths=paths,
                required_paths=required,
                maintenance_check=check,
            )
            reference = root / "key-reference.json"
            with os.fdopen(create_private_file(reference), "w", encoding="utf-8") as stream:
                json.dump(
                    {"secret_key_sha256": key_reference, "local_key": paths["secret_key"].exists()},
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            return checkpoint_id
        finally:
            if gate.owner == owner:
                gate.release(owner)


def offline_restore(checkpoint_id: str, confirmed_timestamp: str):
    if not re.fullmatch(r"[a-f0-9]{32}", checkpoint_id):
        raise ValueError("Checkpoint ID must be an ID created by this installation")
    gate = configured_gate()
    service = UpdateService(gate.path.parent, gate)
    database, paths, _, reference = configured_layout(require_existing=False)
    root = service.root / "checkpoints" / checkpoint_id
    journal = json.loads((root / "journal.json").read_text())
    if journal["created_at"] != confirmed_timestamp:
        raise ValueError(
            "Confirm the checkpoint timestamp exactly; restoration loses changes since that time"
        )
    key_metadata = json.loads((root / "key-reference.json").read_text())
    if not key_metadata.get("local_key") and key_metadata["secret_key_sha256"] != reference:
        raise ValueError(
            "The encryption key configuration changed; restore requires the original key"
        )
    owner = f"restore-{checkpoint_id}"
    with service._control():
        resuming = gate.owner == owner
        try:
            gate.acquire(owner)

            def check():
                return gate.blocked and gate.managers_stopped() and _legacy_processes_stopped()

            if not check() or (database.exists() and not _profiles_stopped(database)):
                raise MaintenanceBlocked("Close all manager instances and browsers before restoring")
        except BaseException:
            # A refused initial restore has not changed data. Let ordinary CLI
            # lease inspection/unlock remain possible. Interrupted restoration
            # retains its existing gate even when a retry precondition fails.
            if not resuming and gate.owner == owner:
                gate.release(owner)
            raise
        # Never release admission if replacement fails; retry this ID resumes the
        # checkpoint's restore journal and keeps the newer state retained.
        restore_checkpoint(root, db_path=database, paths=paths, maintenance_check=check)
        gate.release(owner)
