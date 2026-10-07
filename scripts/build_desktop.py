#!/usr/bin/env python3
"""Build the standalone desktop app, then optionally create a platform installer.

Run with ``--package`` after signing the app/binary, or ``--package-only`` when
CI already built/signed it. Python and Node are build dependencies only; the
Camoufox browser is downloaded through the first-run UI.
"""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from importlib.metadata import version as installed_version
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def architecture() -> str:
    return {"AMD64": "x86_64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())


def package_desktop() -> list[Path]:
    """Produce installers without requiring signing credentials."""
    arch = architecture()
    if sys.platform == "darwin":
        stage = ROOT / "build" / "dmg"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        shutil.copytree(DIST / "Camoufox Profile Manager.app", stage / "Camoufox Profile Manager.app", symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        output = DIST / f"camoufox-pm-macos-{arch}.dmg"
        subprocess.run(["hdiutil", "create", "-volname", "Camoufox Profile Manager", "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(output)], check=True)
        return [output]
    if sys.platform == "win32":
        makensis = shutil.which("makensis") or str(Path("C:/Program Files (x86)/NSIS/makensis.exe"))
        bootstrapper = ROOT / "build" / "MicrosoftEdgeWebview2Setup.exe"
        bootstrapper.parent.mkdir(parents=True, exist_ok=True)
        # Official Evergreen endpoint; verify its publisher before redistribution.
        urlretrieve("https://go.microsoft.com/fwlink/p/?LinkId=2124703", bootstrapper)
        # Actions runs PowerShell 7. Inheriting its PSModulePath into Windows
        # PowerShell 5 makes the built-in Security module fail to autoload.
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            raise SystemExit("PowerShell is required to verify the WebView2 bootstrapper")
        signature_env = {
            key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"
        }
        signature_env["CPM_WEBVIEW2_BOOTSTRAPPER"] = str(bootstrapper)
        subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference = 'Stop'; "
             "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); "
             "$signature = Get-AuthenticodeSignature $env:CPM_WEBVIEW2_BOOTSTRAPPER; "
             "if ($signature.Status -ne 'Valid' -or "
             "$signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') "
             "{ throw 'WebView2 bootstrapper signature is not valid Microsoft code' }"],
            env=signature_env,
            check=True,
        )
        output = DIST / f"camoufox-pm-windows-{arch}-setup.exe"
        subprocess.run([makensis, f"/DBUNDLE={DIST / 'camoufox-pm'}", f"/DOUTPUT={output}", f"/DWEBVIEW2={bootstrapper}", str(ROOT / "packaging" / "installer.nsi")], check=True)
        return [output]
    if not sys.platform.startswith("linux"):
        raise SystemExit(f"Packaging not supported on {sys.platform}")
    bundle = DIST / "camoufox-pm"
    output = DIST / f"camoufox-pm-linux-{arch}.tar.gz"
    with tarfile.open(output, "w:gz") as archive:
        archive.add(bundle, arcname="camoufox-pm")
    deb_root = ROOT / "build" / "deb"
    shutil.rmtree(deb_root, ignore_errors=True)
    shutil.copytree(bundle, deb_root / "opt" / "camoufox-pm", symlinks=True)
    bin_dir = deb_root / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "camoufox-pm").symlink_to("/opt/camoufox-pm/camoufox-pm")
    applications = deb_root / "usr" / "share" / "applications"
    applications.mkdir(parents=True)
    shutil.copy2(ROOT / "packaging" / "camoufox-pm.desktop", applications)
    icon_dir = deb_root / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icon_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "packaging" / "camoufox-pm.svg", icon_dir)
    version = installed_version("camoufox-profile-manager")
    control = deb_root / "DEBIAN"
    control.mkdir()
    deb_arch = {"x86_64": "amd64", "arm64": "arm64"}[arch]
    (control / "control").write_text(
        f"Package: camoufox-pm\nVersion: {version}\nArchitecture: {deb_arch}\n"
        "Maintainer: Camoufox Profile Manager Contributors\n"
        "Section: web\nPriority: optional\n"
        "Depends: libc6 (>= 2.35), libegl1, libgl1, libxkbcommon0, libxcb-cursor0, "
        "libnss3, libasound2, libxcomposite1, libxdamage1, libxrandr2, libxtst6, libgbm1, "
        "libgtk-3-0, libdbus-1-3, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, "
        "libxcb-render-util0, libxcb-xinerama0, libxcb-xkb1, libxkbcommon-x11-0, libx11-xcb1\n"
        "Description: Persistent Camoufox browser profile manager\n"
    )
    deb = DIST / f"camoufox-pm-linux-{arch}.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(deb_root), str(deb)], check=True)
    return [deb, output]


def write_checksums(files: list[Path]) -> None:
    for path in files:
        checksum = path.with_name(path.name + ".sha256")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        checksum.write_text(f"{digest.hexdigest()}  {path.name}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", action="store_true", help="Create an installer after building")
    parser.add_argument("--package-only", action="store_true", help="Package an existing (optionally signed) bundle")
    parser.add_argument("--checksums-only", action="store_true", help="Refresh checksums after signing/notarization")
    args = parser.parse_args()
    if args.checksums_only:
        write_checksums([p for p in DIST.iterdir() if p.is_file() and p.suffix in {".exe", ".dmg", ".deb", ".gz"}])
        return 0
    if not args.package_only:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_webui.py")], check=True)
        subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "camoufox-pm.spec"), "--noconfirm", "--clean"], cwd=ROOT, check=True)
    if args.package or args.package_only:
        files = package_desktop()
        write_checksums(files)
    print("Desktop build complete — see dist/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
