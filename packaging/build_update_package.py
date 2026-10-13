#!/usr/bin/env python3
"""Build deterministic update transport from a final signed standalone tree.

Run after codesign/notarization/stapling or Authenticode. This does not sign TUF
metadata or enable runtime trust. Use the resulting ZIP's digest/length in TUF.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import zipfile
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from camoufox_pm.core.update_package import (
    MANIFEST,
    MAX_MANIFEST,
    MAX_TARGET,
    PackageEntry,
    _check_directory_bounds,
    _digest,
    _inventory,
    _verify_links,
)


def build_update_package(source: Path, output: Path, *, platform: str) -> None:
    source = Path(source).absolute()
    output = Path(output).absolute()
    if platform not in {"darwin", "windows"}:
        raise ValueError("Linux releases remain managed by their package manager")
    if source.is_symlink() or not source.is_dir() or output.is_relative_to(source):
        raise ValueError("Require a real source directory and output outside its tree")
    entries = []
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            candidate = Path(directory) / name
            relative = candidate.relative_to(source).as_posix()
            info = candidate.lstat()
            if getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Reparse points are unsupported")
            if stat.S_ISLNK(info.st_mode):
                entries.append(
                    PackageEntry(relative, "symlink", 0o777, target=os.readlink(candidate))
                )
            elif stat.S_ISDIR(info.st_mode):
                entries.append(PackageEntry(relative, "directory", 0o755))
            elif stat.S_ISREG(info.st_mode):
                entries.append(
                    PackageEntry(
                        relative,
                        "file",
                        0o755 if info.st_mode & 0o111 else 0o644,
                        info.st_size,
                        _digest(candidate),
                    )
                )
            else:
                raise ValueError("Special filesystem entries are unsupported")
    entries.sort(key=lambda entry: entry.path)
    records = []
    for entry in entries:
        record = {
            key: value for key, value in asdict(entry).items() if key in {"path", "kind", "mode"}
        }
        if entry.kind == "file":
            record.update(length=entry.length, sha256=entry.sha256)
        elif entry.kind == "symlink":
            record["target"] = entry.target
        records.append(record)
    manifest = json.dumps(
        {"protocol": 1, "entries": records}, sort_keys=True, separators=(",", ":")
    ).encode()
    if len(manifest) > MAX_MANIFEST:
        raise ValueError("Inventory exceeds transport limit")
    verified = _inventory(manifest, platform)
    _verify_links(source, verified)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary, "x", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:

            def write(name: str, data: bytes) -> None:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, data)

            write(MANIFEST, manifest)
            for entry in entries:
                if entry.kind == "symlink":
                    write("payload/" + entry.path, entry.target.encode())
                elif entry.kind == "file":
                    # Streaming preserves bounded memory for bundled browsers.
                    info = zipfile.ZipInfo("payload/" + entry.path, (1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    digest = hashlib.sha256()
                    length = 0
                    with (
                        (source / entry.path).open("rb") as stream,
                        archive.open(info, "w", force_zip64=True) as dest,
                    ):
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            length += len(chunk)
                            digest.update(chunk)
                            dest.write(chunk)
                    if length != entry.length or digest.hexdigest() != entry.sha256:
                        raise ValueError("Source changed during package creation")
        if temporary.stat().st_size > MAX_TARGET:
            raise ValueError("Archive exceeds transport limit")
        with temporary.open("rb") as stream:
            _check_directory_bounds(stream, temporary.stat().st_size)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--platform", choices=("darwin", "windows"), required=True)
    args = parser.parse_args()
    build_update_package(args.source, args.output, platform=args.platform)
