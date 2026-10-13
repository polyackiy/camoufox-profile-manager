# Small native packaging proof; no production synchronization entry point.
import sys
from pathlib import Path

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "packaging"))
from openssl_bundle import select_openssl_pair

a = Analysis([str(root / "scripts" / "smoke_sync_crypto.py")],
             pathex=[str(root / "src")], binaries=[], datas=[], hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
if sys.platform == "darwin":
    from PyInstaller.depend.bindepend import get_imports
    a.binaries = select_openssl_pair(a.binaries, get_imports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="sync-crypto-smoke", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="sync-crypto-smoke")
