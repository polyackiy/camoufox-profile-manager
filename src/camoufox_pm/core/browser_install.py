"""Small adapter around Camoufox's versioned installer, with UI progress.

Readiness probes never invoke Camoufox's auto-downloading path resolver. The
upstream package owns release selection and layout; no browser files are patched.
"""

import hashlib
import json
import shutil
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel


class BrowserStatus(BaseModel):
    installed: bool
    version: str | None = None
    state: Literal["idle", "downloading", "ready", "error"] = "idle"
    progress: int | None = None
    message: str = "Download the browser to open your first profile."
    error: str | None = None


def installed_browser() -> tuple[bool, str | None]:
    """Inspect the active executable without fetching or clearing old caches."""
    try:
        from camoufox.multiversion import get_active_path
        from camoufox.pkgman import Version, launch_path

        active = get_active_path()
        if active is None:
            return False, None
        version = Version.from_path(active)
        if not version.is_supported():
            return False, None
        executable = launch_path(active)
        return Path(executable).is_file(), version.full_string
    except (ImportError, OSError, ValueError, RuntimeError):
        return False, None
    except Exception:
        # Upstream uses its own exception types for unsupported/missing builds.
        return False, None


def valid_addon(path: Path) -> bool:
    """A failed extraction can leave a missing or truncated manifest behind."""
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        return isinstance(manifest, dict) and manifest.get("manifest_version") in (2, 3)
    except (OSError, ValueError):
        return False


def browser_resources_ready() -> bool:
    """The files a normal launch otherwise downloads without a progress UI."""
    try:
        from camoufox.addons import DefaultAddons, get_addon_path
        from camoufox.geolocation import MMDB_DIR, load_geoip_config

        for addon in DefaultAddons:
            if not valid_addon(Path(get_addon_path(addon.name))):
                return False
        config = load_geoip_config()
        return all(
            (MMDB_DIR / f"{config['name'].lower()}-{kind}.mmdb").is_file()
            for kind in config["urls"]
        )
    except (ImportError, OSError, ValueError, KeyError):
        return False


def ensure_browser_ready() -> None:
    from .browser_session import BrowserLaunchError

    if not installed_browser()[0] or not browser_resources_ready():
        raise BrowserLaunchError(
            "The browser needs setup. Open Settings and click Install browser before launching a profile."
        )


@contextmanager
def installation_lock():
    """Serialize downloads across manager processes, released on process exit."""
    from camoufox.pkgman import INSTALL_DIR

    lock_path = INSTALL_DIR.parent / ".camoufox-pm-install.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(
                    "Another application is installing Camoufox. Try again later."
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(
                    "Another application is installing Camoufox. Try again later."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class BrowserInstaller:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = BrowserStatus(installed=False)
        self._thread: threading.Thread | None = None

    def status(self) -> BrowserStatus:
        installed, version = installed_browser()
        installed = installed and browser_resources_ready()
        with self._lock:
            result = self._state.model_copy(update={"installed": installed, "version": version})
        if result.state not in ("downloading", "error"):
            result.state = "ready" if installed else "idle"
            result.message = (
                "Browser is ready."
                if installed
                else "Download the browser to open your first profile."
            )
        return result

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._state = self._state.model_copy(update=values)

    def start(self) -> BrowserStatus:
        with self._lock:
            if self._state.state != "downloading":
                self._state = BrowserStatus(
                    installed=False,
                    state="downloading",
                    progress=None,
                    message="Finding a compatible browser…",
                )
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="browser-download"
                )
                self._thread.start()
        return self.status()

    def _run(self) -> None:
        try:
            with installation_lock():
                self._install()
            installed, version = installed_browser()
            if not installed or not browser_resources_ready():
                raise RuntimeError(
                    "The browser download finished but required browser files are missing. Try again."
                )
            self._update(
                installed=True,
                version=version,
                state="ready",
                progress=100,
                message="Browser is ready.",
                error=None,
            )
        except Exception as exc:
            self._update(
                state="error",
                progress=None,
                message="Could not finish installing the browser. Check your connection and retry.",
                error=str(exc),
            )

    def _install(self) -> None:
        from camoufox.addons import DefaultAddons, get_addon_path, maybe_download_addons
        from camoufox.geolocation import download_mmdb
        from camoufox.pkgman import CamoufoxFetcher

        owner = self

        class ProgressFetcher(CamoufoxFetcher):
            def download_file(self, file, url):
                digest = hashlib.sha256()
                count = 0
                with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", 0))
                    for chunk in response.iter_bytes():
                        file.write(chunk)
                        digest.update(chunk)
                        count += len(chunk)
                        owner._update(
                            progress=min(95, int(count / total * 95)) if total else None,
                            message="Downloading Camoufox…",
                        )
                expected = self.installed_sha256
                if expected and digest.hexdigest() != expected:
                    raise RuntimeError("Browser download checksum did not match. Please retry.")
                owner._update(progress=96, message="Unpacking the browser…")
                file.seek(0)
                return file

        if not installed_browser()[0]:
            fetcher = ProgressFetcher()
            # Upstream skips a target carrying version.json even if its executable
            # is missing. Retry only an incomplete target, never a healthy build.
            from camoufox.multiversion import BROWSERS_DIR, get_repo_name, version_folder_name
            from camoufox.pkgman import launch_path

            target = (
                BROWSERS_DIR
                / get_repo_name(fetcher.github_repo)
                / version_folder_name(fetcher.version, fetcher.build, fetcher.installed_sha8)
            )
            replace = False
            if target.exists():
                try:
                    launch_path(target)
                except Exception:
                    replace = True
            fetcher.install(replace=replace)
        self._update(progress=97, message="Preparing location data and browser add-ons…")
        if not browser_resources_ready():
            download_mmdb()
        # Upstream treats an existing empty directory as a successful install.
        # Preserve incomplete contents for diagnosis and let it retry cleanly.
        for addon in DefaultAddons:
            addon_path = Path(get_addon_path(addon.name))
            if addon_path.exists() and not valid_addon(addon_path):
                if addon_path.is_symlink():
                    raise RuntimeError(
                        "Browser add-on cache is a symlink; repair it manually before retrying."
                    )
                failed = addon_path.with_name(f"{addon_path.name}.incomplete")
                if failed.exists():
                    shutil.rmtree(failed)
                addon_path.rename(failed)
        maybe_download_addons(list(DefaultAddons))
        if not browser_resources_ready():
            raise RuntimeError(
                "A browser add-on or location database could not be downloaded. Please retry."
            )


browser_installer = BrowserInstaller()
