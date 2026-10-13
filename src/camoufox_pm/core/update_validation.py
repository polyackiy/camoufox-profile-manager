"""Fixed new-process migration and isolated paired-browser validation entrypoint."""

import asyncio
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from camoufox_pm import __version__

from .database import StorageManager
from .maintenance import MaintenanceGate
from .schema_migrations import CURRENT_SCHEMA
from .update_permissions import create_private_file, verify_private_path


def _activate_paired_browser(record: dict):
    from camoufox.browser_pin import load_pin
    from camoufox.multiversion import get_active_path, list_installed, load_config, save_config
    from camoufox.pkgman import RepoConfig
    from packaging.version import Version

    manifest = record["manifest"]
    release_pin = load_pin()
    if release_pin is not None and release_pin.spec != manifest["browser_version"]:
        raise ValueError("The authenticated pair does not match the installed wrapper release pin")
    matches = [
        item for item in list_installed() if item.version.full_string == manifest["browser_version"]
    ]
    if record.get("paired_browser_path"):
        expected_path = Path(record["paired_browser_path"]).resolve()
        marker = json.loads((expected_path / ".cpm-pair.json").read_text())
        if marker != {
            "version": manifest["browser_version"],
            "sha256": record.get("browser_sha256"),
        }:
            raise ValueError(
                "Paired browser cache identity does not match the authenticated target"
            )
        matches = [item for item in matches if item.path.resolve() == expected_path]
    if record.get("browser_sha256"):
        matches = [
            item
            for item in matches
            if item.repo_name == "cpm-verified"
            and item.path.name.endswith("-" + record["browser_sha256"])
        ]
    else:
        verified = [item for item in matches if item.repo_name == "cpm-verified"]
        if verified:
            matches = verified
    if len(matches) != 1:
        raise ValueError("The exact tested browser pair must already be installed")
    paired = matches[0]
    config = load_config()
    # Use the upstream fetch pin protocol. The verified cache repository name is
    # local storage provenance, never an upstream download channel.
    origin = (
        release_pin.repo_name if release_pin is not None else RepoConfig.get_default_name().lower()
    )
    config["channel"] = (
        f"{origin}/{'prerelease' if Version(paired.version.full_string).is_prerelease else 'stable'}"
    )
    config["pinned"] = paired.version.full_string
    if record.get("browser_sha256"):
        config["pinned_sha"] = record["browser_sha256"]
    else:
        config.pop("pinned_sha", None)
    config["active_version"] = paired.relative_path
    save_config(config)
    if get_active_path() != paired.path:
        raise ValueError("Browser pin prevents activating the tested pair")
    return paired


async def _validate(record: dict):
    from camoufox.async_api import AsyncCamoufox
    from camoufox.pkgman import launch_path

    manifest = record["manifest"]
    if manifest["version"] != __version__ or manifest["schema_result"] != CURRENT_SCHEMA:
        raise ValueError("Installed manager does not match the authenticated target")
    paired = _activate_paired_browser(record)
    from camoufox_pm.config import get_settings

    from .update_recovery import _validate_encrypted_credentials

    key_path = Path(record["paths"].get("secret_key", ""))
    effective_key = get_settings().secret_key or (
        key_path.read_text().strip() if key_path.is_file() else ""
    )
    _validate_encrypted_credentials(Path(record["db_path"]), effective_key)
    database = StorageManager(record["db_path"])
    await database.initialize()
    try:
        with closing(sqlite3.connect(record["db_path"])) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Migrated database integrity check failed")
    finally:
        await database.close()
    # A temporary user-data directory prevents touching any user's profile.
    executable = launch_path(paired.path)
    with tempfile.TemporaryDirectory(prefix="cpm-update-validation-") as profile:
        async with AsyncCamoufox(
            executable_path=str(executable),
            headless=True,
            persistent_context=True,
            user_data_dir=profile,
        ) as context:
            page = await context.new_page()
            await page.goto("data:text/html,<title>CPM update validation</title>")
            if await page.title() != "CPM update validation":
                raise ValueError("Synthetic browser validation failed")


def validate_installed_process(receipt_path: str) -> None:
    root_text = os.environ.get("CPM_UPDATE_STATE_DIR")
    if not root_text:
        raise ValueError("Validation is available only to the offline helper")
    root = Path(root_text).absolute()
    receipt = Path(receipt_path).absolute()
    if receipt != root / "validation.json" or receipt.is_symlink():
        raise ValueError("Unexpected validation receipt")
    verify_private_path(root)
    verify_private_path(receipt)
    record = json.loads((root / "transaction.json").read_text())
    request = json.loads(receipt.read_text())
    gate = MaintenanceGate(root / "maintenance.sqlite3")
    # configured_gate uses maintenance.sqlite3; never admit normal startup.
    if gate.owner != record["transaction_id"] or request != {
        "transaction_id": record["transaction_id"],
        "manifest": record["manifest"],
    }:
        raise ValueError("Validation receipt is not owned by the transaction")
    asyncio.run(_validate(record))
    temporary = root / "validation-result.tmp"
    temporary.unlink(missing_ok=True)
    with os.fdopen(create_private_file(temporary), "w", encoding="utf-8") as stream:
        json.dump({"validated": record["transaction_id"]}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, receipt)
