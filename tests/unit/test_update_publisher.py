import plistlib
import struct
import sys

import pytest

from camoufox_pm.core import update_publisher as publisher


def test_absent_policy_fails_closed(tmp_path):
    with pytest.raises(publisher.PublisherVerificationError, match="No trusted"):
        publisher.verify_publisher(tmp_path, {}, None)


@pytest.fixture
def mac_bundle(tmp_path):
    (tmp_path / "Contents/MacOS").mkdir(parents=True)
    (tmp_path / "Contents/MacOS/app").write_bytes(b"not executed")
    (tmp_path / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "test.bundle", "CFBundleExecutable": "app"})
    )
    return tmp_path


@pytest.mark.parametrize(
    "failure", [None, "team", "identifier", "adhoc", "notarization", "architecture", "command"]
)
def test_mac_os_policy(mac_bundle, monkeypatch, failure):
    monkeypatch.setattr(publisher.sys, "platform", "darwin")
    calls = []

    def run(command):
        calls.append(command)
        if failure == "command":
            raise publisher.PublisherVerificationError("OS rejected")
        if "--display" in command:
            return (
                f"TeamIdentifier={'WRONGTEAM0' if failure == 'team' else 'ABCDEFGHIJ'}\n"
                f"Identifier={'other' if failure == 'identifier' else 'test.bundle'}\n"
                + (
                    "Signature=adhoc\n"
                    if failure == "adhoc"
                    else "Authority=Developer ID Application: Test\n"
                )
            )
        if "spctl" in command[0]:
            return (
                "source=Unnotarized Developer ID"
                if failure == "notarization"
                else "source=Notarized Developer ID"
            )
        if "lipo" in command[0]:
            return "x86_64" if failure == "architecture" else "arm64"
        return ""

    monkeypatch.setattr(publisher, "_run", run)
    policy = publisher.PublisherPolicy("mac-policy", "test.bundle", "ABCDEFGHIJ")
    manifest = {
        "publisher_policy": "mac-policy",
        "product_id": "test.bundle",
        "platform": "darwin",
        "architecture": "arm64",
    }
    if failure:
        with pytest.raises(publisher.PublisherVerificationError):
            publisher.verify_publisher(mac_bundle, manifest, policy)
    else:
        publisher.verify_publisher(mac_bundle, manifest, policy)
        assert "--strict" in calls[0] and "--deep" in calls[0]


@pytest.fixture
def pe_file(tmp_path):
    header = bytearray(64)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    executable = tmp_path / "app.exe"
    executable.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0x8664))
    return executable


def test_pe_architecture(pe_file):
    assert publisher.pe_architecture(pe_file) == "x86_64"
    pe_file.write_bytes(b"not PE")
    with pytest.raises(publisher.PublisherVerificationError):
        publisher.pe_architecture(pe_file)


@pytest.mark.parametrize("failure", [None, "untrusted", "certificate", "product"])
def test_windows_os_policy(pe_file, monkeypatch, failure):
    import json

    monkeypatch.setattr(publisher.sys, "platform", "win32")
    result = {
        "status": "NotTrusted" if failure == "untrusted" else "Valid",
        "thumbprint": "B" * 40 if failure == "certificate" else "A" * 40,
        "product": "other" if failure == "product" else "Test Product",
    }
    monkeypatch.setattr(publisher, "_run", lambda command: json.dumps(result))
    policy = publisher.PublisherPolicy(
        "win-policy", "Test Product", windows_thumbprints=("A" * 40,), windows_executable="app.exe"
    )
    manifest = {
        "publisher_policy": "win-policy",
        "product_id": "Test Product",
        "platform": "windows",
        "architecture": "x86_64",
    }
    if failure:
        with pytest.raises(publisher.PublisherVerificationError):
            publisher.verify_publisher(pe_file.parent, manifest, policy)
    else:
        publisher.verify_publisher(pe_file.parent, manifest, policy)


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="native OS trust smoke")
def test_native_unsigned_package_rejected(tmp_path):
    if sys.platform == "darwin":
        (tmp_path / "Contents/MacOS").mkdir(parents=True)
        (tmp_path / "Contents/MacOS/app").write_bytes(b"unsigned")
        (tmp_path / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "test.bundle", "CFBundleExecutable": "app"})
        )
        policy = publisher.PublisherPolicy("native", "test.bundle", "ABCDEFGHIJ")
        manifest = {
            "publisher_policy": "native",
            "product_id": "test.bundle",
            "platform": "darwin",
            "architecture": "arm64",
        }
    else:
        executable = tmp_path / "app.exe"
        header = bytearray(64)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 60, 64)
        executable.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0x8664))
        policy = publisher.PublisherPolicy(
            "native", "test", windows_thumbprints=("A" * 40,), windows_executable="app.exe"
        )
        manifest = {
            "publisher_policy": "native",
            "product_id": "test",
            "platform": "windows",
            "architecture": "x86_64",
        }
    with pytest.raises(publisher.PublisherVerificationError):
        publisher.verify_publisher(tmp_path, manifest, policy)
