"""Check official application releases; installation stays with OS installers."""

import re

import httpx
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


async def check_update() -> UpdateStatus:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            release = response.json()
        tag = release["tag_name"]
        latest = version_tuple(tag)
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("No stable release is available")
        url = f"{RELEASES}/tag/{tag}"
        assets = [
            ReleaseAsset(name=item["name"], url=item["browser_download_url"])
            for item in release.get("assets", [])
            if item.get("browser_download_url", "").startswith(f"{RELEASES}/download/{tag}/")
        ]
        return UpdateStatus(
            latest_version=tag.lstrip("v"),
            available=latest > version_tuple(__version__),
            release_url=url,
            assets=assets,
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return UpdateStatus(
            error="Could not check for updates. Check your connection and try again."
        )
