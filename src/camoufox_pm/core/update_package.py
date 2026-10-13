"""Bounded ZIP transport and immutable inventory for authenticated update trees.

This module establishes content integrity, never publisher trust. The caller must
run the native publisher verifier and verify_tree again immediately before apply.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .update_permissions import (
    create_private_directory,
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)
from .update_targets import UpdateVerificationError, VerifiedTarget

MANIFEST = "update-package.json"
MAX_ENTRIES = 50_000
MAX_TARGET = 2 * 1024**3
MAX_DIRECTORY = 32 * 1024**2
MAX_MANIFEST = 16 * 1024**2
MAX_UNPACKED = 8 * 1024**3
MAX_FILE = 2 * 1024**3
MAX_RATIO = 1000


@dataclass(frozen=True)
class PackageEntry:
    path: str
    kind: str
    mode: int
    length: int = 0
    sha256: str = ""
    target: str = ""


@dataclass(frozen=True)
class VerifiedPackage:
    path: Path
    entries: tuple[PackageEntry, ...]
    manifest_json: str

    @property
    def manifest(self) -> dict:
        """A fresh copy of the TUF-authenticated compatibility manifest."""
        return json.loads(self.manifest_json)

    def verify_tree(self, path: Path | None = None) -> None:
        """Reject any added, removed, changed, or redirected installation entry."""
        root = Path(path) if path is not None else self.path
        reject_reparse_paths(root)
        if not root.is_dir():
            raise UpdateVerificationError("Update tree is missing")
        actual = []
        expected = {entry.path: entry for entry in self.entries}
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                candidate = Path(directory) / name
                relative = candidate.relative_to(root).as_posix()
                entry = expected.get(relative)
                if entry is None:
                    raise UpdateVerificationError(
                        "Update tree differs from authenticated inventory"
                    )
                info = candidate.lstat()
                if getattr(info, "st_file_attributes", 0) & 0x400:
                    raise UpdateVerificationError("Update tree contains a reparse point")
                if stat.S_ISLNK(info.st_mode):
                    actual.append(
                        PackageEntry(relative, "symlink", 0o777, target=os.readlink(candidate))
                    )
                elif stat.S_ISDIR(info.st_mode):
                    actual.append(PackageEntry(relative, "directory", _mode(info, True)))
                elif stat.S_ISREG(info.st_mode):
                    if entry.kind != "file" or info.st_size != entry.length:
                        raise UpdateVerificationError(
                            "Update tree differs from authenticated inventory"
                        )
                    actual.append(
                        PackageEntry(
                            relative, "file", _mode(info, False), info.st_size, _digest(candidate)
                        )
                    )
                else:
                    raise UpdateVerificationError("Update tree contains a special entry")
        if tuple(sorted(actual, key=lambda entry: entry.path)) != self.entries:
            raise UpdateVerificationError("Update tree differs from authenticated inventory")
        _verify_links(root, self.entries)


def _mode(info: os.stat_result, directory: bool) -> int:
    # Windows does not retain POSIX executable bits; native ACLs protect each
    # extracted entry from creation. POSIX mode changes are part of the tree binding.
    if os.name == "nt":
        return 0o755 if directory else 0o644
    return stat.S_IMODE(info.st_mode)


def _create_tree_directory(path: Path) -> None:
    if os.name == "nt":
        create_private_directory(path)
    else:
        path.mkdir(mode=0o755)


def _create_tree_file(path: Path):
    if os.name == "nt":
        return os.fdopen(create_private_file(path), "wb")
    return path.open("xb")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise UpdateVerificationError("Invalid package path")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts) or "\\" in name:
        raise UpdateVerificationError("Unsafe package path")
    for part in parts:
        if (
            part.endswith((".", " "))
            or any(ord(c) < 32 or c in ':*?<>|"' for c in part)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        ):
            raise UpdateVerificationError("Package path is not portable")
    return name


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateVerificationError("Duplicate package manifest key")
        result[key] = value
    return result


def _inventory(data: bytes, platform: str) -> tuple[PackageEntry, ...]:
    try:
        manifest = json.loads(data, object_pairs_hook=_no_duplicates)
        if (
            set(manifest) != {"protocol", "entries"}
            or type(manifest["protocol"]) is not int
            or manifest["protocol"] != 1
        ):
            raise ValueError("Unknown package protocol")
        items = manifest["entries"]
        if not isinstance(items, list) or not 0 < len(items) <= MAX_ENTRIES:
            raise ValueError("Invalid inventory size")
        entries = []
        names = set()
        total = 0
        for item in items:
            name = _safe_name(item["path"])
            folded = unicodedata.normalize("NFC", name).casefold()
            if folded in names:
                raise ValueError("Duplicate/colliding entry")
            names.add(folded)
            kind = item["kind"]
            fields = {"path", "kind", "mode"}
            if kind == "file":
                fields |= {"length", "sha256"}
                length = item["length"]
                if type(length) is not int or not 0 <= length <= MAX_FILE:
                    raise ValueError("Invalid file length")
                if not isinstance(item["sha256"], str) or not re.fullmatch(
                    "[0-9a-f]{64}", item["sha256"]
                ):
                    raise ValueError("Invalid file digest")
                total += length
            elif kind == "symlink":
                fields |= {"target"}
                if platform != "darwin" or not isinstance(item["target"], str):
                    raise ValueError("Symlinks require a macOS package")
                target = item["target"]
                if (
                    not target
                    or len(target) > 1024
                    or "\\" in target
                    or PurePosixPath(target).is_absolute()
                ):
                    raise ValueError("Invalid link target")
                if any(ord(c) < 32 for c in target):
                    raise ValueError("Invalid link target")
            elif kind != "directory":
                raise ValueError("Unsupported entry")
            allowed_modes = (
                {0o755} if kind == "directory" else {0o777} if kind == "symlink" else {0o644, 0o755}
            )
            if (
                type(item["mode"]) is not int
                or item["mode"] not in allowed_modes
                or set(item) != fields
            ):
                raise ValueError("Invalid entry fields/mode")
            if os.name == "nt" and kind == "file":
                item = {**item, "mode": 0o644}
            entries.append(PackageEntry(**item))
        if total > MAX_UNPACKED:
            raise ValueError("Package exceeds extraction limit")
        by_name = {entry.path: entry for entry in entries}
        for entry in entries:
            for parent in PurePosixPath(entry.path).parents:
                if str(parent) != "." and (
                    str(parent) not in by_name or by_name[str(parent)].kind != "directory"
                ):
                    raise ValueError("Missing directory or entry below symlink")
        return tuple(sorted(entries, key=lambda entry: entry.path))
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise UpdateVerificationError("Invalid package inventory") from exc


def _verify_links(root: Path, entries: tuple[PackageEntry, ...]) -> None:
    for entry in entries:
        if entry.kind == "symlink":
            try:
                destination = (root / entry.path).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise UpdateVerificationError("Broken or cyclic package symlink") from exc
            if not destination.is_relative_to(root.resolve()):
                raise UpdateVerificationError("Package symlink escapes tree")


def _check_archive_names(members: list[zipfile.ZipInfo]) -> None:
    # ZipInfo retains the decoded on-disk name before NUL truncation and Windows
    # separator normalization. Never validate only its sanitized filename.
    for member in members:
        if member.orig_filename != member.filename:
            raise UpdateVerificationError("Archive member name was normalized or truncated")
        name = member.orig_filename
        _safe_name(name[:-1] if name.endswith("/") else name)


def _check_directory_bounds(stream, length: int) -> None:
    # Bound allocation before ZipFile constructs the central directory inventory.
    # This contract never needs a ZIP64 end record: <= 2 GiB and < 65535 entries.
    stream.seek(max(0, length - 65557))
    tail = stream.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise UpdateVerificationError("Missing ZIP directory")
    _, disk, directory_disk, count_disk, count, size, start, comment = struct.unpack_from(
        "<4s4H2IH", tail, offset
    )
    if (
        disk
        or directory_disk
        or count_disk != count
        or count > MAX_ENTRIES + 1
        or size > MAX_DIRECTORY
        or start + size > length
        or len(tail) - offset != 22 + comment
        or b"PK\x06\x07" in tail[max(0, offset - 20) : offset]
    ):
        raise UpdateVerificationError("Unsupported or excessive ZIP directory")
    stream.seek(start)
    consumed = 0
    actual_count = 0
    while consumed < size:
        header = stream.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise UpdateVerificationError("Invalid ZIP central directory")
        name_size, extra_size, comment_size = struct.unpack_from("<3H", header, 28)
        record_size = 46 + name_size + extra_size + comment_size
        consumed += record_size
        actual_count += 1
        if consumed > size or actual_count > MAX_ENTRIES + 1:
            raise UpdateVerificationError("Excessive ZIP central directory")
        stream.seek(record_size - 46, 1)
    if actual_count != count:
        raise UpdateVerificationError("ZIP central directory entry count differs")


def extract_verified_package(target: VerifiedTarget, staging: Path) -> VerifiedPackage:
    """Recheck a TUF target and extract its exact inventory into fresh private staging.

    staging must already exist and have native owner-only protection. Linux
    system-package updates intentionally have no automatic extraction contract.
    """
    if target.manifest.get("platform") not in {"darwin", "windows"}:
        raise UpdateVerificationError("Only macOS/Windows standalone trees support automatic apply")
    staging = Path(staging).absolute()
    reject_reparse_paths(staging)
    verify_private_path(staging)
    reject_reparse_paths(target.path)
    if not stat.S_ISREG(target.path.lstat().st_mode):
        raise UpdateVerificationError("Update target is not a regular file")
    if type(target.length) is not int or not 0 < target.length <= MAX_TARGET:
        raise UpdateVerificationError("Update target exceeds archive limit")
    if not target.hashes or any(name not in {"sha256", "sha512"} for name in target.hashes):
        raise UpdateVerificationError("Update target needs supported authenticated hashes")
    root = staging / ("package-" + uuid4().hex)
    try:
        # Read through the same open descriptor used by ZIP, avoiding path swap
        # between hash verification and parsing. Inventory hashes also bind bytes
        # if a same-account writer changes the underlying file while it is open.
        with target.path.open("rb") as stream:
            _authenticate_archive(target, stream)
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                _check_archive_names(members)
                if len(members) > MAX_ENTRIES + 1 or len(
                    {member.filename for member in members}
                ) != len(members):
                    raise UpdateVerificationError("Duplicate or excessive archive entries")
                metadata = archive.getinfo(MANIFEST)
                if metadata.file_size > MAX_MANIFEST:
                    raise UpdateVerificationError("Package inventory exceeds limit")
                entries = _inventory(archive.read(metadata), target.manifest["platform"])
                expected = {MANIFEST} | {
                    "payload/" + entry.path for entry in entries if entry.kind != "directory"
                }
                if {member.filename for member in members} != expected:
                    raise UpdateVerificationError("Archive differs from package inventory")
                for member in members:
                    mode = member.external_attr >> 16
                    if (
                        member.flag_bits & 1
                        or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                        or member.file_size
                        > (MAX_MANIFEST if member.filename == MANIFEST else MAX_FILE)
                        or member.file_size > max(1, member.compress_size) * MAX_RATIO
                    ):
                        raise UpdateVerificationError(
                            "Unsafe or excessively compressed archive member"
                        )
                create_private_directory(root)
                for entry in entries:
                    candidate = root / entry.path
                    if entry.kind == "directory":
                        _create_tree_directory(candidate)
                        if os.name != "nt":
                            candidate.chmod(entry.mode)
                    elif entry.kind == "file":
                        member = archive.getinfo("payload/" + entry.path)
                        if member.file_size != entry.length:
                            raise UpdateVerificationError(
                                "Archive file length differs from inventory"
                            )
                        digest = hashlib.sha256()
                        length = 0
                        with (
                            archive.open(member) as source,
                            _create_tree_file(candidate) as destination,
                        ):
                            while chunk := source.read(1024 * 1024):
                                length += len(chunk)
                                if length > entry.length:
                                    raise UpdateVerificationError(
                                        "Extraction exceeds declared length"
                                    )
                                digest.update(chunk)
                                destination.write(chunk)
                        if length != entry.length or digest.hexdigest() != entry.sha256:
                            raise UpdateVerificationError("Extracted file differs from inventory")
                        if os.name != "nt":
                            candidate.chmod(entry.mode)
                    else:
                        member = archive.getinfo("payload/" + entry.path)
                        encoded = entry.target.encode("utf-8")
                        if member.file_size != len(encoded) or archive.read(member) != encoded:
                            raise UpdateVerificationError("Archive symlink differs from inventory")
                for entry in entries:
                    if entry.kind == "symlink":
                        (root / entry.path).symlink_to(entry.target)
            _authenticate_archive(target, stream)
        result = VerifiedPackage(root, entries, json.dumps(target.manifest, sort_keys=True))
        result.verify_tree()
        return result
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        if isinstance(exc, UpdateVerificationError):
            raise
        raise UpdateVerificationError("Update package extraction failed") from exc


def _authenticate_archive(target: VerifiedTarget, stream) -> None:
    stream.seek(0)
    if type(target.length) is not int or not 0 < target.length <= MAX_TARGET:
        raise UpdateVerificationError("Update target exceeds archive limit")
    if not target.hashes or any(name not in {"sha256", "sha512"} for name in target.hashes):
        raise UpdateVerificationError("Update target needs supported authenticated hashes")
    hashes = {name: hashlib.new(name) for name in target.hashes}
    length = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        length += len(chunk)
        if length > target.length:
            raise UpdateVerificationError("Update target length changed")
        for digest in hashes.values():
            digest.update(chunk)
    if length != target.length or any(
        hashes[name].hexdigest() != value for name, value in target.hashes.items()
    ):
        raise UpdateVerificationError("Update target digest changed")
    _check_directory_bounds(stream, length)
    stream.seek(0)


def extract_verified_browser_archive(target: VerifiedTarget, staging: Path) -> VerifiedPackage:
    """Bind an upstream Camoufox ZIP authenticated independently through TUF.

    Retain the exact upstream root/layout; do not strip wrapper directories. The
    browser installer owns exact version identity/cache metadata and activation.
    There is no unsigned URL, digest override, or locally pinned trust fallback.
    """
    if target.manifest.get("component") != "camoufox" or target.manifest.get("platform") not in {
        "darwin",
        "windows",
    }:
        raise UpdateVerificationError(
            "Upstream extraction requires an authenticated standalone browser target"
        )
    staging = Path(staging).absolute()
    reject_reparse_paths(staging)
    verify_private_path(staging)
    reject_reparse_paths(target.path)
    if not stat.S_ISREG(target.path.lstat().st_mode):
        raise UpdateVerificationError("Browser target is not a regular file")
    root = staging / ("browser-package-" + uuid4().hex)
    try:
        with target.path.open("rb") as stream:
            _authenticate_archive(target, stream)
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                _check_archive_names(members)
                if not 0 < len(members) <= MAX_ENTRIES or len(
                    {item.filename for item in members}
                ) != len(members):
                    raise UpdateVerificationError("Duplicate or excessive upstream archive entries")
                entries = {}
                sources = {}
                total = 0
                for member in members:
                    is_dir = member.is_dir()
                    name = _safe_name(member.filename[:-1] if is_dir else member.filename)
                    raw_mode = member.external_attr >> 16
                    kind = stat.S_IFMT(raw_mode)
                    if (
                        member.flag_bits & 1
                        or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or kind not in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}
                        or (is_dir and kind not in {0, stat.S_IFDIR})
                        or (not is_dir and kind == stat.S_IFDIR)
                        or member.file_size > MAX_FILE
                        or member.file_size > max(1, member.compress_size) * MAX_RATIO
                    ):
                        raise UpdateVerificationError("Unsafe upstream archive member")
                    total += member.file_size
                    if total > MAX_UNPACKED:
                        raise UpdateVerificationError("Upstream extraction exceeds size limit")
                    if is_dir:
                        if member.file_size:
                            raise UpdateVerificationError("Directory member contains data")
                        entry = PackageEntry(name, "directory", 0o755)
                    elif kind == stat.S_IFLNK:
                        if target.manifest["platform"] != "darwin" or member.file_size > 1024:
                            raise UpdateVerificationError("Unsupported upstream symlink")
                        entry = PackageEntry(
                            name, "symlink", 0o777, target=archive.read(member).decode("utf-8")
                        )
                    else:
                        mode = 0o755 if raw_mode & 0o111 and os.name != "nt" else 0o644
                        entry = PackageEntry(name, "file", mode, member.file_size, "0" * 64)
                    if name in sources:
                        raise UpdateVerificationError("Colliding upstream member names")
                    entries[name] = entry
                    sources[name] = member
                for entry in tuple(entries.values()):
                    for parent in PurePosixPath(entry.path).parents:
                        if str(parent) != "." and str(parent) not in entries:
                            entries[str(parent)] = PackageEntry(str(parent), "directory", 0o755)
                records = []
                for entry in entries.values():
                    fields = {"path", "kind", "mode"}
                    fields |= (
                        {"length", "sha256"}
                        if entry.kind == "file"
                        else {"target"}
                        if entry.kind == "symlink"
                        else set()
                    )
                    records.append(
                        {key: value for key, value in asdict(entry).items() if key in fields}
                    )
                ordered = _inventory(
                    json.dumps({"protocol": 1, "entries": records}).encode(),
                    target.manifest["platform"],
                )
                create_private_directory(root)
                final = []
                for entry in ordered:
                    candidate = root / entry.path
                    if entry.kind == "directory":
                        _create_tree_directory(candidate)
                        if os.name != "nt":
                            candidate.chmod(0o755)
                    elif entry.kind == "file":
                        digest = hashlib.sha256()
                        length = 0
                        with (
                            archive.open(sources[entry.path]) as source,
                            _create_tree_file(candidate) as dest,
                        ):
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                length += len(chunk)
                                if length > entry.length:
                                    raise UpdateVerificationError(
                                        "Upstream member exceeds declared size"
                                    )
                                digest.update(chunk)
                                dest.write(chunk)
                        if length != entry.length:
                            raise UpdateVerificationError("Upstream member length differs")
                        if os.name != "nt":
                            candidate.chmod(entry.mode)
                        entry = replace(entry, sha256=digest.hexdigest())
                    final.append(entry)
                for entry in final:
                    if entry.kind == "symlink":
                        (root / entry.path).symlink_to(entry.target)
            # Unlike a manifest-bearing package, upstream hashes are derived.
            # Reauthenticate after reading to reject concurrent archive mutation.
            _authenticate_archive(target, stream)
        result = VerifiedPackage(root, tuple(final), json.dumps(target.manifest, sort_keys=True))
        result.verify_tree()
        return result
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        if isinstance(exc, UpdateVerificationError):
            raise
        raise UpdateVerificationError("Upstream browser extraction failed") from exc
