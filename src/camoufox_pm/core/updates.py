"""Check official application releases; installation stays with OS installers."""

import re
from typing import Literal

import httpx
from packaging.version import Version
from pydantic import BaseModel, Field

from camoufox_pm import __version__

REPOSITORY = "polyackiy/camoufox-profile-manager"
RELEASES = f"https://github.com/{REPOSITORY}/releases"


class ReleaseAsset(BaseModel):
    name: str
    url: str


class UpdateStatus(BaseModel):
    current_version: str = __version__
    latest_version: str | None = None
    available: bool = False
    release_url: str = RELEASES
    assets: list[ReleaseAsset] = Field(default_factory=list)
    error: str | None = None


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("Release version is not a stable version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


async def check_update(channel: Literal["stable", "preview"] = "stable") -> UpdateStatus:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"https://api.github.com/repos/{REPOSITORY}/releases"
                + ("/latest" if channel == "stable" else "?per_page=100"),
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            release = response.json()
        if channel == "preview":
            if not isinstance(release, list):
                raise ValueError("Invalid releases response")
            eligible = [
                item
                for item in release
                if isinstance(item, dict)
                and not item.get("draft")
                and isinstance(item.get("tag_name"), str)
                and re.fullmatch(r"v\d+\.\d+\.\d+(?:-rc\.\d+)?", item["tag_name"])
            ]
            if not eligible:
                raise ValueError("No preview release is available")
            release = max(eligible, key=lambda item: Version(item["tag_name"].lstrip("v")))
        tag = release["tag_name"]
        if channel == "stable":
            version_tuple(tag)  # Restrict tags before constructing download URLs.
        if release.get("draft") or (channel == "stable" and release.get("prerelease")):
            raise ValueError("No stable release is available")
        url = f"{RELEASES}/tag/{tag}"
        assets = [
            ReleaseAsset(name=item["name"], url=item["browser_download_url"])
            for item in release.get("assets", [])
            if item.get("browser_download_url", "").startswith(f"{RELEASES}/download/{tag}/")
        ]
        return UpdateStatus(
            latest_version=tag.lstrip("v"),
            available=Version(tag.lstrip("v")) > Version(__version__),
            release_url=url,
            assets=assets,
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return UpdateStatus(
            error="Could not check for updates. Check your connection and try again."
        )
