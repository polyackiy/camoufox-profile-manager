"""Disposable Windows installer trials; never uses existing application data.

Run on an isolated CI account: NSIS updates its normal HKCU/Start menu entries.
Tests the supplied real installer (or the tiny same-script CI fixture), including
reinstall, unknown files, inaccessible/redirected payload paths and removal.
"""

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(installer: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    import winreg

    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
            raise RuntimeError("Use a disposable account without an existing manager installation")
    except FileNotFoundError:
        pass
    with tempfile.TemporaryDirectory(prefix="cpm-installer-") as temporary:
        work = Path(temporary)
        target = work / "App with spaces"
        target.mkdir()
        external = work / "unrelated"
        external.mkdir()
        sentinel = external / "keep.txt"
        sentinel.write_bytes(b"unrelated user data")
        (external / "nested").mkdir()
        redirected_owned = external / "nested" / "owned.txt"
        redirected_owned.write_bytes(b"unrelated file with a bundled name")
        unknown = target / "personal files" / "keep.txt"
        unknown.parent.mkdir()
        unknown.write_bytes(b"personal file inside selected installation folder")
        user_data = work / "user data"
        user_data.mkdir()
        (user_data / "profiles.db").write_bytes(b"synthetic data must survive")
        (user_data / ".key").write_bytes(b"synthetic key must survive")
        expected = {
            p: p.read_bytes() for p in [sentinel, redirected_owned, unknown, *user_data.iterdir()]
        }

        def install(expected_code=0):
            result = subprocess.run([str(installer), "/S", f"/D={target}"], timeout=180)
            assert result.returncode == expected_code, "Unexpected installer exit code"

        def uninstall(expected_code=0):
            # Execute a copy and suppress NSIS's asynchronous self-copy so the
            # subprocess exit status describes the actual uninstall operation.
            runner = work / "uninstall-test.exe"
            shutil.copy2(target / "Uninstall.exe", runner)
            result = subprocess.run([str(runner), "/S", f"_?={target}"], timeout=120)
            assert result.returncode == expected_code, "Unexpected uninstaller exit code"

        def preserved():
            assert all(p.read_bytes() == value for p, value in expected.items())

        try:
            install()
            assert (target / "camoufox-pm.exe").is_file()
            payload = target / "_internal"
            assert payload.is_dir(), "Fixture and real bundle require _internal"
            nested = payload / "personal.txt"
            nested.write_bytes(b"unowned file in an owned directory")
            expected[nested] = nested.read_bytes()
            install()  # Same-version reinstall must preserve added files.
            preserved()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            kernel.CreateFileW.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            locked = kernel.CreateFileW(
                str(target / "camoufox-pm.exe"), 0x80000000, 0, None, 3, 0, None
            )
            assert locked != ctypes.c_void_p(-1).value, "Could not lock fixture executable"
            try:
                uninstall(3)
                assert (target / "Uninstall.exe").is_file()
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                    pass
                preserved()
            finally:
                kernel.CloseHandle(locked)
            install()  # Restore any payload removed before reaching the lock.
            saved = target / "saved-internal"
            payload.rename(saved)
            # Junction creation works without Developer Mode/admin privileges.
            subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(payload), str(external)],
                check=True,
                capture_output=True,
            )
            try:
                install(2)
                uninstall(2)
                assert sentinel.read_bytes() == expected[sentinel]
                assert redirected_owned.read_bytes() == expected[redirected_owned]
                assert (target / "camoufox-pm.exe").is_file()
                assert (target / "Uninstall.exe").is_file()
            finally:
                payload.rmdir()  # Remove the junction itself, never its target.
                saved.rename(payload)
            uninstall()
            preserved()
            assert not (target / "camoufox-pm.exe").exists()
            assert not (target / "Uninstall.exe").exists()
            assert list(payload.iterdir()) == [nested]
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                    raise AssertionError("Uninstall registration survived removal")
            except FileNotFoundError:
                pass
            install()  # Reinstall after removal; unrelated files still survive.
            preserved()
            uninstall()
            preserved()
        finally:
            if (target / "Uninstall.exe").exists() and not (target / "saved-internal").exists():
                uninstall()
    print("Windows installer: reinstall, exact-file removal and junction refusal passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Only a sanitized result is uploaded, never files from the test account.
    report = {
        "passed": False,
        "scope": "synthetic installer lifecycle; not clean desktop acceptance",
    }
    try:
        run(args.installer.resolve())
        report["passed"] = True
    finally:
        args.output.write_text(json.dumps(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
