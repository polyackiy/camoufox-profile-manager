"""Does what a profile claims match where its proxy actually comes out?

A pinned fingerprint keeps a profile's hardware honest. The parts deliberately
left dynamic — timezone, coordinates, locale — follow the proxy only as long as
the profile does not override them. A profile that reports ``Europe/Berlin``
while its proxy exits in Tokyo contradicts itself in a way any page can measure
with two lines of JavaScript, and no amount of fingerprint pinning hides it.

This module resolves where a proxy really comes out and compares that with what
the profile would tell a page.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from loguru import logger

from .models import BrowserSettings, ProxyConfig, ProxyType

Level = Literal["error", "warning", "info"]

# The same endpoints Camoufox asks for the exit address, so a check sees what the
# browser will see. We make the request ourselves rather than calling
# camoufox.ip.public_ip: that answer is cached for the life of the process, and a
# check button has to see the proxy as it is now, not as it was an hour ago.
IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
)

# MaxMind places an address in a city, not on a street. Below this a difference
# is database noise rather than a contradiction; above the second, no plausible
# reading of the coordinates puts them near the exit.
NEARBY_KM = 100.0
FAR_KM = 500.0

DEFAULT_TIMEOUT = 10.0


class LocationUnavailable(RuntimeError):
    """The exit address could not be placed on the map."""


@dataclass(frozen=True)
class ProxyLocation:
    """Where a proxy actually comes out, as the browser's own database sees it."""

    ip: str
    country: str | None = None
    timezone: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class Finding:
    """One thing worth telling the user about this proxy and this profile."""

    level: Level
    field: str
    message: str


@dataclass
class ProxyCheckResult:
    reachable: bool
    error: str | None = None
    latency_ms: int | None = None
    location: ProxyLocation | None = None
    findings: list[Finding] = field(default_factory=list)


def proxy_url(proxy: ProxyConfig) -> str:
    """The proxy as a URL, with credentials if it has them."""
    credentials = ""
    if proxy.username:
        credentials = proxy.username
        if proxy.password:
            credentials += f":{proxy.password}"
        credentials += "@"
    return f"{proxy.type.value}://{credentials}{proxy.server}"


def preflight(proxy: ProxyConfig) -> list[Finding]:
    """What can be said about a proxy without touching the network."""
    findings: list[Finding] = []
    if proxy.type in (ProxyType.SOCKS4, ProxyType.SOCKS5) and (proxy.username or proxy.password):
        findings.append(
            Finding(
                "error",
                "proxy",
                "Firefox refuses to authenticate to a SOCKS proxy, so this profile will "
                "fail to launch. Use an HTTP or HTTPS proxy for credentials.",
            )
        )
    return findings


def _utc_offset(zone: str) -> float | None:
    """Hours from UTC in this zone right now, or None if the name is unknown."""
    try:
        offset = datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return None if offset is None else offset.total_seconds() / 3600


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points."""
    lat1, lon1, lat2, lon2 = (radians(v) for v in (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371.0


def locate(ip: str) -> ProxyLocation:
    """Place an address using the database Camoufox itself uses at launch.

    Raises LocationUnavailable when the database is missing or does not know the
    address, so the caller can still report a working proxy it cannot place.
    """
    try:
        from camoufox.geolocation import get_geolocation
    except ImportError as exc:  # pragma: no cover - the geoip extra is a hard dependency
        raise LocationUnavailable(str(exc)) from exc

    try:
        geo = get_geolocation(ip)
    except Exception as exc:
        raise LocationUnavailable(str(exc)) from exc

    # Only the region is taken from geo.locale. Its language is sampled
    # statistically for that region, so the same address answered "de-BG" and then
    # "bg-BG" — not something to report as a fact about the proxy.
    return ProxyLocation(
        ip=ip,
        country=geo.locale.region,
        timezone=geo.timezone,
        latitude=geo.latitude,
        longitude=geo.longitude,
    )


def compare(settings: BrowserSettings, location: ProxyLocation) -> list[Finding]:
    """Everything a page could notice between this profile and this exit address.

    Pure: no network, no clock beyond the current UTC offsets, so the rules are
    testable on their own.
    """
    findings: list[Finding] = []
    where = location.country or location.ip

    if settings.timezone and location.timezone and settings.timezone != location.timezone:
        profile_offset = _utc_offset(settings.timezone)
        exit_offset = _utc_offset(location.timezone)
        if profile_offset is not None and profile_offset == exit_offset:
            findings.append(
                Finding(
                    "info",
                    "timezone",
                    f"This profile reports {settings.timezone} while the proxy exits in "
                    f"{where} ({location.timezone}). The clock agrees; the zone names "
                    "name different places.",
                )
            )
        else:
            findings.append(
                Finding(
                    "warning",
                    "timezone",
                    f"This profile reports {settings.timezone} but the proxy exits in "
                    f"{where} ({location.timezone}). A page can compare its clock with "
                    "the address it sees and find the contradiction.",
                )
            )
    elif not settings.timezone and location.timezone:
        findings.append(
            Finding(
                "info",
                "timezone",
                f"No timezone is set, so it follows the proxy: {location.timezone}.",
            )
        )

    if settings.geolocation:
        lat, lon = settings.geolocation.get("lat"), settings.geolocation.get("lon")
        placed = location.latitude is not None and location.longitude is not None
        if lat is not None and lon is not None and placed:
            distance = _distance_km(lat, lon, location.latitude, location.longitude)  # type: ignore[arg-type]
            if distance > FAR_KM:
                findings.append(
                    Finding(
                        "warning",
                        "geolocation",
                        f"The coordinates are {distance:,.0f} km from where the proxy "
                        f"exits ({where}). A site granted location permission sees one "
                        "place and the address says another.",
                    )
                )
            elif distance > NEARBY_KM:
                findings.append(
                    Finding(
                        "info",
                        "geolocation",
                        f"The coordinates are {distance:,.0f} km from where the proxy "
                        f"exits ({where}) — the same region, a different city.",
                    )
                )

    return findings


async def resolve_exit_ip(
    proxy: ProxyConfig | None, timeout: float = DEFAULT_TIMEOUT
) -> tuple[str, int]:
    """Ask, through the proxy, what address the internet sees. Returns (ip, ms)."""
    url = proxy_url(proxy) if proxy else None
    last_error: Exception | None = None

    # TLS is verified. A proxy that intercepts it fails the check with an SSL error,
    # which is worth knowing rather than papering over.
    async with httpx.AsyncClient(proxy=url, timeout=timeout) as client:
        for endpoint in IP_ENDPOINTS:
            started = time.monotonic()
            try:
                response = await client.get(endpoint)
                response.raise_for_status()
            except Exception as exc:
                last_error = exc
                logger.debug(f"Proxy check via {endpoint} failed: {exc}")
                continue
            return response.text.strip(), int((time.monotonic() - started) * 1000)

    raise ConnectionError(_readable(last_error))


def _readable(error: Exception | None) -> str:
    """Turn an httpx failure into something worth showing a user."""
    if error is None:
        return "The proxy did not answer."
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code == 407:
            return "The proxy rejected the credentials."
        return f"The proxy answered {error.response.status_code}."
    if isinstance(error, httpx.ProxyError):
        # httpx reports a failed CONNECT as a ProxyError, not as a status, so the
        # common case of wrong credentials arrives here rather than above.
        if "407" in str(error):
            return "The proxy rejected the credentials."
        return f"The proxy refused the connection: {error}"
    if isinstance(error, httpx.TimeoutException):
        return "The proxy did not answer in time."
    if (
        isinstance(error, ssl.SSLError)
        or isinstance(error, httpx.ConnectError)
        and ("SSL" in str(error) or "CERTIFICATE" in str(error))
    ):
        return f"The proxy intercepts TLS, so its certificate could not be verified: {error}"
    if isinstance(error, httpx.UnsupportedProtocol):
        return "This proxy protocol cannot be checked; SOCKS4 is not supported."
    return f"Could not reach the internet through the proxy: {error}"


async def fill_what_geoip_would_have(proxy: ProxyConfig | None, options: dict) -> None:
    """Supply the IP-derived values Camoufox skips when geoip is off.

    Setting coordinates turns geoip off, because that is the only way Camoufox
    keeps them — with geoip on it overwrites the geolocation keys from the exit
    address. But the same branch is what fills the timezone and the WebRTC
    address, so turning it off leaves both unset and Firefox falls back to *this
    computer's* timezone. Measured: a profile with Tokyo coordinates reported
    Europe/Moscow, the host's own zone. That is worse than any mismatch, because
    it is the real machine showing through.

    So we do that part ourselves, from the same exit address and the same
    database Camoufox would have used. A lookup that fails leaves the launch
    alone rather than blocking it.
    """
    if options.get("geoip"):
        return

    config = options.setdefault("config", {})
    needs_timezone = "timezone" not in config
    needs_webrtc = "webrtc:ipv4" not in config and not options.get("block_webrtc")
    if not needs_timezone and not needs_webrtc:
        return

    try:
        ip, _ = await resolve_exit_ip(proxy)
    except ConnectionError as exc:
        logger.warning(f"Could not find the exit address, leaving the launch alone: {exc}")
        return

    if needs_webrtc:
        config["webrtc:ipv4"] = ip
    if not needs_timezone:
        return

    try:
        location = await asyncio.to_thread(locate, ip)
    except LocationUnavailable as exc:
        logger.warning(f"Could not place {ip}, leaving the timezone alone: {exc}")
        return

    if location.timezone:
        config["timezone"] = location.timezone
        logger.info(f"Timezone {location.timezone} taken from the exit address {ip}")


async def check(
    proxy: ProxyConfig | None,
    settings: BrowserSettings,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProxyCheckResult:
    """Reach the internet through the proxy, place it, and compare it with the profile."""
    findings = preflight(proxy) if proxy else []

    try:
        ip, latency = await resolve_exit_ip(proxy, timeout=timeout)
    except ConnectionError as exc:
        return ProxyCheckResult(reachable=False, error=str(exc), findings=findings)

    try:
        # get_geolocation reads (and may download) the MaxMind database, so keep it
        # off the event loop.
        location = await asyncio.to_thread(locate, ip)
    except LocationUnavailable as exc:
        logger.warning(f"Could not place {ip}: {exc}")
        findings.append(
            Finding(
                "info",
                "proxy",
                "The proxy works, but its address could not be placed on the map. "
                "Run 'camoufox fetch' to install the location database.",
            )
        )
        return ProxyCheckResult(
            reachable=True, latency_ms=latency, location=ProxyLocation(ip=ip), findings=findings
        )

    findings.extend(compare(settings, location))
    return ProxyCheckResult(
        reachable=True, latency_ms=latency, location=location, findings=findings
    )
