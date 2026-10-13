"""Install an independently authenticated upstream browser after checkpointing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .update_package import PackageEntry, extract_verified_browser_archive
from .update_permissions import create_private_directory
from .update_targets import UpdateVerificationError, VerifiedTarget


def install_paired_browser(target: VerifiedTarget, browser_root: Path, *, offline_check) -> Path:
    """Reauthenticate every byte on retries; activation belongs to new-process validation.

    Raw upstream archives remain independently distributed. Only deterministic
    multiversion metadata is added to their authenticated tree; no executable
    path or code can be provided by an HTTP caller.
    """
    manifest = target.manifest
    version = manifest.get("version")
    if (
        manifest.get("component") != "camoufox"
        or not isinstance(version, str)
        or version != manifest.get("browser_version")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,160}", version)
        or "sha256" not in target.hashes
    ):
        raise UpdateVerificationError("Invalid authenticated browser pair")
    if not offline_check():
        raise UpdateVerificationError("Browser installation requires offline maintenance")
    browser_root = Path(browser_root).absolute()
    for component in (browser_root, *browser_root.parents):
        if component.is_symlink():
            raise UpdateVerificationError("Browser cache must not contain symbolic links")
    destination = (
        browser_root / "browsers" / "cpm-verified" / f"{version}-{target.hashes['sha256']}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".staging-{uuid4().hex}"
    create_private_directory(temporary)
    try:
        package = extract_verified_browser_archive(target, temporary)
        package.verify_tree()
        entries = list(package.entries)
        metadata = package.path / "version.json"
        if not metadata.is_file():
            if "-" not in version:
                raise UpdateVerificationError("Browser target requires exact version-build pair")
            engine, build = version.rsplit("-", 1)
            data = json.dumps(
                {"version": engine, "build": build, "sha256": target.hashes["sha256"]},
                sort_keys=True,
            ).encode()
            metadata.write_bytes(data)
            metadata.chmod(0o644)
            entries.append(
                PackageEntry(
                    "version.json", "file", 0o644, len(data), hashlib.sha256(data).hexdigest()
                )
            )
        value = json.loads(metadata.read_text())
        build = value.get("build", value.get("release", value.get("tag")))
        if f"{value.get('version')}-{build}" != version:
            raise UpdateVerificationError("Browser archive version differs from the signed pair")
        data = json.dumps(
            {"version": version, "sha256": target.hashes["sha256"]}, sort_keys=True
        ).encode()
        marker = package.path / ".cpm-pair.json"
        marker.write_bytes(data)
        marker.chmod(0o644)
        entries.append(
            PackageEntry(
                ".cpm-pair.json", "file", 0o644, len(data), hashlib.sha256(data).hexdigest()
            )
        )
        augmented = replace(package, entries=tuple(sorted(entries, key=lambda entry: entry.path)))
        augmented.verify_tree()
        if destination.exists():
            augmented.verify_tree(destination)
            return destination
        if not offline_check():
            raise UpdateVerificationError("Offline maintenance was lost")
        package.path.rename(destination)
        if os.name != "nt":
            fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
