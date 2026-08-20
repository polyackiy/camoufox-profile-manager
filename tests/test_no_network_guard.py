"""The --no-network guard has to outrank the code it polices.

Only meaningful with the flag: without it, these would be tests that reach the
internet, which is the thing the flag exists to prevent.
"""

import socket

import pytest

from camoufox_pm.core import proxy_check


def _type_names(error: BaseException):
    """Every exception type in the tree, since anyio wraps failures in a group."""
    yield type(error).__name__
    for nested in getattr(error, "exceptions", ()):
        yield from _type_names(nested)


@pytest.fixture
def guarded(request):
    if not request.config.getoption("--no-network"):
        pytest.skip("only meaningful under --no-network")


@pytest.mark.asyncio
async def test_the_guard_outranks_a_module_that_swallows_failures(guarded):
    """`fill_what_geoip_would_have` catches every failure by design.

    It treats an unreachable endpoint as a reason to leave the launch alone,
    which is right in production and blinding in a test: an AssertionError from
    the guard was swallowed by exactly that, and a test making three outbound
    requests passed. That is why the guard raises past `except Exception`.
    """
    with pytest.raises(BaseException) as caught:
        await proxy_check.fill_what_geoip_would_have(
            None, {"geoip": False, "config": {"timezone": "Pacific/Auckland"}}
        )

    assert "NetworkBlocked" in _type_names(caught.value)


def test_the_guard_leaves_loopback_alone(guarded):
    """It must not block what the suite legitimately does: talk to itself."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(), timeout=5):
            pass

    assert socket.getaddrinfo("localhost", 80), "resolving localhost must stay allowed"
