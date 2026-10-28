"""Download an official native installer, checking release integrity over HTTPS.

This is the explicit OS-installer flow, not the signed TUF replacement path.
A same-release checksum detects corruption; it is not a publisher signature.
"""

import hashlib
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .updates import RELEASES, UpdateStatus

INSTALLERS = {
    "linux": "camoufox-pm-linux-x86_64.deb",
    "win32": "camoufox-pm-windows-x86_64-setup.exe",
}
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
DOWNLOAD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


def installer_url(update: UpdateStatus, platform: str) -> tuple[str, str]:
    name = INSTALLERS[platform]
    version = update.latest_version or ""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", version):
        raise ValueError("Invalid installer release version")
    base = f"{RELEASES}/download/v{version}/"
    urls = {asset.name: asset.url for asset in update.assets}
    if urls.get(name) != base + name or urls.get(name + ".sha256") != base + name + ".sha256":
        raise ValueError("This release has no matching installer and checksum")
    return base + name, base + name + ".sha256"


def _response(client: httpx.Client, url: str) -> httpx.Response:
    for _ in range(6):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in DOWNLOAD_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Installer download redirected to an untrusted host")
        response = client.send(client.build_request("GET", url), stream=True)
        if response.is_redirect:
            target = response.headers.get("location", "")
            response.close()
            url = str(httpx.URL(url).join(target))
            continue
        try:
            response.raise_for_status()
        except BaseException:
            response.close()
            raise
        return response
    raise ValueError("Too many installer download redirects")


def download_installer(
    update: UpdateStatus,
    platform: str,
    destination: Path,
    progress: Callable[[int, int | None], None],
    *,
    client: httpx.Client,
) -> str:
    url, checksum_url = installer_url(update, platform)
    response = _response(client, checksum_url)
    try:
        data = bytearray()
        for chunk in response.iter_bytes(1024):
            data.extend(chunk)
            if len(data) > 1024:
                raise ValueError("Invalid installer checksum")
        expected = data.decode("ascii").strip().split()
        if not expected or not re.fullmatch(r"[a-fA-F0-9]{64}", expected[0]):
            raise ValueError("Invalid installer checksum")
        if len(expected) != 2 or expected[1].lstrip("*") != INSTALLERS[platform]:
            raise ValueError("Installer checksum names another file")
        digest = expected[0].lower()
    finally:
        response.close()
    response = _response(client, url)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        total = (
            int(response.headers["content-length"])
            if "content-length" in response.headers
            else None
        )
        if total is not None and not 0 < total <= MAX_INSTALLER_BYTES:
            raise ValueError("Installer exceeds the download limit")
        received = 0
        sha = hashlib.sha256()
        deadline = time.monotonic() + 1800
        import os

        from .update_permissions import create_private_file

        with os.fdopen(create_private_file(temporary), "wb") as output:
            for chunk in response.iter_bytes(256 * 1024):
                received += len(chunk)
                if received > MAX_INSTALLER_BYTES or time.monotonic() > deadline:
                    raise ValueError("Installer download exceeded its limit")
                output.write(chunk)
                sha.update(chunk)
                progress(received, total)
            output.flush()
            os.fsync(output.fileno())
        if not received or (total is not None and received != total) or sha.hexdigest() != digest:
            raise ValueError("Installer checksum verification failed")
        temporary.replace(destination)
        if sys.platform == "win32":
            # Preserve downloaded-file provenance for Windows attachment policy.
            Path(str(destination) + ":Zone.Identifier").write_text(
                f"[ZoneTransfer]\nZoneId=3\nHostUrl={url}\n", encoding="utf-8"
            )
        return digest
    finally:
        response.close()
        temporary.unlink(missing_ok=True)
