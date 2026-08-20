"""Shared machinery for the browser tests: two local origins, and offline launches.

Kept out of conftest.py because these are imported by name; a conftest can be
loaded more than once depending on the rootdir, and importing one directly is a
pytest pitfall rather than a convention.
"""

import errno
import http.server
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple

# In the MaxMind database Camoufox ships, which not every address is — 1.1.1.1
# is not.
GEOIP_ADDRESS = "8.8.8.8"

PAGE = b"<!doctype html><meta charset=utf-8><title>test page</title><p>test page"


def offline_launch(options: dict[str, Any]) -> dict[str, Any]:
    """These launch options with the one thing that reaches the internet replaced.

    A real launch passes `geoip=True`, and Camoufox then asks the internet what
    this machine's public address is. The answer is cached per process, so it is
    one lookup per run rather than one per launch — but a *failed* lookup is not
    cached, so with no internet every launch retries and fails, and the answer
    differs by machine anyway.

    Naming an address keeps the whole path. Camoufox gates only the HTTP lookup
    on `geoip is True`; everything after it — geolocation, timezone, locale, the
    WebRTC candidate and the IPv6 pref that goes with it — runs the same either
    way, against the database on disk. `geoip=False` would take a different
    branch and change what the browser does, which is why it is not used here.

    Left alone when a test set geoip itself; profiles with coordinates turn it
    off, and that is the behaviour under test. Note it also short-circuits the
    lookup Camoufox makes *through a proxy* — no browser test launches with one
    today, and one that did would want its own answer here.
    """
    launch = dict(options)
    if launch.get("geoip") is True:
        launch["geoip"] = GEOIP_ADDRESS
    return launch


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args: object) -> None:
        """Silence the per-request logging; pytest output is not a web log."""


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class LocalSites(NamedTuple):
    """Two URLs serving the same page that the browser treats as different sites."""

    first: str
    second: str


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(family: int, host: str, port: int) -> _Server:
    class Bound(_Server):
        address_family = family

    server = Bound((host, port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@contextmanager
def serve_local_sites() -> Iterator[LocalSites]:
    """Serve one page under both loopback names, on one port.

    Both families, so `localhost` reaches a server whichever way this machine
    resolves it.
    """
    port = _free_port()
    servers = [_serve(socket.AF_INET, "127.0.0.1", port)]
    try:
        servers.append(_serve(socket.AF_INET6, "::1", port))
    except OSError as error:
        # No IPv6 here, so `localhost` resolves to 127.0.0.1 and the first
        # server answers both names. Anything else — the port taken on ::1
        # between the probe and the bind — must not be swallowed: `localhost`
        # may resolve to ::1, and the tests would then fail on a 45-second
        # navigation timeout with nothing pointing back at this fixture.
        if error.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
            raise

    try:
        yield LocalSites(f"http://127.0.0.1:{port}/", f"http://localhost:{port}/")
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
