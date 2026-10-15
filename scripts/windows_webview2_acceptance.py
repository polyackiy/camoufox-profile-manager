"""Execute production WebView2 prerequisite logic with isolated registry fixtures.

No real Edge/WebView2 keys, downloads or runtime installation are touched. These
fault tests do not replace real ordinary-user/WebView2-absent desktop trials.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run():
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    import winreg

    base = rf"Software\CamoufoxPMPrerequisiteTests\{uuid.uuid4()}"
    machine, user = base + r"\machine", base + r"\user"
    view = winreg.KEY_WOW64_32KEY
    compiler = shutil.which("makensis") or r"C:\Program Files (x86)\NSIS\makensis.exe"
    results = []

    def clear_versions():
        for path in (machine, user):
            try:
                winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, path, view)
            except FileNotFoundError:
                pass

    def version(path, value):
        if value is not None:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE | view
            ) as key:
                winreg.SetValueEx(key, "pv", 0, winreg.REG_SZ, value)

    try:
        with tempfile.TemporaryDirectory(prefix="cpm-webview2-") as directory:
            work = Path(directory)
            marker, called = work / "manager.txt", work / "bootstrap.txt"
            installers = {}
            for mode in ("no-runtime", "failure", "register", "register-failure", "cannot-launch"):
                bootstrap = work / f"bootstrap-{mode}.exe"
                if mode == "cannot-launch":
                    bootstrap.write_bytes(b"Not a Windows executable")
                else:
                    source = work / f"bootstrap-{mode}.nsi"
                    source.write_text(
                        f'Unicode True\nOutFile "{bootstrap}"\nRequestExecutionLevel user\n'
                        "SilentInstall silent\nSection\nSetRegView 32\n"
                        f'FileOpen $0 "{called}" w\nFileWrite $0 "called"\nFileClose $0\n'
                        + (
                            f'WriteRegStr HKCU "{user}" "pv" "153.0.1.2"\n'
                            if mode.startswith("register")
                            else ""
                        )
                        + f"SetErrorLevel {5 if 'failure' in mode else 0}\nSectionEnd\n",
                        encoding="utf-8",
                    )
                    subprocess.run([compiler, str(source)], check=True, capture_output=True)
                output = work / f"setup-{mode}.exe"
                source = work / f"setup-{mode}.nsi"
                source.write_text(
                    f'Unicode True\nOutFile "{output}"\nRequestExecutionLevel user\n'
                    "SilentInstall silent\n"
                    f'!include "{ROOT / "packaging" / "webview2.nsh"}"\n'
                    f'!insertmacro WebView2Prerequisite HKCU "{machine}" "{user}" "{bootstrap}"\n'
                    "Section\nCall CheckWebView2\n"
                    f'FileOpen $0 "{marker}" w\nFileWrite $0 "updated"\nFileClose $0\nSectionEnd\n',
                    encoding="utf-8",
                )
                subprocess.run([compiler, str(source)], check=True, capture_output=True)
                installers[mode] = output

            def trial(
                name,
                mode="failure",
                machine_value=None,
                user_value=None,
                ready=False,
                bootstrap=True,
            ):
                clear_versions()
                version(machine, machine_value)
                version(user, user_value)
                marker.write_text("existing manager data", encoding="utf-8")
                called.unlink(missing_ok=True)
                result = subprocess.run([str(installers[mode]), "/S"], timeout=30)
                assert result.returncode == (0 if ready else 4), f"Unexpected exit status: {name}"
                assert marker.read_text() == ("updated" if ready else "existing manager data"), name
                assert called.exists() == bootstrap, name
                results.append(name)

            trial("missing-runtime-failure")
            trial("zero-exit-without-runtime", "no-runtime")
            trial("bootstrap-cannot-launch", "cannot-launch", bootstrap=False)
            trial("install-registers-runtime", "register", ready=True)
            trial("nonzero-exit-despite-registration", "register-failure")
            # Same executable can be run again after the prerequisite is fixed.
            marker.write_text("existing manager data")
            called.unlink()
            result = subprocess.run([str(installers["register-failure"]), "/S"], timeout=30)
            assert (
                result.returncode == 0 and marker.read_text() == "updated" and not called.exists()
            )
            results.append("retry-after-runtime-becomes-available")
            trial("machine-runtime", machine_value="153.0.1.2", ready=True, bootstrap=False)
            trial("user-runtime", user_value="153.0.1.2", ready=True, bootstrap=False)
            trial(
                "bad-machine-valid-user",
                machine_value="invalid",
                user_value="153.0.1.2",
                ready=True,
                bootstrap=False,
            )
            trial(
                "valid-machine-bad-user",
                machine_value="153.0.1.2",
                user_value="invalid",
                ready=True,
                bootstrap=False,
            )
            for index, value in enumerate(
                [
                    "",
                    "0.0.0.0",
                    "00.00.00.00",
                    "invalid",
                    "1.2.3",
                    "1..2.3",
                    "1.2.3.",
                    "1.2.3.4.5",
                    "-1.2.3.4",
                    "1.2.3.x",
                    "1.2.3.4 ",
                    "123456.0.0.0",
                ]
            ):
                trial(f"malformed-or-zero-version-{index}", user_value=value)
            trial("lowest-positive-version", user_value="0.0.0.1", ready=True, bootstrap=False)
    finally:
        clear_versions()
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, base, view)
        except FileNotFoundError:
            pass
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "passed": False,
        "scope": "isolated prerequisite fault fixtures; no actual runtime installation",
    }
    try:
        report["scenarios"] = run()
        report["passed"] = True
        print(f"WebView2 prerequisite: {len(report['scenarios'])} scenarios passed")
    finally:
        args.output.write_text(json.dumps(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
