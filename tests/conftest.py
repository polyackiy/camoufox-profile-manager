"""Shared pytest fixtures, and the switch that proves the suite runs offline."""

import ipaddress
import socket

import pytest

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager


def pytest_addoption(parser):
    parser.addoption(
        "--no-network",
        action="store_true",
        help="Fail any test that reaches off this machine.",
    )


class NetworkBlocked(BaseException):
    """Deliberately not an Exception.

    Modules here catch Exception and carry on by design — `resolve_exit_ip`
    treats a failed endpoint as a reason to try the next one, and
    `fill_what_geoip_would_have` treats them all failing as a reason to leave the
    launch alone. An AssertionError from the guard was swallowed by exactly that,
    and a test that made three outbound requests passed. Measured, then changed.
    """


def pytest_configure(config):
    """Refuse everything that leaves this machine, when asked.

    The browser suite is meant to need no internet, which is a claim that rots
    the moment someone adds a test that quietly reaches for it. This turns the
    claim into a command:

        uv run pytest -m browser --no-network

    Loopback stays open: the test server and the browser's own control channel
    live there. Name resolution counts as leaving, since asking a resolver about
    a name off this machine is a request like any other.

    Scope worth knowing: this binds the *test* process. The browser is a child
    process of its own, and what keeps its page loads local is that the tests
    only ever hand it loopback URLs — not this guard.
    """
    if not config.getoption("--no-network"):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    real_sendto = socket.socket.sendto

    def _check(host: object, verb: str) -> None:
        if isinstance(host, str) and not _is_loopback(host):
            raise NetworkBlocked(f"--no-network: refused to {verb} {host}")

    def connect(self, address):
        _check(address[0] if isinstance(address, tuple) else None, "connect to")
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(address[0] if isinstance(address, tuple) else None, "connect to")
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None:
            _check(host, "resolve")
        return real_getaddrinfo(host, *args, **kwargs)

    def sendto(self, data, *args):
        address = args[-1] if args else None
        _check(address[0] if isinstance(address, tuple) else None, "send to")
        return real_sendto(self, data, *args)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    socket.socket.sendto = sendto


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A name this process would have to ask a resolver about, which is a
        # request off the machine in itself.
        return False


@pytest.fixture
async def storage(tmp_path):
    """A StorageManager backed by a throwaway SQLite database."""
    manager = StorageManager(str(tmp_path / "test.db"))
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
async def profile_manager(tmp_path):
    """A ProfileManager backed by a throwaway data directory."""
    storage = StorageManager(str(tmp_path / "test.db"))
    await storage.initialize()
    manager = ProfileManager(storage, str(tmp_path))
    await manager.initialize()
    yield manager
    await storage.close()
