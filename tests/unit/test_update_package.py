from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import zipfile
from pathlib import Path

import pytest

from camoufox_pm.core.update_package import (
    MANIFEST,
    extract_verified_browser_archive,
    extract_verified_package,
)
from camoufox_pm.core.update_permissions import create_private_directory, verify_private_path
from camoufox_pm.core.update_targets import UpdateVerificationError, VerifiedTarget

SPEC = importlib.util.spec_from_file_location(
    "build_update_package", Path(__file__).parents[2] / "packaging/build_update_package.py"
)
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)


def target(path, platform="windows"):
    data = path.read_bytes()
    return VerifiedTarget(
        path, {"platform": platform}, len(data), {"sha256": hashlib.sha256(data).hexdigest()}
    )


@pytest.fixture
def package(tmp_path):
    tree = tmp_path / "source"
    tree.mkdir()
    (tree / "_internal").mkdir()
    (tree / "manager.exe").write_bytes(b"executable")
    (tree / "_internal/adjacent.dll").write_bytes(b"trusted DLL")
    archive = tmp_path / "package.zip"
    producer.build_update_package(tree, archive, platform="windows")
    staging = tmp_path / "staging"
    create_private_directory(staging)
    return tree, archive, staging


def test_deterministic_roundtrip_and_dll_binding(package, tmp_path):
    tree, archive, staging = package
    second = tmp_path / "second.zip"
    producer.build_update_package(tree, second, platform="windows")
    assert archive.read_bytes() == second.read_bytes()
    result = extract_verified_package(target(archive), staging)
    result.verify_tree()
    (result.path / "_internal/adjacent.dll").write_bytes(b"changed DLL")
    with pytest.raises(UpdateVerificationError, match="inventory"):
        result.verify_tree()


@pytest.mark.parametrize("change", ["added", "removed", "mode"])
def test_exact_tree(package, change):
    _, archive, staging = package
    result = extract_verified_package(target(archive), staging)
    if change == "added":
        (result.path / "unexpected.dll").write_bytes(b"x")
    elif change == "removed":
        (result.path / "manager.exe").unlink()
    elif os.name != "nt":
        (result.path / "manager.exe").chmod(0o777)
    else:
        pytest.skip("POSIX mode contract")
    with pytest.raises(UpdateVerificationError, match="inventory"):
        result.verify_tree()


def test_target_rechecked_before_extraction(package):
    _, archive, staging = package
    verified = target(archive)
    archive.write_bytes(b"altered")
    with pytest.raises(UpdateVerificationError, match="digest"):
        extract_verified_package(verified, staging)
    assert not list(staging.iterdir())


def test_platform_boundary(package):
    _, archive, staging = package
    with pytest.raises(UpdateVerificationError, match="standalone"):
        extract_verified_package(target(archive, "linux"), staging)


def rewrite(archive, mutate):
    with zipfile.ZipFile(archive) as zipped:
        contents = {member.filename: zipped.read(member) for member in zipped.infolist()}
    manifest = json.loads(contents[MANIFEST])
    mutate(manifest, contents)
    contents[MANIFEST] = json.dumps(manifest).encode()
    with zipfile.ZipFile(archive, "w") as zipped:
        for name, data in contents.items():
            zipped.writestr(name, data)


@pytest.mark.parametrize(
    "name", ["../escape", "C:/escape", "_internal/../escape", "NUL.txt", "escape\\foo"]
)
def test_unsafe_paths(package, name):
    _, archive, staging = package
    rewrite(archive, lambda manifest, _: manifest["entries"][0].update(path=name))
    with pytest.raises(UpdateVerificationError):
        extract_verified_package(target(archive), staging)
    assert not list(staging.iterdir())


def test_case_collision(package):
    _, archive, staging = package

    def mutate(manifest, _):
        manifest["entries"].append({**manifest["entries"][-1], "path": "MANAGER.EXE"})

    rewrite(archive, mutate)
    with pytest.raises(UpdateVerificationError):
        extract_verified_package(target(archive), staging)


def test_uninventoried_member(package):
    _, archive, staging = package
    with zipfile.ZipFile(archive, "a") as zipped:
        zipped.writestr("payload/injected.dll", b"payload")
    with pytest.raises(UpdateVerificationError, match="inventory"):
        extract_verified_package(target(archive), staging)


def test_payload_hash_and_cleanup(package):
    _, archive, staging = package
    rewrite(archive, lambda _, contents: contents.update({"payload/manager.exe": b"corruption"}))
    with pytest.raises(UpdateVerificationError):
        extract_verified_package(target(archive), staging)
    assert not list(staging.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="macOS symbolic links")
def test_macos_contained_symlinks(tmp_path):
    tree = tmp_path / "source"
    (tree / "Contents/Frameworks/Versions/A").mkdir(parents=True)
    (tree / "Contents/Frameworks/Versions/A/library").write_bytes(b"framework")
    (tree / "Contents/Frameworks/Versions/Current").symlink_to("A")
    (tree / "Contents/Frameworks/library").symlink_to("Versions/Current/library")
    archive = tmp_path / "package.zip"
    producer.build_update_package(tree, archive, platform="darwin")
    staging = tmp_path / "staging"
    create_private_directory(staging)
    result = extract_verified_package(target(archive, "darwin"), staging)
    assert (result.path / "Contents/Frameworks/library").read_bytes() == b"framework"
    result.verify_tree()
    (result.path / "Contents/Frameworks/library").unlink()
    (result.path / "Contents/Frameworks/library").symlink_to("/etc/passwd")
    with pytest.raises(UpdateVerificationError):
        result.verify_tree()


def test_symlink_escape_producer(tmp_path):
    if os.name == "nt":
        pytest.skip("macOS symbolic links")
    tree = tmp_path / "source"
    tree.mkdir()
    (tree / "bad").symlink_to("/etc/passwd")
    with pytest.raises(UpdateVerificationError):
        producer.build_update_package(tree, tmp_path / "package.zip", platform="darwin")


def test_compression_bomb_rejected_before_extract(package):
    _, archive, staging = package
    with zipfile.ZipFile(archive, "r") as zipped:
        contents = {member.filename: zipped.read(member) for member in zipped.infolist()}
    data = b"\0" * (2 * 1024 * 1024)
    manifest = json.loads(contents[MANIFEST])
    for entry in manifest["entries"]:
        if entry["path"] == "manager.exe":
            entry.update(length=len(data), sha256=hashlib.sha256(data).hexdigest())
    contents[MANIFEST] = json.dumps(manifest).encode()
    contents["payload/manager.exe"] = data
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in contents.items():
            zipped.writestr(name, data)
    with pytest.raises(UpdateVerificationError, match="compressed"):
        extract_verified_package(target(archive), staging)
    assert not list(staging.iterdir())


def test_special_archive_entry(package):
    import stat

    _, archive, staging = package
    with zipfile.ZipFile(archive) as zipped:
        contents = {member.filename: zipped.read(member) for member in zipped.infolist()}
    with zipfile.ZipFile(archive, "w") as zipped:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFIFO | 0o644) << 16
            zipped.writestr(info, data)
    with pytest.raises(UpdateVerificationError, match="Unsafe"):
        extract_verified_package(target(archive), staging)


def test_lying_central_count(package):
    import struct

    _, archive, staging = package
    data = bytearray(archive.read_bytes())
    offset = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, offset + 8, 1, 1)
    archive.write_bytes(data)
    with pytest.raises(UpdateVerificationError, match="count differs"):
        extract_verified_package(target(archive), staging)


def raw_name_archive(archive: Path, name: str) -> None:
    # ZipInfo's constructor rewrites Windows backslashes and truncates NULs.
    # Patch equal-length local + central names to exercise actual on-disk bytes.
    raw_name = name.encode("ascii")
    placeholder = b"n" * len(raw_name)
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(placeholder.decode("ascii"), b"browser")
    data = archive.read_bytes()
    assert data.count(placeholder) == 2
    archive.write_bytes(data.replace(placeholder, raw_name))


def test_authenticated_upstream_archive_exact_root(tmp_path):
    archive = tmp_path / "upstream.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("Camoufox.app/Contents/MacOS/camoufox", b"browser")
        zipped.writestr("Camoufox.app/Contents/Resources/library", b"resource")
    verified = target(archive, "darwin")
    verified.manifest["component"] = "camoufox"
    staging = tmp_path / "staging"
    create_private_directory(staging)
    package = extract_verified_browser_archive(verified, staging)
    assert (package.path / "Camoufox.app/Contents/MacOS/camoufox").read_bytes() == b"browser"
    package.verify_tree()
    (package.path / "Camoufox.app/Contents/Resources/library").write_bytes(b"modified")
    with pytest.raises(UpdateVerificationError, match="inventory"):
        package.verify_tree()


@pytest.mark.parametrize(
    "name",
    ["../escape", "folder/../escape", "folder\\escape", "C:/escape", "NUL", "folder\x00escape"],
)
def test_upstream_traversal_rejected(tmp_path, name):
    archive = tmp_path / "upstream.zip"
    raw_name_archive(archive, name)
    verified = target(archive)
    verified.manifest["component"] = "camoufox"
    staging = tmp_path / "staging"
    create_private_directory(staging)
    with pytest.raises(UpdateVerificationError):
        extract_verified_browser_archive(verified, staging)
    assert not list(staging.iterdir())


def test_upstream_missing_authentication_rejected(package):
    _, archive, staging = package
    with pytest.raises(UpdateVerificationError, match="authenticated"):
        extract_verified_browser_archive(target(archive), staging)
    verified = target(archive)
    verified.manifest["component"] = "camoufox"
    verified.hashes.clear()
    with pytest.raises(UpdateVerificationError, match="authenticated"):
        extract_verified_browser_archive(verified, staging)


@pytest.mark.skipif(os.name == "nt", reason="macOS framework links")
def test_upstream_macos_symlinks(tmp_path):
    import stat

    archive = tmp_path / "upstream.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("Frameworks/Versions/A/library", b"framework")
        info = zipfile.ZipInfo("Frameworks/Versions/Current")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(info, b"A")
    verified = target(archive, "darwin")
    verified.manifest["component"] = "camoufox"
    staging = tmp_path / "staging"
    create_private_directory(staging)
    package = extract_verified_browser_archive(verified, staging)
    assert (package.path / "Frameworks/Versions/Current/library").read_bytes() == b"framework"
    package.verify_tree()


def test_upstream_case_collision(tmp_path):
    archive = tmp_path / "upstream.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("camoufox.exe", b"browser")
        zipped.writestr("CAMOUFOX.exe", b"injection")
    verified = target(archive)
    verified.manifest["component"] = "camoufox"
    staging = tmp_path / "staging"
    create_private_directory(staging)
    with pytest.raises(UpdateVerificationError):
        extract_verified_browser_archive(verified, staging)


@pytest.mark.skipif(os.name != "nt", reason="Windows native protected DACLs")
def test_extracted_entries_have_private_native_acl(package):
    _, archive, staging = package
    result = extract_verified_package(target(archive), staging)
    verify_private_path(result.path)
    for candidate in result.path.rglob("*"):
        verify_private_path(candidate)


@pytest.mark.skipif(os.name != "nt", reason="Windows native protected DACLs")
def test_upstream_entries_have_private_native_acl(tmp_path):
    archive = tmp_path / "upstream.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("_internal/adjacent.dll", b"DLL")
        zipped.writestr("camoufox.exe", b"browser")
    verified = target(archive)
    verified.manifest["component"] = "camoufox"
    staging = tmp_path / "staging"
    create_private_directory(staging)
    result = extract_verified_browser_archive(verified, staging)
    verify_private_path(result.path)
    for candidate in result.path.rglob("*"):
        verify_private_path(candidate)


@pytest.mark.parametrize(
    "original, unsafe",
    [(MANIFEST, "update-package.jso\x00"), ("payload/manager.exe", "payload\\manager.exe")],
)
def test_manager_raw_member_names_rejected(package, original, unsafe):
    _, archive, staging = package
    before, after = original.encode("ascii"), unsafe.encode("ascii")
    assert len(before) == len(after)
    data = archive.read_bytes()
    assert data.count(before) == 2
    archive.write_bytes(data.replace(before, after))
    with pytest.raises(UpdateVerificationError):
        extract_verified_package(target(archive), staging)
    assert not list(staging.iterdir())
