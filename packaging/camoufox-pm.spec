# PyInstaller spec for the Camoufox Profile Manager desktop app.
#
# Build (after `python scripts/build_webui.py`):
#   pyinstaller packaging/camoufox-pm.spec --noconfirm --clean
#
# Produces dist/camoufox-pm/ (a standalone bundle) and, on macOS, a .app.
# The Camoufox browser binary is NOT bundled — it is fetched at first run.
import os
import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata
from camoufox.fpgen_model import PIN, PINNED_MTIME, STAMP, ensure_fpgen_model, fpgen_data_dir, is_pinned

# Install and verify the upstream-pinned model before collect_all imports fpgen.
# A frozen app must carry this data rather than writing into its installed bundle.
ensure_fpgen_model()


def finalize_fpgen_model(directory):
    """Seal collected model metadata so read-only installs never need to stamp it.

    COLLECT and macOS BUNDLE copy bytes without preserving source timestamps.
    fpgen refreshes models older than five weeks; Camoufox avoids this with the
    pinned year-2100 mtime. Restore it on the final outputs, before installers
    and release signing. Their tar/deb/DMG/NSIS packaging preserves file times.
    """
    directory = Path(directory)
    if not is_pinned(directory):
        raise RuntimeError(f"The packaged fingerprint model is incomplete: {directory}")
    if (directory / STAMP).read_text(encoding="utf-8").strip() != PIN["sha256"]:
        raise RuntimeError(f"The packaged fingerprint model stamp is invalid: {directory}")
    for name in PIN["file_sha256"]:
        path = directory / name
        os.utime(path, (PINNED_MTIME, PINNED_MTIME))
        if int(path.stat().st_mtime) != PINNED_MTIME:
            raise RuntimeError(f"The packaged model timestamp was not preserved: {path}")


# SPECPATH is this file's directory (packaging/); ROOT is the repository root.
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "src", "camoufox_pm", "webui"), "camoufox_pm/webui")]
datas += [(os.path.join(SPECPATH, "camoufox-pm.svg"), ".")]
datas += copy_metadata("camoufox-profile-manager")
datas += copy_metadata("playwright")
# Explicitly retain the verified stamp, including when data collectors omit
# hidden files. Its bytes must be present before macOS signs the bundle.
datas += [(str(fpgen_data_dir() / STAMP), "fpgen/data")]
binaries = []
hiddenimports = collect_submodules("uvicorn")
hiddenimports += collect_submodules("keyring.backends")
datas += copy_metadata("keyring")
if sys.platform.startswith("linux"):
    hiddenimports += ["webview.platforms.qt", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore"]

# These packages ship data files (fingerprint datapoints, language tags, …) that
# PyInstaller does not pick up automatically.
for _pkg in (
    "camoufox",
    "fpgen",
    "language_tags",
    "ua_parser",
):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
if sys.platform == "darwin":
    from PyInstaller.depend.bindepend import get_imports
    sys.path.insert(0, SPECPATH)
    from openssl_bundle import select_openssl_pair
    a.binaries = select_openssl_pair(a.binaries, get_imports)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="camoufox-pm",
    icon=os.path.join(SPECPATH, "camoufox-pm.ico") if sys.platform == "win32" else None,
    # Desktop entry point redirects absent streams to the writable data folder.
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="camoufox-pm",
)
app = BUNDLE(
    coll,
    name="Camoufox Profile Manager.app",
    icon=os.path.join(SPECPATH, "camoufox-pm.icns"),
    bundle_identifier="com.github.polyackiy.camoufox-pm",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": version("camoufox-profile-manager"),
    },
)

# BUNDLE also resets metadata, so seal both final layouts after construction.
# Only timestamps change here; no resource bytes are added after ad-hoc signing.
finalize_fpgen_model(Path(coll.name) / "_internal" / "fpgen" / "data")
if sys.platform == "darwin":
    finalize_fpgen_model(Path(app.name) / "Contents" / "Resources" / "fpgen" / "data")
