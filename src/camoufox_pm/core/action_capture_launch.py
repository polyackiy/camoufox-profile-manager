"""Native-host registration and explicit transient add-on preparation."""

from __future__ import annotations

import json
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .update_permissions import create_private_directory, create_private_file, reject_reparse_paths

EXTENSION_FILES = Path(__file__).with_name("action_capture_extension")


def _check_registration_parents(existing: Path) -> None:
    if os.name != "posix":
        return  # Windows manifests and all their new directories use private DACLs.
    for parent in (existing, *existing.parents):
        info = parent.stat()
        # A root/user-owned sticky parent such as /tmp protects its child
        # names from other writers. The actual registration directory must
        # itself be owner-writable only, so no one can insert a manifest.
        shared = info.st_mode & 0o022
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in (0, os.geteuid())
            or (shared and (parent == existing or not info.st_mode & stat.S_ISVTX))
        ):
            raise PermissionError("Native-host registration has an unsafe parent")


@contextmanager
def native_registration(root: Path, host: str, addon_id: str, executable: Path):
    """Register only a new random host; never replace another install's manifest."""
    # Firefox on macOS resolves its real per-user native-host directory even
    # when HOME is overridden. Register one random name there explicitly, and
    # remove only our own identical manifest. The caller owns the prepared launch.
    home = Path.home().resolve()
    if sys.platform == "darwin":
        directory = home / "Library/Application Support/Mozilla/NativeMessagingHosts"
    elif sys.platform == "win32":
        directory = root / "native-manifests"
    else:
        directory = home / ".mozilla/native-messaging-hosts"
    reject_reparse_paths(directory)
    created = []
    missing = directory
    while not missing.exists():
        created.append(missing)
        missing = missing.parent
    _check_registration_parents(missing)
    manifest = directory / (host + ".json")
    payload = json.dumps(
        {
            "name": host,
            "description": "Camoufox Profile Manager action capture",
            "path": str(executable),
            "type": "stdio",
            "allowed_extensions": [addon_id],
        }
    )
    registry_path = rf"Software\Mozilla\NativeMessagingHosts\{host}"
    registered = False
    owned_manifest = False
    owned_directories = []
    try:
        for new_directory in reversed(created):
            create_private_directory(new_directory)
            owned_directories.append(new_directory)
        handle = create_private_file(manifest)
        owned_manifest = True
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        if sys.platform == "win32":
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path):  # type: ignore[attr-defined]
                    raise RuntimeError("Native-host name is already registered")
            except FileNotFoundError:
                pass
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:  # type: ignore[attr-defined]
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))  # type: ignore[attr-defined]
            registered = True
        yield home
    finally:
        if registered:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:  # type: ignore[attr-defined]
                if winreg.QueryValueEx(key, "")[0] != str(manifest):  # type: ignore[attr-defined]
                    raise RuntimeError("Native-host registration changed during the probe")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)  # type: ignore[attr-defined]
        if owned_manifest and manifest.exists() and manifest.read_text(encoding="utf-8") == payload:
            manifest.unlink()
        for path in reversed(owned_directories):
            try:
                path.rmdir()
            except OSError:
                break


def prepare_capture_addon(
    root: Path, *, origin: str, generation: str, host: str, addon_id: str
) -> Path:
    addon = root / "addon"
    addon.mkdir()
    parsed = urlsplit(origin)
    pattern = f"{parsed.scheme}://{parsed.hostname}/*"
    manifest = {
        "manifest_version": 2,
        "name": "Camoufox Profile Manager action capture",
        "version": "0.1",
        "browser_specific_settings": {"gecko": {"id": addon_id}},
        "permissions": ["nativeMessaging", "tabs", pattern],
        "background": {"scripts": ["config.js", "background.js"]},
        "content_scripts": [
            {
                "matches": [pattern],
                "js": ["content.js"],
                "run_at": "document_idle",
                "all_frames": False,
            }
        ],
    }
    (addon / "manifest.json").write_text(json.dumps(manifest))
    (addon / "config.js").write_text(
        "const CPM_CAPTURE_CONFIG = "
        + json.dumps({"host": host, "generation": generation, "origin": origin})
        + ";"
    )
    for name in ("background.js", "content.js"):
        (addon / name).write_bytes((EXTENSION_FILES / name).read_bytes())
    return addon
