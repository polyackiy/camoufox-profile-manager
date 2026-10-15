from pathlib import Path

import pytest
from scripts.windows_uninstall_manifest import literal_path, write_uninstall_manifest


@pytest.mark.parametrize(
    "name", ["$INSTDIR", "*.dll", "a?b", 'a"b', "a:b", "CON.txt", "nul", "x.", "x ", "x\ny", "x\\y"]
)
def test_rejects_nsis_injection_and_windows_aliases(name):
    with pytest.raises(ValueError):
        literal_path(Path("_internal") / name)


def test_manifest_removes_only_compiled_files_and_empty_directories(tmp_path):
    bundle = tmp_path / "bundle"
    nested = bundle / "_internal" / "nested"
    nested.mkdir(parents=True)
    (bundle / "camoufox-pm.exe").touch()
    (nested / "owned.txt").touch()
    output = tmp_path / "manifest.nsh"
    write_uninstall_manifest(bundle, output)
    text = output.read_text()
    # A file added after packaging must not become owned by the uninstaller.
    (nested / "personal.txt").touch()
    assert '!insertmacro RemoveOwnedFile "$INSTDIR\\_internal\\nested\\owned.txt"' in text
    assert "personal" not in text and "RMDir /r" not in text and "*" not in text
    assert text.index('RMDir "$INSTDIR\\_internal\\nested"') < text.index(
        'RMDir "$INSTDIR\\_internal"'
    )


def test_rejects_linked_bundle_paths(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "camoufox-pm.exe").touch()
    (bundle / "redirected").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="reparse"):
        write_uninstall_manifest(bundle, tmp_path / "manifest.nsh")


def test_rejects_reserved_uninstaller_payload(tmp_path):
    (tmp_path / "camoufox-pm.exe").touch()
    (tmp_path / "Uninstall.exe").touch()
    with pytest.raises(ValueError, match="reserved"):
        write_uninstall_manifest(tmp_path, tmp_path / "manifest.nsh")
