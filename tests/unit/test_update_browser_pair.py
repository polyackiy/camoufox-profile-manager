import hashlib
import json
import zipfile

import pytest

from camoufox_pm.core.update_browser_pair import install_paired_browser
from camoufox_pm.core.update_targets import UpdateVerificationError, VerifiedTarget


def browser(tmp_path):
    archive = tmp_path / "browser.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("version.json", json.dumps({"version": "135", "build": "1"}))
        zipped.writestr("camoufox.exe", b"fake executable")
    return VerifiedTarget(
        archive,
        {
            "component": "camoufox",
            "platform": "windows",
            "version": "135-1",
            "browser_version": "135-1",
        },
        archive.stat().st_size,
        {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
    )


def test_authenticated_browser_installed_without_activation_and_verified_on_retry(tmp_path):
    target = browser(tmp_path)
    root = tmp_path / "browser-root"
    installed = install_paired_browser(target, root, offline_check=lambda: True)
    assert (installed / "camoufox.exe").read_bytes() == b"fake executable"
    assert not (root / "config.json").exists()
    assert install_paired_browser(target, root, offline_check=lambda: True) == installed
    (installed / "camoufox.exe").write_bytes(b"changed executable")
    with pytest.raises(UpdateVerificationError):
        install_paired_browser(target, root, offline_check=lambda: True)


def test_added_browser_code_rejected_on_retry(tmp_path):
    target = browser(tmp_path)
    root = tmp_path / "browser-root"
    installed = install_paired_browser(target, root, offline_check=lambda: True)
    (installed / "injected.dll").write_bytes(b"bad")
    with pytest.raises(UpdateVerificationError):
        install_paired_browser(target, root, offline_check=lambda: True)


def test_browser_install_refuses_without_offline_lock(tmp_path):
    with pytest.raises(UpdateVerificationError, match="offline"):
        install_paired_browser(browser(tmp_path), tmp_path / "root", offline_check=lambda: False)


def test_pair_staging_uses_native_private_creation(tmp_path, monkeypatch):
    from camoufox_pm.core import update_browser_pair
    from camoufox_pm.core.update_permissions import create_private_directory, verify_private_path

    created = []

    def private(path):
        create_private_directory(path)
        verify_private_path(path)
        created.append(path)

    monkeypatch.setattr(update_browser_pair, "create_private_directory", private)
    install_paired_browser(browser(tmp_path), tmp_path / "root", offline_check=lambda: True)
    assert len(created) == 1
    assert created[0].name.startswith(".staging-")
