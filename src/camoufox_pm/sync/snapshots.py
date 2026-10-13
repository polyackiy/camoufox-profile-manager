"""Bounded portable snapshots, kept private until encrypted by ``sync.crypto``.

These functions do not establish remote trust or a browser's quiescence. The
engine verifies the signed selected revision before validation, and supplies a
process/lock proof while the local profile-operation lease is held for capture.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import struct
import unicodedata
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from camoufox_pm.core.archive_paths import portable_component
from camoufox_pm.core.fingerprint_store import freeze
from camoufox_pm.core.models import BrowserSettings, Profile, ProxyConfig
from camoufox_pm.core.profile_archive import _PROFILE_LOCKS, _should_skip
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

MAX_FILES = 20_000
MAX_BYTES = 2 * 1024**3 - 16 * 1024**2
MAX_METADATA = 8 * 1024**2
MAX_DIRECTORY = 8 * 1024**2
MAX_ARCHIVE = 2 * 1024**3
CHUNK = 1024**2
_FORBIDDEN = frozenset({"secret.key", ".env", "manager.db", "profiles.db"})


class SnapshotError(ValueError):
    """Snapshot unavailable, unsafe or incompatible; never contains secret values."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Compatibility(_Strict):
    """Exact tested target, supplied from trusted local runtime information."""

    protocol: Literal[1] = 1
    schema_version: Literal[1] = 1
    browser_build: str = Field(min_length=1, max_length=128)
    os: Literal["linux", "windows", "macos"]
    architecture: Literal["x86_64", "aarch64"]
    capabilities: tuple[str, ...] = ()

    @field_validator("protocol", "schema_version", mode="before")
    @classmethod
    def version_is_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Version must be an integer")
        return value

    @field_validator("capabilities")
    @classmethod
    def capabilities_are_known(cls, value):
        # No portable extension mapping exists yet. Never ignore a requirement.
        if value:
            raise ValueError("Unsupported snapshot capability")
        return value


class PortableProfile(_Strict):
    name: str = Field(min_length=1, max_length=512)
    tags: tuple[str, ...] = ()
    notes: str | None = Field(default=None, max_length=64 * 1024)
    browser_settings: dict[str, Any]
    proxy: dict[str, Any] | None = None
    fingerprint: dict[str, Any] | None = None

    @field_validator("browser_settings")
    @classmethod
    def settings_are_allowlisted(cls, value):
        if set(value) - BrowserSettings.model_fields.keys():
            raise ValueError("Unknown browser setting")
        return BrowserSettings.model_validate(value).model_dump(mode="json")

    @field_validator("proxy")
    @classmethod
    def proxy_is_portable(cls, value):
        if value is None:
            return value
        if set(value) - ProxyConfig.model_fields.keys():
            raise ValueError("Unknown proxy setting")
        proxy = ProxyConfig.model_validate(value)
        if proxy.password and proxy.password.startswith("enc:"):
            raise ValueError("Local encrypted proxy password cannot be synchronized")
        return proxy.model_dump(mode="json")

    @field_validator("fingerprint")
    @classmethod
    def fingerprint_is_portable(cls, value):
        if value is not None and set(value) - set(freeze(value)):
            raise ValueError("Nonportable fingerprint configuration")
        return value

    @field_validator("tags")
    @classmethod
    def tags_are_valid(cls, value):
        from camoufox_pm.core.models import normalize_tags

        return tuple(normalize_tags(list(value)))

    def apply_fields(self) -> dict[str, Any]:
        """Allowlist for mapped-profile update; local IDs/grants never originate here."""
        return self.model_dump(mode="json")


class FileEntry(_Strict):
    size: int = Field(ge=0, le=MAX_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SnapshotMetadata(_Strict):
    format_version: Literal[1, 2] = 1
    lifecycle: Literal["active", "trashed"] | None = None
    compatibility: Compatibility
    profile: PortableProfile
    files: dict[str, FileEntry]

    @field_validator("format_version", mode="before")
    @classmethod
    def format_is_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Version must be an integer")
        return value

    @model_validator(mode="after")
    def lifecycle_version(self):
        if (self.format_version == 1 and self.lifecycle is not None) or (
            self.format_version == 2 and self.lifecycle is None
        ):
            raise ValueError("Lifecycle requires snapshot format version 2")
        return self


def _name(name: str) -> tuple[str, ...]:
    parts = name.split("/")
    if parts in ([".camoufox-pm-sync.json"], ["data", ".camoufox-pm-sync.json"]):
        raise SnapshotError("Snapshot contains a local synchronization marker")
    if (
        len(name.encode("utf-8")) > 1024
        or len(parts) > 32
        or any(
            part in {".", ".."}
            or not portable_component(part)
            or len(part.encode("utf-8")) > 240
            or unicodedata.normalize("NFC", part) != part
            for part in parts
        )
    ):
        raise SnapshotError("Snapshot contains an unsafe path")
    if any(part.casefold() in _FORBIDDEN for part in parts):
        raise SnapshotError("Snapshot contains manager-private data")
    return tuple(parts)


def _check_names(names: list[str]) -> None:
    """Reject aliases, including aliases in directory components, and file parents."""
    seen: dict[tuple[str, ...], tuple[str, ...]] = {}
    files: set[tuple[str, ...]] = set()
    for name in names:
        parts = _name(name)
        key = tuple(part.casefold() for part in parts)
        if key in files:
            raise SnapshotError("Duplicate snapshot path")
        files.add(key)
        for length in range(1, len(parts) + 1):
            prefix, folded = parts[:length], key[:length]
            if folded in seen and seen[folded] != prefix:
                raise SnapshotError("Colliding snapshot paths")
            seen[folded] = prefix
    for key in files:
        if any(key[:length] in files for length in range(1, len(key))):
            raise SnapshotError("Snapshot file is a directory ancestor")


def _regular_open(path: Path):
    reject_reparse_paths(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SnapshotError("Snapshot input must be a regular non-linked file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    after = os.fstat(fd)
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_nlink != 1
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
    ):
        os.close(fd)
        raise SnapshotError("Snapshot input changed during capture")
    return os.fdopen(fd, "rb")


def _metadata(profile: Profile, compatibility: Compatibility, files) -> SnapshotMetadata:
    if profile.extensions:
        raise SnapshotError("Portable extension mapping is required before synchronization")
    return SnapshotMetadata(
        compatibility=Compatibility.model_validate(compatibility.model_dump()),
        profile=PortableProfile(
            name=profile.name,
            tags=tuple(profile.tags),
            notes=profile.notes,
            browser_settings=profile.browser_settings.model_dump(mode="json"),
            proxy=profile.proxy.model_dump(mode="json") if profile.proxy else None,
            fingerprint=profile.fingerprint,
        ),
        files=files,
    )


def _json(value: SnapshotMetadata) -> bytes:
    data = json.dumps(
        value.model_dump(mode="json", exclude_none=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("ascii")
    if len(data) > MAX_METADATA:
        raise SnapshotError("Snapshot metadata exceeds its limit")
    return data


def _build_snapshot(
    profile: Profile,
    data_dir: Path,
    destination: Path,
    compatibility: Compatibility,
    *,
    lifecycle: Literal["active", "trashed"] | None = None,
) -> SnapshotMetadata:
    """Strict capture under an engine-held lease and independently proven quiescence.

    ``destination`` is plaintext private staging, never an uploadable artifact.
    Missing data, unreadable files or changes during capture fail the whole archive.
    """
    verify_private_path(destination.parent)
    reject_reparse_paths(data_dir)
    if not data_dir.is_dir():
        raise SnapshotError("Profile data directory is missing")
    _metadata(profile, compatibility, {})
    if destination.absolute().is_relative_to(data_dir.absolute()):
        raise SnapshotError("Snapshot destination cannot be inside browser data")
    paths: list[tuple[str, Path]] = []
    directories: dict[Path, tuple[int, int]] = {}
    entry_count = 0

    def walk_error(error):
        raise SnapshotError("Profile directory is unreadable") from None

    for directory, subdirs, filenames in os.walk(data_dir, followlinks=False, onerror=walk_error):
        directory_path = Path(directory)
        reject_reparse_paths(directory_path)
        directory_stat = directory_path.stat()
        directories[directory_path] = (directory_stat.st_mtime_ns, directory_stat.st_ctime_ns)
        for child in list(subdirs) + filenames:
            path = Path(directory) / child
            relative = path.relative_to(data_dir)
            if relative.parts == (".camoufox-pm-sync.json",):
                continue
            if len(relative.parts) == 1 and child in _PROFILE_LOCKS:
                # Quiescence callback, not absence of this marker, authorizes capture.
                if child in subdirs:
                    subdirs.remove(child)
                continue
            reject_reparse_paths(path)
            mode = path.lstat().st_mode
            if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                raise SnapshotError("Profile contains a nonregular filesystem entry")
            if _should_skip(relative):
                if child in subdirs:
                    subdirs.remove(child)
                continue
            _name(relative.as_posix())
            entry_count += 1
            if entry_count > MAX_FILES:
                raise SnapshotError("Snapshot has too many filesystem entries")
            if stat.S_ISREG(mode):
                paths.append((relative.as_posix(), path))
                if len(paths) > MAX_FILES:
                    raise SnapshotError("Snapshot has too many files")
    _check_names([name for name, _ in paths])
    files: dict[str, FileEntry] = {}
    written = 0
    captured: dict[Path, tuple[int, int, int, int]] = {}
    created = False
    try:
        fd = create_private_file(destination)
        created = True
        with os.fdopen(fd, "wb") as output:
            # Stored entries make CPU/disk limits predictable even for very compressible data.
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_STORED, allowZip64=False
            ) as archive:
                for name, path in sorted(paths):
                    digest = hashlib.sha256()
                    size = 0
                    with _regular_open(path) as source, archive.open("data/" + name, "w") as target:
                        before = os.fstat(source.fileno())
                        while chunk := source.read(CHUNK):
                            size += len(chunk)
                            written += len(chunk)
                            if written > MAX_BYTES:
                                raise SnapshotError("Snapshot exceeds the byte limit")
                            digest.update(chunk)
                            target.write(chunk)
                        after = os.fstat(source.fileno())
                        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                            after.st_size,
                            after.st_mtime_ns,
                            after.st_ctime_ns,
                        ) or size != after.st_size:
                            raise SnapshotError("Profile changed during capture")
                    captured[path] = (
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    files[name] = FileEntry(size=size, sha256=digest.hexdigest())
                for directory_path, checkpoint in directories.items():
                    reject_reparse_paths(directory_path)
                    current = directory_path.stat()
                    if (current.st_mtime_ns, current.st_ctime_ns) != checkpoint:
                        raise SnapshotError("Profile directory changed during capture")
                for path, file_checkpoint in captured.items():
                    reject_reparse_paths(path)
                    # Windows pathname stat and descriptor fstat can expose
                    # different timestamp precision/semantics. Compare through
                    # the same descriptor API, retaining inode/size/time checks
                    # and _regular_open's replacement/special-file protections.
                    with _regular_open(path) as verification:
                        current = os.fstat(verification.fileno())
                    if (
                        current.st_ino,
                        current.st_size,
                        current.st_mtime_ns,
                        current.st_ctime_ns,
                    ) != file_checkpoint:
                        raise SnapshotError("Profile changed during capture")
                metadata = _metadata(profile, compatibility, files)
                if lifecycle is not None:
                    metadata = SnapshotMetadata.model_validate(
                        {**metadata.model_dump(), "format_version": 2, "lifecycle": lifecycle}
                    )
                archive.writestr("snapshot.json", _json(metadata))
            output.flush()
            os.fsync(output.fileno())
        return metadata
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


async def capture_snapshot(
    manager,
    profile_id: str,
    destination: Path,
    compatibility: Compatibility,
    prove_closed: Callable[[Path], None],
) -> SnapshotMetadata:
    """Capture while renewing the local lease and draining I/O on cancellation.

    ``prove_closed`` MUST reject an active/unknown process or OS browser lock.
    The engine additionally holds the application maintenance/sync operation gate.
    """
    async with profile_operation(manager, profile_id) as profile:
        data = profile_data_path(manager, profile)

        def capture():
            prove_closed(data)
            result = build_snapshot(profile, data, destination, compatibility)
            try:
                prove_closed(data)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return result

        return await run_file_operation(capture)


def _zip_preflight(source) -> None:
    """Bound central directory before ZipFile creates one Python object per member."""
    source.seek(0, 2)
    size = source.tell()
    if size > MAX_ARCHIVE or size < 22:
        raise SnapshotError("Invalid snapshot archive size")
    source.seek(max(0, size - 65557))
    tail = source.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise SnapshotError("Snapshot directory is missing")
    # ZipFile consults the ZIP64 locator immediately before EOCD and silently
    # replaces these ordinary EOCD limits with ZIP64 counts/offsets. Our format
    # never emits ZIP64: reject it before the parser can allocate its directory.
    if offset >= 20 and tail[offset - 20 : offset - 16] == b"PK\x06\x07":
        raise SnapshotError("ZIP64 snapshots are unsupported")
    _, disk, cd_disk, disk_count, count, length, start, comment = struct.unpack(
        "<4s4H2IH", tail[offset : offset + 22]
    )
    if (
        disk
        or cd_disk
        or disk_count != count
        or count > MAX_FILES + 1
        or length > MAX_DIRECTORY
        or start + length != size - len(tail) + offset
        or len(tail) - offset != 22 + comment
        or comment
    ):
        raise SnapshotError("Unsupported or oversized snapshot directory")
    source.seek(0)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("Duplicate snapshot metadata field")
        result[key] = value
    return result


def _validate_snapshot(source: Path, staging: Path, expected: Compatibility) -> SnapshotMetadata:
    """Validate an already authenticated archive into a NEW private staging directory.

    No existing profile is changed. On every failure the newly created staging
    directory is removed. ``staging/data`` is ready for journalled activation.
    """
    verify_private_path(source.parent)
    verify_private_path(source)
    verify_private_path(staging.parent)
    expected = Compatibility.model_validate(expected.model_dump())
    created = False
    try:
        with _regular_open(source) as handle:
            _zip_preflight(handle)
            with zipfile.ZipFile(handle) as archive:
                members = archive.infolist()
                if len(members) > MAX_FILES + 1:
                    raise SnapshotError("Snapshot has too many files")
                names = [member.filename for member in members]
                if names.count("snapshot.json") != 1:
                    raise SnapshotError("Snapshot metadata is missing or repeated")
                _check_names(names)
                total = 0
                for member in members:
                    mode = member.external_attr >> 16
                    if (
                        member.is_dir()
                        or member.compress_type != zipfile.ZIP_STORED
                        or member.flag_bits & 1
                        or member.extra
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                        or member.external_attr & 0x400
                        or member.file_size != member.compress_size
                    ):
                        raise SnapshotError("Unsupported snapshot entry")
                    if member.filename != "snapshot.json" and not member.filename.startswith(
                        "data/"
                    ):
                        raise SnapshotError("Unexpected snapshot entry")
                    total += member.file_size
                if total > MAX_BYTES + MAX_METADATA:
                    raise SnapshotError("Snapshot exceeds the byte limit")
                info = archive.getinfo("snapshot.json")
                if info.file_size > MAX_METADATA:
                    raise SnapshotError("Snapshot metadata exceeds its limit")
                raw = archive.read(info)
                # JSON depth is bounded before parsing to avoid parser recursion/memory abuse.
                depth = 0
                quoted = escaped = False
                for char in raw:
                    if quoted:
                        if escaped:
                            escaped = False
                        elif char == 92:
                            escaped = True
                        elif char == 34:
                            quoted = False
                    elif char == 34:
                        quoted = True
                    elif char in (91, 123):
                        depth += 1
                        if depth > 16:
                            raise SnapshotError("Snapshot metadata is too deeply nested")
                    elif char in (93, 125):
                        depth -= 1
                obj = json.loads(
                    raw,
                    object_pairs_hook=_unique_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(
                        SnapshotError("Invalid JSON number")
                    ),
                )
                # JSON mode permits arrays for strict tuple fields; duplicate check above remains mandatory.
                metadata = SnapshotMetadata.model_validate_json(json.dumps(obj, allow_nan=False))
                if metadata.compatibility != expected:
                    raise SnapshotError("Snapshot browser/platform/protocol is incompatible")
                file_names = [name[5:] for name in names if name.startswith("data/")]
                if set(file_names) != set(metadata.files) or len(metadata.files) > MAX_FILES:
                    raise SnapshotError("Snapshot inventory does not match its files")
                _check_names(list(metadata.files))
                if sum(entry.size for entry in metadata.files.values()) > MAX_BYTES:
                    raise SnapshotError("Snapshot exceeds the byte limit")
                create_private_directory(staging)
                created = True
                create_private_directory(staging / "data")
                for name, entry in metadata.files.items():
                    member = archive.getinfo("data/" + name)
                    if member.file_size != entry.size:
                        raise SnapshotError("Snapshot inventory size differs")
                    destination = staging / "data" / name
                    for parent in reversed(destination.parents):
                        if parent.is_relative_to(staging) and not parent.exists():
                            create_private_directory(parent)
                    digest = hashlib.sha256()
                    size = 0
                    with (
                        archive.open(member) as incoming,
                        os.fdopen(create_private_file(destination), "wb") as output,
                    ):
                        while chunk := incoming.read(CHUNK):
                            size += len(chunk)
                            if size > entry.size:
                                raise SnapshotError("Snapshot member exceeds its byte limit")
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if size != entry.size or digest.hexdigest() != entry.sha256:
                        raise SnapshotError("Snapshot member integrity check failed")
                return metadata
    except BaseException:
        if created:
            shutil.rmtree(staging)
        raise


def build_snapshot(
    profile: Profile,
    data_dir: Path,
    destination: Path,
    compatibility: Compatibility,
    *,
    lifecycle: Literal["active", "trashed"] | None = None,
) -> SnapshotMetadata:
    """Internal lease-held, independently quiescent capture into private plaintext staging.

    Use ``capture_snapshot`` for manager integration. No exception exposes profile
    values, credentials or local paths. All failures preserve an existing destination.
    """
    try:
        return _build_snapshot(profile, data_dir, destination, compatibility, lifecycle=lifecycle)
    except SnapshotError:
        raise
    except Exception:
        raise SnapshotError("Snapshot capture failed validation or private file access") from None


def validate_snapshot(source: Path, staging: Path, expected: Compatibility) -> SnapshotMetadata:
    """Validate authenticated plaintext into a new private staging/data directory.

    Caller must first authenticate the selected signed revision through sync.crypto.
    Existing destination data and local profile authority are never modified here.
    """
    try:
        return _validate_snapshot(source, staging, expected)
    except SnapshotError:
        raise
    except Exception:
        raise SnapshotError("Snapshot validation or private staging failed") from None
