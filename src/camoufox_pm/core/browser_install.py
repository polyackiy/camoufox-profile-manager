"""Small adapter around Camoufox's versioned installer, with UI progress.

Readiness probes never invoke Camoufox's auto-downloading path resolver. The
upstream package owns release selection and layout; no browser files are patched.
"""

import hashlib
import json
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, SecretStr

if TYPE_CHECKING:
    from camoufox.pkgman import CamoufoxFetcher

GITHUB_RATE_LIMIT = (
    "GitHub's download request limit was reached. Wait and retry, or use a GitHub token below."
)
GITHUB_TOKEN_REJECTED = "GitHub rejected the token. Check that it is valid and has not expired."
GITHUB_FORBIDDEN = "GitHub refused the request. Check token access or wait before retrying."
GITHUB_UNAVAILABLE = "Could not read browser releases from GitHub. Check your connection and retry."
SETUP_FAILED = "Could not finish installing the browser. Check your connection and retry."
EXPLICIT_BROWSER_SETUP = (
    "The explicitly selected browser is missing or incompatible. Repair that selection "
    "with the Camoufox CLI, or select the paired build with camoufox set --release and retry."
)


class BrowserSelectionError(RuntimeError):
    """Safe setup guidance for an explicitly selected upstream build."""


class GitHubDownloadError(RuntimeError):
    """Fixed public messages; never include upstream headers, bodies or credentials."""


def github_releases(repository: str, token: SecretStr | None = None) -> list[dict]:
    """Authenticate release discovery only, without modifying upstream global state."""
    from camoufox.pkgman import GITHUB_TOKEN

    credential = token.get_secret_value() if token is not None else GITHUB_TOKEN
    headers = {"Accept": "application/vnd.github+json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    url = httpx.URL(f"https://api.github.com/repos/{repository}/releases")
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            for _ in range(5):
                # Follow renamed repositories without forwarding a token off-origin.
                if (
                    url.scheme != "https"
                    or url.host != "api.github.com"
                    or url.port not in (None, 443)
                    or url.userinfo
                ):
                    raise GitHubDownloadError(GITHUB_UNAVAILABLE)
                response = client.get(url, headers=headers)
                if response.is_redirect:
                    url = response.url.join(response.headers.get("location", ""))
                    continue
                if response.status_code == 401:
                    raise GitHubDownloadError(GITHUB_TOKEN_REJECTED)
                if response.status_code in (403, 429):
                    limited = (
                        response.status_code == 429
                        or response.headers.get("x-ratelimit-remaining") == "0"
                        or "retry-after" in response.headers
                    )
                    # Secondary limits may supply only the documented message.
                    if not limited:
                        try:
                            message = response.json().get("message", "")
                            limited = isinstance(message, str) and "rate limit" in message.lower()
                        except (ValueError, AttributeError):
                            pass
                    raise GitHubDownloadError(GITHUB_RATE_LIMIT if limited else GITHUB_FORBIDDEN)
                response.raise_for_status()
                releases = response.json()
                if not isinstance(releases, list):
                    raise GitHubDownloadError(GITHUB_UNAVAILABLE)
                return releases
    except (httpx.HTTPError, ValueError):
        raise GitHubDownloadError(GITHUB_UNAVAILABLE) from None
    raise GitHubDownloadError(GITHUB_UNAVAILABLE)


class BrowserStatus(BaseModel):
    installed: bool
    version: str | None = None
    state: Literal["idle", "downloading", "ready", "error"] = "idle"
    progress: int | None = None
    message: str = "Download the browser to open your first profile."
    error: str | None = None


def installed_browser() -> tuple[bool, str | None]:
    """Inspect the active browser bundle without fetching or clearing old caches."""
    try:
        from camoufox.multiversion import get_active_path

        active = get_active_path()
        if active is None:
            return False, None
        version = _compatible_version(active)
        return version is not None and _complete_bundle(active), version
    except Exception:
        # Upstream uses its own exception types for unsupported/missing builds.
        return False, None


def _compatible_version(build: Path) -> str | None:
    from camoufox.pkgman import Version, installed_interface, interface_supported

    try:
        version_file = build / "version.json"
        if not version_file.resolve(strict=True).is_relative_to(build.resolve(strict=True)):
            return None
        version = Version.from_path(build)
        if version.is_supported() and interface_supported(installed_interface(build)):
            return version.full_string
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        pass
    return None


def _complete_bundle(build: Path) -> bool:
    """Check the executable and add-ons from this version's own archive."""
    from camoufox.addons import DefaultAddons, get_addon_path
    from camoufox.pkgman import launch_path

    try:
        root = build.resolve(strict=True)
        executable = Path(launch_path(build))
        if not executable.is_file() or not executable.resolve(strict=True).is_relative_to(root):
            return False
        for addon in DefaultAddons:
            path = Path(get_addon_path(addon.name, executable)) / "manifest.json"
            if not path.is_file() or not path.resolve(strict=True).is_relative_to(root):
                return False
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("manifest_version") not in (2, 3):
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def browser_resources_ready() -> bool:
    """The files a normal launch otherwise downloads without a progress UI."""
    try:
        from camoufox.fpgen_model import is_pinned
        from camoufox.geolocation import MMDB_DIR, load_geoip_config

        config = load_geoip_config()
        return is_pinned() and all(
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

    def start(self, github_token: SecretStr | None = None) -> BrowserStatus:
        with self._lock:
            if self._state.state != "downloading":
                self._state = BrowserStatus(
                    installed=False,
                    state="downloading",
                    progress=None,
                    message="Finding a compatible browser…",
                )
                self._thread = threading.Thread(
                    target=self._run,
                    kwargs={"github_token": github_token} if github_token is not None else {},
                    daemon=True,
                    name="browser-download",
                )
                self._thread.start()
        return self.status()

    def _run(self, github_token: SecretStr | None = None) -> None:
        try:
            from .maintenance import configured_gate

            with configured_gate().operation(), installation_lock():
                if github_token is None:
                    self._install()
                else:
                    self._install(github_token)
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
                message=SETUP_FAILED,
                error=str(exc)
                if github_token is None
                or isinstance(exc, (GitHubDownloadError, BrowserSelectionError))
                else SETUP_FAILED,
            )

    def _install(self, github_token: SecretStr | None = None) -> None:
        from camoufox.fpgen_model import ensure_fpgen_model
        from camoufox.geolocation import download_mmdb
        from camoufox.pkgman import CamoufoxFetcher

        owner = self

        class ProgressFetcher(CamoufoxFetcher):
            def _get_releases(self, github_repo):
                return github_releases(github_repo, github_token)

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
            from camoufox.browser_pin import is_explicit_choice
            from camoufox.multiversion import load_config

            if is_explicit_choice(load_config()):
                raise BrowserSelectionError(EXPLICIT_BROWSER_SETUP)
            _install_paired_bundle(ProgressFetcher())
        self._update(progress=97, message="Preparing location data and the fingerprint model…")
        ensure_fpgen_model()
        if not browser_resources_ready():
            download_mmdb()
        if not browser_resources_ready():
            raise RuntimeError(
                "The location database or fingerprint model could not be downloaded. Please retry."
            )


def _install_paired_bundle(fetcher: "CamoufoxFetcher") -> None:
    from camoufox.multiversion import BROWSERS_DIR, get_repo_name, version_folder_name

    from .update_permissions import reject_reparse_paths

    browsers = BROWSERS_DIR.resolve()
    repo = browsers / get_repo_name(fetcher.github_repo)
    version = version_folder_name(fetcher.version, fetcher.build)
    target = repo / version_folder_name(fetcher.version, fetcher.build, fetcher.installed_sha8)
    reject_reparse_paths(target)

    def complete(candidate: Path) -> bool:
        return _compatible_version(candidate) == version and _complete_bundle(candidate)

    # Never let upstream unpack over files from an interrupted install. Its
    # replace=True only removes a target which already contains version.json.
    # Retire damaged caches outside browsers/ before the SDK can scan them.
    browsers.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".cpm-browser-repair-", dir=browsers.parent) as discarded:
        if target.exists() and not complete(target):
            target.rename(Path(discarded) / target.name)
        # The SDK selects the first cached copy matching the pin, regardless of
        # digest. Retire broken siblings of this exact release as well, keeping
        # all healthy builds and profile data in place.
        same_version = re.compile(rf"{re.escape(version)}(?:-[0-9a-fA-F]{{8}})?")
        if repo.is_dir():
            for candidate in repo.iterdir():
                if candidate != target and same_version.fullmatch(candidate.name):
                    reject_reparse_paths(candidate)
                    if candidate.is_dir() and not complete(candidate):
                        candidate.rename(Path(discarded) / candidate.name)
        fetcher.install()
        if not complete(target):
            raise RuntimeError(
                "The browser download finished but required browser files are missing. Try again."
            )


browser_installer = BrowserInstaller()
