"""Bounded proxy-list parsing with credential-safe validation errors."""

import ipaddress
import re
from urllib.parse import unquote, urlsplit

from .models import ProxyConfig, ProxyType

MAX_IMPORT_LINES = 100
MAX_IMPORT_CHARS = 64_000


def normalize_proxy_server(server: str) -> str:
    """Return canonical host:port, requiring brackets around IPv6 addresses."""
    if not server or any(char.isspace() or ord(char) < 32 for char in server):
        raise ValueError("Server must contain a host and port without whitespace.")
    if any(char in server for char in "/?#@%\\"):
        raise ValueError("Server must contain only a host and port.")
    if not re.fullmatch(r"(?:\[[0-9a-fA-F:.]+\]|[^:\[\]]+):[0-9]+", server):
        raise ValueError("Server must use host:port or [IPv6]:port.")
    try:
        parsed = urlsplit("//" + server)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError("Server must contain a valid host and port.") from None
    if not host or port is None or not 1 <= port <= 65535:
        raise ValueError("Server requires an explicit port between 1 and 65535.")
    if ":" in host:
        try:
            host = "[" + ipaddress.IPv6Address(host).compressed + "]"
        except ValueError:
            raise ValueError("Invalid IPv6 server address.") from None
    else:
        try:
            host = host.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError("Invalid server hostname.") from None
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError("Invalid server hostname.")
        if re.fullmatch(r"[0-9.]+", host) and "." in host:
            try:
                host = str(ipaddress.IPv4Address(host))
            except ValueError:
                raise ValueError("Invalid IPv4 server address.") from None
    return f"{host}:{port}"


def _credential(value: str | None) -> str | None:
    if value is None:
        return None
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("Credentials contain invalid percent encoding.")
    try:
        decoded = unquote(value, errors="strict")
    except UnicodeError:
        raise ValueError("Credentials must use valid UTF-8 encoding.") from None
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        raise ValueError("Credentials cannot contain control characters.")
    return decoded or None


def _parse_line(line: str) -> ProxyConfig:
    if len(line) > 2048 or any(char.isspace() or ord(char) < 32 for char in line):
        raise ValueError("Proxy lines must be at most 2048 characters without whitespace.")
    try:
        parsed = urlsplit(line if "://" in line else "http://" + line)
    except ValueError:
        raise ValueError("Invalid proxy URL.") from None
    if parsed.scheme not in {item.value for item in ProxyType}:
        raise ValueError("Use http, https, socks4 or socks5.")
    if parsed.path or parsed.query or parsed.fragment or "?" in line or "#" in line:
        raise ValueError("Proxy URLs cannot include a path, query or fragment.")
    authority = parsed.netloc.rsplit("@", 1)[-1]
    server = normalize_proxy_server(authority)
    if parsed.netloc.count("@") > 1:
        raise ValueError("Encode special characters in credentials.")
    username, password = _credential(parsed.username), _credential(parsed.password)
    if password and not username:
        raise ValueError("A password requires a username.")
    return ProxyConfig(
        type=ProxyType(parsed.scheme), server=server, username=username, password=password
    )


def parse_proxy_import(text: str) -> list[tuple[int, ProxyConfig]]:
    """Validate the whole batch before the caller performs any database writes."""
    if len(text) > MAX_IMPORT_CHARS:
        raise ValueError("Proxy import exceeds 64000 characters.")
    lines = [
        (number, line.strip()) for number, line in enumerate(text.splitlines(), 1) if line.strip()
    ]
    if not lines or len(lines) > MAX_IMPORT_LINES:
        raise ValueError("Provide between 1 and 100 nonempty proxy lines.")
    result = []
    for number, line in lines:
        try:
            result.append((number, _parse_line(line)))
        except ValueError as exc:
            raise ValueError(f"Line {number}: {exc}") from None
    return result
