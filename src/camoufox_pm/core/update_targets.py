"""Authenticated download boundary; installation and publisher checks are separate.

Only python-tuf verifies metadata/signatures. Never bootstrap trust from a URL.
The metadata cache is persistent and must have one cooperating caller at a time.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit
from uuid import uuid4

from packaging.version import InvalidVersion, Version

from .update_permissions import (
    PermissionProtectionError,
    create_private_directory,
    create_private_file,
    protect_path,
    verify_private_path,
)


class UpdateVerificationError(RuntimeError):
    """Update cannot be authenticated or is incompatible with this installation."""


@dataclass(frozen=True)
class UpdatePolicy:
    component: str
    version: str
    platform: str
    architecture: str
    channel: str
    current_schema: int
    updater_version: str
    browser_version: str
    publisher_policy: str
    product_id: str
    os_version: str
    max_target_bytes: int = 2 * 1024**3


@dataclass(frozen=True)
class VerifiedTarget:
    path: Path
    manifest: dict
    length: int
    hashes: dict[str, str]


def validate_manifest(custom: dict, target_path: str, policy: UpdatePolicy) -> dict:
    """Validate compatibility claims only after TUF authenticates target metadata."""
    manifest = custom.get("camoufox_pm") if isinstance(custom, dict) else None
    required = {
        "protocol",
        "component",
        "version",
        "platform",
        "architecture",
        "channel",
        "artifact",
        "schema_min",
        "schema_max",
        "schema_result",
        "recovery",
        "minimum_updater",
        "browser_version",
        "publisher_policy",
        "product_id",
        "minimum_os",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise UpdateVerificationError("Signed update manifest has missing or unknown fields")
    if type(manifest["protocol"]) is not int or manifest["protocol"] != 1:
        raise UpdateVerificationError("Unsupported updater protocol")
    for key in required - {"protocol", "schema_min", "schema_max", "schema_result"}:
        if not isinstance(manifest[key], str) or not manifest[key]:
            raise UpdateVerificationError(f"Invalid manifest field: {key}")
    for key in (
        "component",
        "version",
        "platform",
        "architecture",
        "channel",
        "browser_version",
        "publisher_policy",
        "product_id",
    ):
        if manifest[key] != getattr(policy, key):
            raise UpdateVerificationError(f"Update does not match installed {key}")
    if policy.component not in {"manager", "camoufox"} or policy.channel not in {
        "stable",
        "preview",
    }:
        raise UpdateVerificationError("Unsupported component or channel")
    if manifest["artifact"] != target_path or manifest["recovery"] != "checkpoint-v1":
        raise UpdateVerificationError("Artifact or recovery contract is unsupported")
    for key in ("schema_min", "schema_max", "schema_result"):
        if type(manifest[key]) is not int or manifest[key] < 0:
            raise UpdateVerificationError("Invalid schema compatibility contract")
    if not manifest["schema_min"] <= policy.current_schema <= manifest["schema_max"]:
        raise UpdateVerificationError("Installed database schema is unsupported")
    try:
        version = Version(manifest["version"])
        Version(manifest["browser_version"])
        if (
            policy.component == "manager"
            and policy.channel == "stable"
            and (version.is_prerelease or version.is_devrelease)
        ):
            raise UpdateVerificationError("Stable updates cannot install preview versions")
        if Version(policy.updater_version) < Version(manifest["minimum_updater"]):
            raise UpdateVerificationError("Updater is too old for this target")
        if Version(policy.os_version) < Version(manifest["minimum_os"]):
            raise UpdateVerificationError("Operating system is too old for this target")
    except InvalidVersion as exc:
        raise UpdateVerificationError("Invalid signed version") from exc
    return dict(manifest)


def _valid_root_history_link(text: str, *, windows: bool | None = None) -> bool:
    """Accept only TUF's canonical relative two-component cache link."""
    windows = os.name == "nt" if windows is None else windows
    path = PureWindowsPath(text) if windows else PurePosixPath(text)
    normalized = text.replace("\\", "/") if windows else text
    return (
        not path.anchor
        and len(path.parts) == 2
        and path.parts[0] == "root_history"
        and re.fullmatch(r"[1-9][0-9]*\.root\.json", path.parts[1]) is not None
        and normalized == path.as_posix()
    )


def _metadata_permissions(directory: Path, *, protect: bool) -> None:
    # python-tuf 7 atomically publishes root.json as an internal root-history
    # symlink on POSIX. Permit only that exact maintained-client cache format.
    for cached in directory.rglob("*"):
        if cached.is_symlink():
            if (
                cached != directory / "root.json"
                or not _valid_root_history_link(os.readlink(cached))
                or not cached.resolve().is_relative_to(directory.resolve())
                or not cached.resolve().is_file()
            ):
                raise UpdateVerificationError("Unsafe cached metadata symlink")
            if hasattr(os, "getuid") and cached.lstat().st_uid != os.getuid():
                raise UpdateVerificationError("Cached metadata is owned by another account")
            continue
        if not (cached.is_file() or cached.is_dir()):
            raise UpdateVerificationError("Unsupported cached metadata entry")
        if protect:
            protect_path(cached)
        else:
            verify_private_path(cached, require_protected=False)
        if hasattr(os, "getuid") and cached.lstat().st_uid != os.getuid():
            raise UpdateVerificationError("Cached metadata is owned by another account")


class VerifiedTargetClient:
    def __init__(
        self,
        metadata_dir: Path,
        staging_dir: Path,
        metadata_base_url: str,
        target_base_url: str,
        trusted_root: Path | None = None,
    ):
        self.metadata_dir = Path(metadata_dir)
        self.staging_dir = Path(staging_dir)
        self.trusted_root = Path(trusted_root) if trusted_root is not None else None
        for url in (metadata_base_url, target_base_url):
            parts = urlsplit(url)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise UpdateVerificationError(
                    "Update repository must use an explicit HTTPS base URL"
                )
        self.metadata_base_url = metadata_base_url
        self.target_base_url = target_base_url

    def download(
        self,
        target_path: str,
        policy: UpdatePolicy,
        *,
        discover: bool = False,
        offline: bool = False,
        cached_target: Path | None = None,
    ) -> VerifiedTarget:
        path = PurePosixPath(target_path)
        if (
            not target_path
            or path.is_absolute()
            or any(p in {".", ".."} for p in target_path.split("/"))
            or "\\" in target_path
            or "%" in target_path
            or "?" in target_path
            or "#" in target_path
            or any(not p for p in target_path.split("/"))
        ):
            raise UpdateVerificationError("Unsafe update target path")
        if self.trusted_root is None:
            raise UpdateVerificationError(
                "Verified updates are disabled: no trusted TUF root is configured"
            )
        try:
            root_stat = self.trusted_root.lstat()
            if not stat.S_ISREG(root_stat.st_mode) or (
                os.name != "nt" and root_stat.st_mode & 0o022
            ):
                raise UpdateVerificationError(
                    "Trusted root must be a regular file without group/world write access"
                )
            if hasattr(os, "getuid") and root_stat.st_uid not in {0, os.getuid()}:
                raise UpdateVerificationError("Trusted root is owned by another account")
            if root_stat.st_size > 512000:
                raise UpdateVerificationError("Trusted root exceeds metadata limit")
            if os.name == "nt":
                verify_private_path(self.trusted_root)
            bootstrap = self.trusted_root.read_bytes()
            from tuf.ngclient import Updater
            from tuf.ngclient.config import UpdaterConfig
        except ImportError as exc:
            raise UpdateVerificationError(
                "Verified updates require the updates optional dependency"
            ) from exc
        except (OSError, PermissionProtectionError) as exc:
            raise UpdateVerificationError("Trusted root could not be read or trusted") from exc
        staged = None
        try:
            for directory in (self.metadata_dir, self.staging_dir):
                missing = []
                cursor = directory
                while not cursor.exists():
                    missing.append(cursor)
                    cursor = cursor.parent
                for new_directory in reversed(missing):
                    create_private_directory(new_directory)
                info = directory.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise UpdateVerificationError("Update storage must be a directory")
                verify_private_path(directory)
                if hasattr(os, "getuid") and info.st_uid != os.getuid():
                    raise UpdateVerificationError("Update directory is owned by another account")
            _metadata_permissions(self.metadata_dir, protect=False)
            from tuf.ngclient.fetcher import FetcherInterface

            metadata_dir = self.metadata_dir

            class OfflineFetcher(FetcherInterface):
                def _fetch(self, url):
                    from tuf.api.exceptions import DownloadHTTPError

                    name = urlsplit(url).path.rsplit("/", 1)[-1]
                    if re.fullmatch(r"[1-9][0-9]*\.root\.json", name):
                        raise DownloadHTTPError("Offline root rotation disabled", 404)
                    # TUF cache stores role names without consistent-snapshot prefixes.
                    name = re.sub(r"^[0-9]+\.", "", name)
                    cached = metadata_dir / name
                    if not cached.is_file() or cached.is_symlink():
                        raise UpdateVerificationError(
                            "Offline authenticated metadata is incomplete"
                        )
                    return iter([cached.read_bytes()])

            updater = Updater(
                str(self.metadata_dir),
                self.metadata_base_url,
                target_base_url=self.target_base_url,
                config=UpdaterConfig(max_root_rotations=32, max_delegations=16),
                fetcher=OfflineFetcher() if offline else None,
                bootstrap=bootstrap,
            )
            updater.refresh()
            _metadata_permissions(self.metadata_dir, protect=True)
            target = updater.get_targetinfo(target_path)
            _metadata_permissions(self.metadata_dir, protect=True)
            if target is None:
                raise UpdateVerificationError("Target is absent from authenticated metadata")
            if type(target.length) is not int or not 0 < target.length <= policy.max_target_bytes:
                raise UpdateVerificationError("Target exceeds the download size limit")
            if discover:
                signed = target.custom.get("camoufox_pm", {})
                try:
                    if not offline and Version(signed["version"]) <= Version(policy.version):
                        raise UpdateVerificationError(
                            "Target must be newer than the installed manager"
                        )
                    policy = replace(
                        policy, version=signed["version"], browser_version=signed["browser_version"]
                    )
                except (KeyError, TypeError, InvalidVersion) as exc:
                    raise UpdateVerificationError("Invalid authenticated channel target") from exc
            manifest = validate_manifest(target.custom, target_path, policy)
            staged = self.staging_dir / f"verified-update-{uuid4().hex}"
            os.close(create_private_file(staged))
            if offline:
                if cached_target is None:
                    raise UpdateVerificationError(
                        "Offline update requires an authenticated cached target"
                    )
                target.verify_length_and_hashes(cached_target.read_bytes())
                import shutil

                shutil.copyfile(cached_target, staged)
            else:
                updater.download_target(target, filepath=str(staged))
            protect_path(staged)
            with staged.open("r+b") as downloaded:
                os.fsync(downloaded.fileno())
            return VerifiedTarget(staged, manifest, target.length, dict(target.hashes))
        except Exception as exc:
            if staged is not None:
                staged.unlink(missing_ok=True)
            if isinstance(exc, UpdateVerificationError):
                raise
            raise UpdateVerificationError("TUF metadata or target verification failed") from exc
