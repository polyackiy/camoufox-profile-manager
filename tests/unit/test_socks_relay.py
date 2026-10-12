"""Deterministic wire tests against an authenticated SOCKS5 fixture."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from camoufox_pm.core.socks_relay import SocksRelay


@asynccontextmanager
async def upstream(username=b"alice", password=b"secret", response_body=None):
    events = []
    tasks = set()

    async def serve(reader, writer):
        try:
            assert await reader.readexactly(3) == b"\x05\x01\x02"
            writer.write(b"\x05\x02")
            await writer.drain()
            assert await reader.readexactly(1) == b"\x01"
            user = await reader.readexactly((await reader.readexactly(1))[0])
            pwd = await reader.readexactly((await reader.readexactly(1))[0])
            events.append((user, pwd))
            ok = user == username and pwd == password
            writer.write(bytes([1, 0 if ok else 1]))
            await writer.drain()
            if not ok:
                return
            head = await reader.readexactly(4)
            assert head[:3] == b"\x05\x01\x00"
            if head[3] == 3:
                domain = await reader.readexactly((await reader.readexactly(1))[0])
            else:
                domain = await reader.readexactly(4 if head[3] == 1 else 16)
            port = await reader.readexactly(2)
            events.append((domain, int.from_bytes(port, "big")))
            writer.write(b"\x05\x00\x00\x01" + bytes(6))
            await writer.drain()
            if response_body is not None:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                    + str(len(response_body)).encode()
                    + b"\r\n\r\n"
                    + response_body
                )
                await writer.drain()
                return
            while data := await reader.read(4096):
                writer.write(data)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader, writer):
        task = asyncio.create_task(serve(reader, writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], events
    finally:
        server.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


async def connect(relay, domain=b"must-not-resolve.invalid"):
    reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    assert await reader.readexactly(2) == b"\x05\x00"
    writer.write(b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + b"\x01\xbb")
    await writer.drain()
    return reader, writer, await reader.readexactly(10)


@pytest.mark.asyncio
async def test_auth_domain_and_bidirectional_tunnel():
    async with upstream() as (port, events):
        relay = SocksRelay(f"socks5://127.0.0.1:{port}", "alice", "secret")
        await relay.start()
        try:
            reader, writer, reply = await connect(relay)
            assert reply[1] == 0
            writer.write(b"payload")
            await writer.drain()
            assert await reader.readexactly(7) == b"payload"
            assert events == [(b"alice", b"secret"), (b"must-not-resolve.invalid", 443)]
            assert set(relay.browser_proxy) == {"server"}
            writer.close()
            await writer.wait_closed()
        finally:
            await relay.close()


@pytest.mark.asyncio
async def test_wrong_password_fails_without_destination_connection():
    async with upstream() as (port, events):
        relay = SocksRelay(f"socks5://127.0.0.1:{port}", "alice", "wrong")
        await relay.start()
        try:
            reader, writer, reply = await connect(relay)
            assert reply[1] != 0
            assert await reader.read() == b""
            assert events == [(b"alice", b"wrong")]
            writer.close()
            await writer.wait_closed()
        finally:
            await relay.close()


@pytest.mark.asyncio
async def test_profiles_are_isolated_and_close_stops_active_tunnels():
    async with upstream() as (a, ea), upstream(b"bob", b"other") as (b, eb):
        first = SocksRelay(f"socks5://127.0.0.1:{a}", "alice", "secret")
        second = SocksRelay(f"socks5://127.0.0.1:{b}", "bob", "other")
        await first.start()
        await second.start()
        assert first.port != second.port
        r1, w1, _ = await connect(first)
        r2, w2, _ = await connect(second)
        await first.close()
        await first.close()
        assert await r1.read() == b""
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", first.port)
        w2.write(b"still-live")
        await w2.drain()
        assert await r2.readexactly(10) == b"still-live"
        assert ea[0] == (b"alice", b"secret") and eb[0] == (b"bob", b"other")
        await second.close()
        assert not first._tasks and first._username == first._password == b""
        w1.close()
        w2.close()


@pytest.mark.asyncio
async def test_stalled_client_times_out_and_connection_limit_rejects():
    relay = SocksRelay(
        "socks5://127.0.0.1:1", "alice", "secret", negotiation_timeout=0.05, max_connections=1
    )
    await relay.start()
    try:
        r1, w1 = await asyncio.open_connection("127.0.0.1", relay.port)
        r2, w2 = await asyncio.open_connection("127.0.0.1", relay.port)
        assert await asyncio.wait_for(r2.read(), 1) == b""
        assert await asyncio.wait_for(r1.read(), 1) == b""
        w1.close()
        w2.close()
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_unreachable_upstream_returns_failure():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    relay = SocksRelay(f"socks5://127.0.0.1:{port}", "alice", "secret")
    await relay.start()
    try:
        reader, writer, reply = await connect(relay)
        assert reply[1] != 0 and await reader.read() == b""
        writer.close()
    finally:
        await relay.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,address",
    [(1, bytes([203, 0, 113, 7])), (4, bytes.fromhex("20010db8000000000000000000000001"))],
)
async def test_ip_address_types_are_forwarded_verbatim(kind, address):
    async with upstream() as (port, events):
        relay = SocksRelay(f"socks5://127.0.0.1:{port}", "alice", "secret")
        await relay.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            assert await reader.readexactly(2) == b"\x05\x00"
            writer.write(b"\x05\x01\x00" + bytes([kind]) + address + b"\x01\xbb")
            await writer.drain()
            assert (await reader.readexactly(10))[1] == 0
            assert events[-1] == (address, 443)
            writer.close()
        finally:
            await relay.close()


@pytest.mark.asyncio
async def test_udp_associate_is_explicitly_rejected():
    relay = SocksRelay("socks5://127.0.0.1:1", "alice", "secret")
    await relay.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x00"
        writer.write(b"\x05\x03\x00\x01" + bytes(6))
        await writer.drain()
        assert (await reader.readexactly(10))[1] == 7
        assert await reader.read() == b""
        writer.close()
    finally:
        await relay.close()
