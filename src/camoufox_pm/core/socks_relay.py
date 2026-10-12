"""Session-owned TCP SOCKS5 relay for Firefox's authenticated proxy limitation.

Only the loopback listener is unauthenticated. Upstream credentials stay in
memory, domain requests are forwarded verbatim, and failures never connect to
the destination directly. This is not a system proxy or a UDP tunnel.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit


async def _address(reader: asyncio.StreamReader, kind: int) -> bytes:
    if kind == 1:
        return await reader.readexactly(4)
    if kind == 4:
        return await reader.readexactly(16)
    if kind == 3:
        size = await reader.readexactly(1)
        if size == b"\x00":
            raise ValueError("Empty SOCKS hostname")
        return size + await reader.readexactly(size[0])
    raise ValueError("Unsupported SOCKS address type")


def validate_credentials(username: str | None, password: str | None) -> tuple[bytes, bytes]:
    """Validate RFC 1929 byte lengths without putting secrets in errors."""
    user, secret = (username or "").encode("utf-8"), (password or "").encode("utf-8")
    if not 1 <= len(user) <= 255 or not 1 <= len(secret) <= 255:
        raise ValueError("SOCKS5 username and password must each contain 1–255 UTF-8 bytes")
    return user, secret


class SocksRelay:
    """One ephemeral listener and its connections, owned by one browser session.

    Other local processes can use this listener while it is running. It never
    binds a LAN address, writes credentials, or shares a listener across profiles.
    """

    def __init__(
        self,
        server: str,
        username: str | None,
        password: str | None,
        *,
        negotiation_timeout: float = 10.0,
        max_connections: int = 128,
    ) -> None:
        parsed = urlsplit(server)
        if parsed.scheme != "socks5" or not parsed.hostname or parsed.port is None:
            raise ValueError("SOCKS5 requires a host and port")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Provide SOCKS credentials separately")
        self._host = parsed.hostname
        self._port = parsed.port
        self._username, self._password = validate_credentials(username, password)
        self._timeout = negotiation_timeout
        self._max_connections = max_connections
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self.port: int | None = None

    @property
    def browser_proxy(self) -> dict[str, str]:
        if self.port is None or self._closed:
            raise RuntimeError("SOCKS relay is not running")
        return {"server": f"socks5://127.0.0.1:{self.port}"}

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("SOCKS relay is closed")
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed or len(self._tasks) >= self._max_connections:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        negotiated = False
        connected = False
        try:

            async def negotiate():
                nonlocal upstream_writer, negotiated, connected
                version, count = await reader.readexactly(2)
                if version != 5 or count == 0:
                    return
                methods = await reader.readexactly(count)
                if 0 not in methods:
                    writer.write(b"\x05\xff")
                    await writer.drain()
                    return
                writer.write(b"\x05\x00")
                await writer.drain()
                negotiated = True
                header = await reader.readexactly(4)
                if header[:3] != b"\x05\x01\x00":
                    writer.write(b"\x05\x07\x00\x01" + bytes(6))
                    await writer.drain()
                    return
                address = await _address(reader, header[3])
                destination_port = await reader.readexactly(2)
                # Never resolve destination hostnames here. The upstream server
                # receives the client's address type and original hostname bytes.
                request = header + address + destination_port
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    self._host, self._port
                )
                upstream_writer.write(b"\x05\x01\x02")
                await upstream_writer.drain()
                if await upstream_reader.readexactly(2) != b"\x05\x02":
                    raise ConnectionError("SOCKS5 authentication method rejected")
                upstream_writer.write(
                    b"\x01"
                    + bytes([len(self._username)])
                    + self._username
                    + bytes([len(self._password)])
                    + self._password
                )
                await upstream_writer.drain()
                if await upstream_reader.readexactly(2) != b"\x01\x00":
                    raise ConnectionError("SOCKS5 credentials rejected")
                upstream_writer.write(request)
                await upstream_writer.drain()
                reply = await upstream_reader.readexactly(4)
                if reply[0] != 5 or reply[2] != 0:
                    raise ConnectionError("Invalid SOCKS5 reply")
                reply += await _address(upstream_reader, reply[3])
                reply += await upstream_reader.readexactly(2)
                writer.write(reply)
                await writer.drain()
                if reply[1] != 0:
                    return
                connected = True
                return upstream_reader

            upstream_reader = await asyncio.wait_for(negotiate(), self._timeout)
            if not connected:
                return
            assert upstream_writer is not None
            assert upstream_reader is not None
            # Cancel both pumps when the connection task is cancelled or one
            # side fails; EOF can half-close while the peer sends its response.
            pumps = [
                asyncio.create_task(self._pump(reader, upstream_writer)),
                asyncio.create_task(self._pump(upstream_reader, writer)),
            ]
            try:
                await asyncio.gather(*pumps)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            if negotiated and not connected:
                try:
                    writer.write(b"\x05\x01\x00\x01" + bytes(6))
                    await asyncio.wait_for(writer.drain(), 1.0)
                except (ConnectionError, OSError, asyncio.TimeoutError):
                    pass
            # No error text is logged: SOCKS exceptions must not expose secrets.
        finally:
            writer.close()
            if upstream_writer is not None:
                upstream_writer.close()
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        writer.wait_closed(),
                        *([upstream_writer.wait_closed()] if upstream_writer is not None else []),
                        return_exceptions=True,
                    ),
                    1.0,
                )
            except asyncio.TimeoutError:
                writer.transport.abort()
                if upstream_writer is not None:
                    upstream_writer.transport.abort()

    @staticmethod
    async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
            await writer.drain()

    async def close(self) -> None:
        """Stop accepting, cancel active tunnels, and discard credential references."""
        self._closed = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        self._tasks.clear()
        self._username = self._password = b""
