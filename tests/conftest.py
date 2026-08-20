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
        help="Fail any test that opens a connection off this machine.",
    )


def pytest_configure(config):
    """Refuse every outbound connection when asked.

    The browser suite is meant to run without the internet, which is a claim that
    rots the moment someone adds a test that quietly reaches for it. This turns
    the claim into a command:

        uv run pytest -m browser --no-network

    Loopback stays open, since that is where the test server and the browser's own
    control channel live. This only binds the test process; the browser is a child
    process of its own, and what keeps *it* local is that the tests only ever send
    it loopback URLs.
    """
    if not config.getoption("--no-network"):
        return

    real_connect = socket.socket.connect

    def guard(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and not _is_loopback(host):
            raise AssertionError(f"--no-network: refused a connection to {host}")
        return real_connect(self, address)

    socket.socket.connect = guard


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A name the test process would have to resolve, which means leaving
        # the machine to ask.
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
