"""Export sealed evidence and restore a historical fork into a fresh local profile."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import shutil
import warnings
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.models import Profile
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.profile_operations import (
    profile_data_path,
    profile_operation,
    run_file_operation,
)
from camoufox_pm.core.update_permissions import (
    create_private_directory,
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)
from camoufox_pm.sync.identity import SignedMembership, encode_key
from camoufox_pm.sync.quiescence import browser_profile_lock
from camoufox_pm.sync.recovery_archive import (
    MAX_EVIDENCE_BYTES,
    MAX_MEMBERSHIPS,
    _read_regular,
    build_recovery_evidence,
    recover_archive,
)
from camoufox_pm.sync.runtime import SyncRuntime, installed_compatibility
from camoufox_pm.sync.transfer import SnapshotTransfer
from camoufox_pm.sync.vault import MAX_PACKAGE

_ERROR = "Recovery operation failed. Check the files, unlock secret, installed browser compatibility and maintenance state. Existing profiles are preserved."


class RecoveryWorkflowError(ValueError):
    """Credential-free CLI error."""


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write(path, value):
    with os.fdopen(create_private_file(path), "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


async def export_bundle(
    runtime: SyncRuntime, local_id: str, output: Path, *, password=None
) -> Path:
    """Export a verified published revision; encrypted browser data only.

    The local OS user must already control the enrolled manager installation. A
    still-live administrator account and unlocked device vault are required.
    """
    output = Path(output).absolute()
    created = False
    client = None
    try:
        with runtime.maintenance.operation():
            installation = runtime.service.control.get("installation")
            if installation is None:
                raise RecoveryWorkflowError(_ERROR)
            user_id = installation.payload["local_user_id"]
            engine, keys, client = await runtime._session(user_id, password)
            async with profile_operation(runtime.manager, local_id, sync_operation=True) as profile:
                with ExitStack() as locks:
                    await run_file_operation(
                        locks.enter_context,
                        browser_profile_lock(profile_data_path(runtime.manager, profile)),
                    )
                    manifest, signature, _, _ = await run_file_operation(
                        SnapshotTransfer(engine, keys).selected, local_id, require_checkout=False
                    )
                    if manifest.membership_epoch > MAX_MEMBERSHIPS:
                        raise RecoveryWorkflowError(_ERROR)
                    memberships = []
                    for epoch in range(1, manifest.membership_epoch + 1):
                        record = runtime.service.control.get(f"membership/{epoch}")
                        if record is None:
                            raise RecoveryWorkflowError(_ERROR)
                        memberships.append(SignedMembership.model_validate(record.payload))
                    evidence = build_recovery_evidence(
                        engine.root, tuple(memberships), manifest, encode_key(signature)
                    )
                    await runtime._authorize(user_id)
                    reject_reparse_paths(output)
                    create_private_directory(output)
                    created = True
                    await run_file_operation(
                        client.download,
                        manifest.revision_id,
                        output / "snapshot.ciphertext",
                        manifest.ciphertext_sha256,
                        manifest.ciphertext_length,
                    )
                    await runtime._authorize(user_id)
                    _write(output / "evidence.json", evidence)
                    _sync_directory(output)
                    _sync_directory(output.parent)
                    return output
    except Exception:
        if created:
            shutil.rmtree(output, ignore_errors=True)
        raise RecoveryWorkflowError(_ERROR) from None
    finally:
        if client is not None:
            client.close()


async def restore_bundle(
    manager,
    maintenance,
    package: bytes,
    secret: str,
    evidence: bytes,
    ciphertext: Path,
    *,
    acknowledge_historical_fork: bool,
    compatibility=None,
) -> Profile:
    """Create one NEW local identity; never initialize or write sync-control.

    A caller must supply authenticated OS-user access to this local manager. The
    snapshot's compatibility is compared to the installed runtime, not user input.
    """
    staging = None
    staging_owned = False
    target = None
    inserted = False
    target_created = False
    try:
        if acknowledge_historical_fork is not True:
            raise RecoveryWorkflowError(_ERROR)
        with maintenance.operation():
            expected = (compatibility or installed_compatibility)()
            root = manager.profiles_dir.absolute()
            reject_reparse_paths(root)
            from camoufox_pm.core.update_permissions import protect_path

            protect_path(root)
            staging = root / ("recovery-" + str(uuid4()))
            recovered = await run_file_operation(
                recover_archive,
                package,
                secret,
                evidence,
                ciphertext,
                staging,
                expected,
                acknowledge_historical_fork=True,
            )
            staging_owned = True
            values = recovered.metadata.profile.apply_fields()
            profile = Profile(**values)
            target = root / ("profile_" + profile.id)
            reject_reparse_paths(target)
            # Exclusive reservation means neither existing directories nor IDs can
            # be overwritten, even if another manager creates a profile concurrently.
            create_private_directory(target)
            target_created = True
            source = recovered.directory / "validated" / "data"
            for child in source.iterdir():
                os.rename(child, target / child.name)
            profile.storage_path = str(target)
            with ExitStack() as locks:
                await run_file_operation(locks.enter_context, browser_profile_lock(target))
                for path in target.rglob("*"):
                    if path.name not in {".parentlock", "parent.lock", "lock"} and path.is_file():
                        with path.open("r+b") as stream:
                            os.fsync(stream.fileno())
                for directory in sorted(
                    (p for p in target.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts),
                    reverse=True,
                ):
                    _sync_directory(directory)
                _sync_directory(target)
                _sync_directory(root)
                inserted = await manager.storage.insert_profile_if_absent(profile)
                if not inserted:
                    raise RecoveryWorkflowError(_ERROR)
            return profile
    except Exception:
        raise RecoveryWorkflowError(_ERROR) from None
    finally:
        if staging is not None and staging_owned:
            shutil.rmtree(staging, ignore_errors=True)
        if target is not None and not inserted:
            # Only an exclusively created target belongs to this operation.
            # A failed mkdir must never authorize removal of somebody else's data.
            if target_created:
                shutil.rmtree(target, ignore_errors=True)


def _prompt(label):
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(label)


def _target_key(database: Path, *, create: bool = True):
    """Use this installation's key, never an unrelated cwd dotenv file."""
    from cryptography.fernet import Fernet

    from camoufox_pm.config import get_settings

    key_file = database.parent / "secret.key"
    configured = os.environ.get("CPM_SECRET_KEY")
    if key_file.exists() or key_file.is_symlink():
        verify_private_path(key_file)
        key = _read_regular(key_file, 128).decode("ascii").strip()
        if configured and configured != key:
            raise RecoveryWorkflowError(_ERROR)
    elif configured:
        key = configured
    else:
        # An encrypted preexisting DB without its key is not a clean destination.
        if database.exists():
            import sqlite3

            with sqlite3.connect(str(database)) as db:
                table = db.execute("SELECT 1 FROM sqlite_master WHERE name='profiles'").fetchone()
                if (
                    table
                    and db.execute(
                        "SELECT 1 FROM profiles WHERE proxy_config LIKE '%enc:%' LIMIT 1"
                    ).fetchone()
                ):
                    raise RecoveryWorkflowError(_ERROR)
        key = Fernet.generate_key().decode("ascii") if create else ""
        if create:
            _write(key_file, key.encode("ascii"))
    if key:
        Fernet(key.encode("ascii"))
    os.environ["CPM_SECRET_KEY"] = key
    get_settings.cache_clear()


async def _execute(args):
    database = args.db.expanduser().absolute()
    reject_reparse_paths(database)
    if args.command == "export" and not database.is_file():
        raise RecoveryWorkflowError(_ERROR)
    if not database.parent.exists():
        create_private_directory(database.parent)
    gate = MaintenanceGate(database.parent / "update-control" / "maintenance.sqlite3")
    with gate.operation():
        _target_key(database, create=args.command == "restore")
        storage = StorageManager(str(database))
        await storage.initialize()
        try:
            manager = ProfileManager(storage, str(database.parent))
            if args.command == "export":
                runtime = SyncRuntime(manager, gate)
                record = runtime.service.control.get("installation")
                password = (
                    _prompt("Device vault unlock password: ")
                    if record and record.payload.get("vault_kind") == "encrypted_file"
                    else None
                )
                await export_bundle(runtime, args.profile, args.output, password=password)
                print(
                    "Encrypted snapshot and signed evidence exported. Keep them with the separate owner recovery package; store its secret elsewhere."
                )
            else:
                profile = await restore_bundle(
                    manager,
                    gate,
                    _read_regular(args.package, MAX_PACKAGE),
                    _prompt("Separate owner recovery secret: "),
                    _read_regular(args.evidence, MAX_EVIDENCE_BYTES),
                    args.ciphertext,
                    acknowledge_historical_fork=args.acknowledge_historical_fork,
                )
                print(
                    "Historical fork restored as new local profile "
                    + profile.id
                    + ". Old synchronization authority was not restored. Review website sessions before use."
                )
        finally:
            await storage.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export encrypted recovery evidence or restore a NEW local historical fork"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--db", type=Path, required=True)
    export.add_argument("--profile", required=True)
    export.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore")
    for field in ("package", "evidence", "ciphertext", "db"):
        restore.add_argument("--" + field, type=Path, required=True)
    restore.add_argument("--acknowledge-historical-fork", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            print(
                "This may be an old snapshot. Recovery creates a separate local fork; it cannot prove the latest history or recall another device's browser sessions."
            )
        asyncio.run(_execute(args))
        return 0
    except Exception:
        print(_ERROR)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
