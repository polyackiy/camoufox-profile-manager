# PyInstaller spec for the Camoufox Profile Manager desktop app.
#
# Build (after `python scripts/build_webui.py`):
#   pyinstaller packaging/camoufox-pm.spec --noconfirm --clean
#
# Produces dist/camoufox-pm/ (a standalone bundle) and, on macOS, a .app.
# The Camoufox browser binary is NOT bundled — it is fetched at first run.
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is this file's directory (packaging/); ROOT is the repository root.
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "src", "camoufox_pm", "webui"), "camoufox_pm/webui")]
binaries = []
hiddenimports = collect_submodules("uvicorn")

# These packages ship data files (fingerprint datapoints, language tags, …) that
# PyInstaller does not pick up automatically.
for _pkg in (
    "camoufox",
    "browserforge",
    "apify_fingerprint_datapoints",
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
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="camoufox-pm",
    console=True,
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
    bundle_identifier="com.github.polyackiy.camoufox-pm",
)
