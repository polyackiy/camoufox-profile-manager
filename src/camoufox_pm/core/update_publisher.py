"""OS trust checks for extracted update packages; never executes their code."""

from __future__ import annotations

import base64
import json
import os
import plistlib
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class PublisherVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublisherPolicy:
    identifier: str
    product_id: str
    macos_team_id: str | None = None
    windows_thumbprints: tuple[str, ...] = ()
    windows_executable: str | None = None


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublisherVerificationError("OS publisher verification failed") from exc
    return result.stdout + result.stderr


def _safe_file(root: Path, relative: str) -> Path:
    candidate = root / relative
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise PublisherVerificationError("Unsafe executable path")
    for component in (candidate, *candidate.parents):
        if component.is_symlink():
            raise PublisherVerificationError("Executable path contains symbolic links")
        if component.exists() and getattr(component.stat(), "st_file_attributes", 0) & 0x400:
            raise PublisherVerificationError("Executable path contains a reparse point")
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root.resolve()):
        raise PublisherVerificationError("Package executable is missing")
    return candidate


def pe_architecture(executable: Path) -> str:
    try:
        with executable.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise ValueError("Not PE")
            offset = struct.unpack_from("<I", header, 60)[0]
            if offset > 1024 * 1024:
                raise ValueError("Invalid PE offset")
            stream.seek(offset)
            pe = stream.read(6)
            if len(pe) != 6 or pe[:4] != b"PE\0\0":
                raise ValueError("Invalid PE header")
            return {0x8664: "x86_64", 0xAA64: "arm64"}[struct.unpack_from("<H", pe, 4)[0]]
    except (OSError, ValueError, KeyError) as exc:
        raise PublisherVerificationError("Unsupported executable architecture") from exc


def verify_publisher(package: Path, manifest: dict, policy: PublisherPolicy | None) -> None:
    """Use only local deployment policy and previously TUF-authenticated manifest."""
    if policy is None or not policy.identifier or not policy.product_id:
        raise PublisherVerificationError("No trusted publisher policy is configured")
    if (
        manifest.get("publisher_policy") != policy.identifier
        or manifest.get("product_id") != policy.product_id
    ):
        raise PublisherVerificationError("Signed publisher policy does not match deployment")
    package = Path(package).absolute()
    if sys.platform == "darwin" and manifest.get("platform") == "darwin":
        if not policy.macos_team_id or not re.fullmatch(r"[A-Z0-9]{10}", policy.macos_team_id):
            raise PublisherVerificationError("A Developer ID Team ID is required")
        info = _safe_file(package, "Contents/Info.plist")
        try:
            plist = plistlib.loads(info.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            raise PublisherVerificationError("Invalid app bundle metadata") from exc
        if plist.get("CFBundleIdentifier") != policy.product_id:
            raise PublisherVerificationError("App bundle identifier does not match")
        executable_name = plist.get("CFBundleExecutable")
        if (
            not isinstance(executable_name, str)
            or "/" in executable_name
            or "\\" in executable_name
        ):
            raise PublisherVerificationError("Invalid bundle executable")
        executable = _safe_file(package, "Contents/MacOS/" + executable_name)
        _run(
            [
                "/usr/bin/codesign",
                "--verify",
                "--deep",
                "--strict",
                "--all-architectures",
                str(package),
            ]
        )
        details = _run(["/usr/bin/codesign", "--display", "--verbose=4", str(package)])
        fields = dict(line.split("=", 1) for line in details.splitlines() if "=" in line)
        if (
            fields.get("TeamIdentifier") != policy.macos_team_id
            or fields.get("Identifier") != policy.product_id
        ):
            raise PublisherVerificationError(
                "Code signature publisher or identifier does not match"
            )
        if not any(
            line.startswith("Authority=Developer ID Application:") for line in details.splitlines()
        ):
            raise PublisherVerificationError("Developer ID Application signature required")
        assessment = _run(
            ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=2", str(package)]
        )
        if "source=Notarized Developer ID" not in assessment.splitlines():
            raise PublisherVerificationError("Notarized Developer ID assessment required")
        arches = _run(["/usr/bin/lipo", "-archs", str(executable)]).strip().split()
        expected = manifest.get("architecture")
        if expected not in {"arm64", "x86_64"} or expected not in arches:
            raise PublisherVerificationError("Executable architecture does not match signed target")
    elif sys.platform == "win32" and manifest.get("platform") == "windows":
        if not policy.windows_executable or not policy.windows_thumbprints:
            raise PublisherVerificationError(
                "Windows executable and trusted certificate fingerprints required"
            )
        fingerprints = {value.upper() for value in policy.windows_thumbprints}
        if any(not re.fullmatch(r"[0-9A-F]{40}", value) for value in fingerprints):
            raise PublisherVerificationError("Invalid trusted certificate thumbprint")
        executable = _safe_file(package, policy.windows_executable)
        if pe_architecture(executable) != manifest.get("architecture"):
            raise PublisherVerificationError("Executable architecture does not match signed target")
        literal = str(executable).replace("'", "''")
        script = (
            "$ErrorActionPreference='Stop'; "
            f"$p='{literal}'; $s=Get-AuthenticodeSignature -LiteralPath $p; "
            "$v=[System.Diagnostics.FileVersionInfo]::GetVersionInfo($p); "
            "@{status=$s.Status.ToString(); thumbprint=$s.SignerCertificate.Thumbprint; "
            "product=$v.ProductName} | ConvertTo-Json -Compress"
        )
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        powershell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
        output = _run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16le")).decode("ascii"),
            ]
        )
        try:
            result = json.loads(output)
            if (
                result["status"] != "Valid"
                or result["thumbprint"].upper() not in fingerprints
                or result["product"] != policy.product_id
            ):
                raise PublisherVerificationError(
                    "Authenticode trust, certificate or product mismatch"
                )
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise PublisherVerificationError("Invalid Authenticode verification result") from exc
    else:
        raise PublisherVerificationError("Publisher verification is unsupported on this platform")
