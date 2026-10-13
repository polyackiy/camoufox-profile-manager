"""Offline complete recovery for the supported configured local layout."""

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

from camoufox_pm import __version__
from camoufox_pm.config import get_settings

from .maintenance import MaintenanceBlocked, configured_gate
from .update_checkpoint import create_checkpoint, restore_checkpoint
from .update_permissions import create_private_file
from .update_service import UpdateService


def configured_layout(
    *, require_existing: bool = True, configuration_directory: Path | None = None
):
    from camoufox.multiversion import CONFIG_FILE
    from camoufox.pkgman import INSTALL_DIR

    settings = get_settings()
    configuration_directory = (
        Path(configuration_directory) if configuration_directory is not None else Path.cwd()
    )
    database = Path(settings.db_path).expanduser().resolve()
    key = database.parent / "secret.key"
    paths = {
        "profiles": database.parent / "profiles",
        "browser": Path(INSTALL_DIR),
        "secret_key": key,
        "environment": configuration_directory / ".env",
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
    if configuration_directory.joinpath(".env").exists():
        required.append("environment")
    effective_key = settings.secret_key or (key.read_text().strip() if key.exists() else "")
    reference = hashlib.sha256(effective_key.encode()).hexdigest()
    return database, paths, tuple(required), reference


def _profiles_stopped(database: Path):
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        # Even expired claims are refused: expiry is not proof the browser died.
        return not db.execute(
            "SELECT 1 FROM profiles WHERE locked_by IS NOT NULL LIMIT 1"
        ).fetchone()


def _validate_encrypted_credentials(database: Path, effective_key: str):
    from cryptography.fernet import Fernet, InvalidToken

    tokens: list[str] = []
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(profiles)")}
        if "proxy_config" in columns:
            for (raw,) in db.execute(
                "SELECT proxy_config FROM profiles WHERE proxy_config IS NOT NULL"
            ):
                try:
                    config = json.loads(raw)
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        "Stored proxy configuration is unreadable; recovery checkpoint refused"
                    ) from exc
                if isinstance(config, dict):
                    tokens.extend(
                        value
                        for value in config.values()
                        if isinstance(value, str) and value.startswith("enc:")
                    )
        columns = {row[1] for row in db.execute("PRAGMA table_info(proxy_inventory)")}
        if "password" in columns:
            tokens.extend(
                value
                for (value,) in db.execute(
                    "SELECT password FROM proxy_inventory WHERE password IS NOT NULL"
                )
                if isinstance(value, str) and value.startswith("enc:")
            )
    if not tokens:
        return
    if not effective_key:
        raise ValueError("The original encryption key is required for encrypted proxy credentials")
    try:
        cipher = Fernet(effective_key.encode())
        for token in tokens:
            cipher.decrypt(token[4:].encode())
    except (ValueError, InvalidToken) as exc:
        raise ValueError("The encryption key cannot read all stored proxy credentials") from exc


def _legacy_processes_stopped() -> bool:
    import psutil

    # Previous versions do not register lifetimes. Conservatively inspect local
    # same-user manager/browser processes in addition to the durable registry.
    current = psutil.Process()
    user = current.username()
    launcher_ancestors = set()
    try:
        for ancestor in current.parents():
            name = ancestor.name().lower()
            command = " ".join(ancestor.cmdline()).lower()
            known_launcher = name in ("uv", "uv.exe", "pipx", "pipx.exe", "zsh", "bash", "sh") or (
                name.startswith("python") and "pipx" in command
            )
            if known_launcher and "update-recovery" in command:
                launcher_ancestors.add(ancestor.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    for process in psutil.process_iter():
        if process.pid == os.getpid() or process.pid in launcher_ancestors:
            continue
        try:
            name = process.name().lower()
            candidate = (
                "camoufox" in name
                or "firefox" in name
                or name.startswith("python")
                or name
                in (
                    "uvicorn",
                    "uvicorn.exe",
                    "uv",
                    "uv.exe",
                    "pipx",
                    "pipx.exe",
                    "bash",
                    "zsh",
                    "sh",
                )
            )
            # Do not query protected SYSTEM processes' private command lines.
            # Candidate interpreter/browser/manager uncertainty stays closed.
            if not candidate:
                continue
            if process.username() != user:
                continue
            command = " ".join(process.cmdline()).lower()
            if any(
                marker in command
                for marker in ("camoufox_pm", "camoufox-pm", "camoufox profile manager")
            ) or name in (
                "camoufox",
                "camoufox.exe",
                "camoufox-bin",
                "firefox",
                "firefox.exe",
                "firefox-bin",
            ):
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
            effective_key = get_settings().secret_key or (
                paths["secret_key"].read_text().strip() if paths["secret_key"].exists() else ""
            )
            _validate_encrypted_credentials(database, effective_key)
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
                    {
                        "secret_key_sha256": key_reference,
                        "local_key": paths["secret_key"].exists(),
                        "manager_version": __version__,
                        "wrapper_version": version("camoufox"),
                        "browser_active": json.loads(
                            (paths["browser"] / "config.json").read_text()
                        ).get("active_version"),
                    },
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
    if key_metadata.get("manager_version") != __version__:
        raise ValueError(
            f"Checkpoint requires manager {key_metadata.get('manager_version', 'unknown')}; install that matching manager before restoring with {__version__}"
        )
    external_key_present = bool(get_settings().secret_key)
    if (external_key_present or not key_metadata.get("local_key")) and key_metadata[
        "secret_key_sha256"
    ] != reference:
        raise ValueError(
            "The encryption key configuration changed; restore requires the original key"
        )
    effective_key = get_settings().secret_key or ""
    if not effective_key and key_metadata.get("local_key"):
        key_entry = next(
            (entry for entry in journal["entries"] if entry["label"] == "secret_key"), None
        )
        if not key_entry or not re.fullmatch(r"[0-9]+", key_entry["payload"]):
            raise ValueError("Checkpoint encryption key payload is invalid")
        key_payload = root / key_entry["payload"]
        if key_payload.is_symlink():
            raise ValueError("Checkpoint encryption key payload is invalid")
        effective_key = key_payload.read_text().strip()
    _validate_encrypted_credentials(root / "0", effective_key)
    owner = f"restore-{checkpoint_id}"
    with service._control():
        resuming = gate.owner == owner
        try:
            gate.acquire(owner)

            def check():
                return gate.blocked and gate.managers_stopped() and _legacy_processes_stopped()

            if not check() or (database.exists() and not _profiles_stopped(database)):
                raise MaintenanceBlocked(
                    "Close all manager instances and browsers before restoring"
                )
        except BaseException:
            # A refused initial restore has not changed data. Let ordinary CLI
            # lease inspection/unlock remain possible. Interrupted restoration
            # retains its existing gate even when a retry precondition fails.
            if not resuming and gate.owner == owner:
                gate.release(owner)
            raise
        # Never release admission if replacement fails; retry this ID resumes the
        # checkpoint's restore journal and keeps the newer state retained.
        from .sync_guard import invalidate_all_sync

        invalidate_all_sync(database.parent)
        restore_checkpoint(root, db_path=database, paths=paths, maintenance_check=check)
        gate.release(owner)
