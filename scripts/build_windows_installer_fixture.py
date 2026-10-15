"""Build a tiny fixture with the production NSIS script for fast regression CI."""

import shutil
import subprocess
from pathlib import Path

from windows_uninstall_manifest import write_uninstall_manifest

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = ROOT / "build" / "installer-fixture"
    bundle = work / "bundle"
    (bundle / "_internal" / "nested").mkdir(parents=True, exist_ok=True)
    # A valid stub executable stands in only for app/runtime payload. The
    # production NSIS install, guards and uninstall instructions are unchanged.
    stub = work / "stub.nsi"
    exe = work / "stub.exe"
    stub.write_text(
        f'Unicode True\nOutFile "{exe}"\nRequestExecutionLevel user\nSilentInstall silent\nSection\nSetErrorLevel 0\nSectionEnd\n'
    )
    compiler = shutil.which("makensis") or r"C:\Program Files (x86)\NSIS\makensis.exe"
    subprocess.run([compiler, str(stub)], check=True)
    shutil.copy2(exe, bundle / "camoufox-pm.exe")
    (bundle / "_internal" / "nested" / "owned.txt").write_text("owned payload")
    manifest = work / "uninstall.nsh"
    write_uninstall_manifest(bundle, manifest)
    subprocess.run(
        [
            compiler,
            f"/DBUNDLE={bundle}",
            f"/DOUTPUT={work / 'setup.exe'}",
            f"/DWEBVIEW2={exe}",
            f"/DUNINSTALL_MANIFEST={manifest}",
            f"/DICON={ROOT / 'packaging' / 'camoufox-pm.ico'}",
            str(ROOT / "packaging" / "installer.nsi"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
