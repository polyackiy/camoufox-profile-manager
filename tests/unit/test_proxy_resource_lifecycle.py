"""Client TCP sockets close before a proxy check returns or cancellation drains."""

import asyncio

import anyio
import pytest

from camoufox_pm.core import proxy_check
from camoufox_pm.core.models import ProxyConfig
from tests.support.proxy_protocols import certificates, proxy_fixture


@pytest.fixture
def outbound_sockets(monkeypatch):
    opened = []
    connect = anyio.connect_tcp

    async def tracked_connect(*args, **kwargs):
        stream = await connect(*args, **kwargs)
        opened.append((stream, stream.extra(anyio.abc.SocketAttribute.raw_socket)))
        return stream

    monkeypatch.setattr(anyio, "connect_tcp", tracked_connect)
    return opened


def config(fixture):
    return ProxyConfig(
        type=fixture.protocol,
        server=f"127.0.0.1:{fixture.proxy_port}",
        username=fixture.username if fixture.auth else None,
        password=fixture.password if fixture.auth else None,
    )


async def cleanup(opened):
    # Only after the assertion: failed regression cases must clean their own
    # retained streams rather than leaking into unrelated test collection sites.
    for stream, _socket in opened:
        await stream.aclose()


@pytest.mark.parametrize(
    ("protocol", "auth"),
    [("http", True), ("https", True), ("socks4", False), ("socks5", False), ("socks5", True)],
)
async def test_refused_proxy_closes_connected_client_sockets(
    tmp_path, monkeypatch, outbound_sockets, protocol, auth
):
    certs = certificates(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    async with proxy_fixture(protocol, certs, auth=auth, reject=True) as fixture:
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"https://cpm-origin.invalid:{fixture.https_port}/ip",)
        )
        try:
            with pytest.raises(ConnectionError):
                await proxy_check.resolve_exit_ip(config(fixture), timeout=1)
            assert outbound_sockets, "The refusal must exercise a connected TCP socket"
            assert all(socket.fileno() == -1 for _, socket in outbound_sockets)
            assert not any(event[0] == "origin" for event in fixture.events)
        finally:
            await cleanup(outbound_sockets)


async def test_cancelled_socks_negotiation_closes_connected_client_socket(
    tmp_path, monkeypatch, outbound_sockets
):
    certs = certificates(tmp_path)
    async with proxy_fixture("socks5", certs) as fixture:
        connected = asyncio.Event()

        async def stall(reader, writer):
            await reader.readexactly(3)
            connected.set()
            await reader.read()

        fixture.proxy_port = await fixture.listen(stall)
        task = asyncio.create_task(proxy_check.resolve_exit_ip(config(fixture), timeout=5))
        try:
            await asyncio.wait_for(connected.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert outbound_sockets
            assert all(socket.fileno() == -1 for _, socket in outbound_sockets)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await cleanup(outbound_sockets)


async def test_stalled_socks_negotiation_obeys_endpoint_deadline(
    tmp_path, monkeypatch, outbound_sockets
):
    certs = certificates(tmp_path)
    async with proxy_fixture("socks5", certs) as fixture:

        async def stall(reader, writer):
            await reader.readexactly(3)
            await reader.read()

        fixture.proxy_port = await fixture.listen(stall)
        monkeypatch.setattr(proxy_check, "IP_ENDPOINTS", ("https://cpm-origin.invalid/ip",))
        try:
            # This outer watchdog fails the regression; it must never supply the
            # product's missing SOCKS negotiation deadline itself.
            with pytest.raises(ConnectionError, match="did not answer in time"):
                await asyncio.wait_for(proxy_check.resolve_exit_ip(config(fixture), timeout=0.5), 5)
            assert outbound_sockets
            assert all(socket.fileno() == -1 for _, socket in outbound_sockets)
            assert not any(event[0] == "origin" for event in fixture.events)
        finally:
            await cleanup(outbound_sockets)


async def test_repeated_cancellation_waits_for_owned_stream_cleanup():
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []

    class Stream:
        async def aclose(self):
            entered.set()
            await release.wait()
            closed.append(True)

    streams = [Stream()]
    task = asyncio.create_task(proxy_check._close_owned_streams(streams))
    try:
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not closed and streams
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True] and not streams
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_cleanup_attempts_every_stream_and_never_exports_close_error_text():
    closed = []

    class Failed:
        async def aclose(self):
            raise OSError("private-user:private-password@proxy.invalid")

    class Other:
        async def aclose(self):
            await asyncio.sleep(0)
            closed.append(True)

    streams = [Failed(), Other()]
    with pytest.raises(ConnectionError) as error:
        await proxy_check._close_owned_streams(streams)
    assert "private-" not in str(error.value)
    assert closed == [True] and not streams
