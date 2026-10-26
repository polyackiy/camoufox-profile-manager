"""Local HTTP/TLS/SOCKS fixtures with bounded I/O and an ephemeral test CA.

Synthetic destinations are routed only to this fixture's loopback origin, never
resolved or dialled by name. Events contain only fictional test credentials.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import ssl
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def certificates(
    directory: Path, *, dns_names: tuple[str, ...] = ("cpm-origin.invalid", "localhost")
) -> tuple[Path, Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CPM ephemeral fixture CA")])
    now = datetime.now(timezone.utc)
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cpm-origin.invalid")]))
        .issuer_name(name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    *(x509.DNSName(name) for name in dns_names),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("2001:db8::19")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    paths = directory / "ca.pem", directory / "server.pem", directory / "server.key"
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return paths


class ProxyFixture:
    def __init__(
        self, protocol: str, certificates: tuple[Path, Path, Path], *, auth=False, reject=False
    ):
        self.protocol = protocol
        self.ca, self.cert, self.key = certificates
        self.auth, self.reject = auth, reject
        self.username, self.password = "fixture-user", "fixture:password@only"
        self.events = []
        self.errors = []
        self.tasks = set()
        self.servers = []
        self.http_port = self.https_port = self.proxy_port = 0

    def ssl_context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        return context

    async def listen(self, handler, **kwargs):
        def accept(reader, writer):
            task = asyncio.create_task(self.run(handler, reader, writer))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        server = await asyncio.start_server(accept, "127.0.0.1", 0, **kwargs)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    async def run(self, handler, reader, writer):
        try:
            await asyncio.wait_for(handler(reader, writer), 15)
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError):
            pass
        except Exception as error:
            self.errors.append(error)
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError):
                writer.transport.abort()

    async def origin(self, reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        self.events.append(("origin", headers.split(b"\r\n", 1)[0]))
        body = b"203.0.113.19" if b"/ip " in headers else b"CPM_PROXY_PROTOCOL_PROOF"
        writer.write(
            b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()

    async def tunnel(self, reader, writer, port, initial=b""):
        # Test endpoints cannot cause arbitrary network connections.
        if port not in {self.http_port, self.https_port}:
            return
        upstream, outbound = await asyncio.open_connection("127.0.0.1", port)
        outbound.write(initial)
        await outbound.drain()

        async def pump(source, target):
            while data := await source.read(65536):
                target.write(data)
                await target.drain()
            if target.can_write_eof():
                target.write_eof()

        pumps = [
            asyncio.create_task(pump(reader, outbound)),
            asyncio.create_task(pump(upstream, writer)),
        ]
        try:
            # Either close is enough for these one-response local origins.
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            outbound.close()
            await outbound.wait_closed()

    async def proxy(self, reader, writer):
        if self.protocol in {"http", "https"}:
            headers = await reader.readuntil(b"\r\n\r\n")
            line = headers.split(b"\r\n", 1)[0]
            expected = b"Proxy-Authorization: Basic " + base64.b64encode(
                f"{self.username}:{self.password}".encode()
            )
            if self.reject or (self.auth and expected.lower() not in headers.lower()):
                writer.write(
                    b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="fixture"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
                )
                await writer.drain()
                self.events.append(("rejected", self.protocol))
                return
            self.events.append(("request", line))
            if line.startswith(b"CONNECT "):
                port = int(line.split()[1].rsplit(b":", 1)[1])
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                await self.tunnel(reader, writer, port)
            else:
                from urllib.parse import urlsplit

                url = urlsplit(line.split()[1].decode())
                await self.tunnel(reader, writer, url.port or 80, headers)
        elif self.protocol == "socks4":
            head = await reader.readexactly(8)
            assert head[:2] == b"\x04\x01"
            user = (await reader.readuntil(b"\x00"))[:-1]
            if head[4:7] == b"\x00\x00\x00" and head[7] != 0:
                domain = (await reader.readuntil(b"\x00"))[:-1]
            else:
                domain = str(ipaddress.ip_address(head[4:8])).encode()
            port = int.from_bytes(head[2:4], "big")
            self.events.append(("connect", domain, port, user))
            writer.write((b"\x00\x5b" if self.reject else b"\x00\x5a") + head[2:8])
            await writer.drain()
            if not self.reject:
                await self.tunnel(reader, writer, port)
        else:
            version, size = await reader.readexactly(2)
            assert version == 5
            methods = await reader.readexactly(size)
            method = 2 if self.auth else 0
            writer.write(bytes([5, method if method in methods else 255]))
            await writer.drain()
            if method not in methods:
                return
            if self.auth:
                assert await reader.readexactly(1) == b"\x01"
                username = await reader.readexactly((await reader.readexactly(1))[0])
                password = await reader.readexactly((await reader.readexactly(1))[0])
                allowed = (
                    username == self.username.encode()
                    and password == self.password.encode()
                    and not self.reject
                )
                self.events.append(("auth", allowed))
                writer.write(bytes([1, 0 if allowed else 1]))
                await writer.drain()
                if not allowed:
                    return
            head = await reader.readexactly(4)
            assert head[:3] == b"\x05\x01\x00"
            if head[3] == 3:
                domain = await reader.readexactly((await reader.readexactly(1))[0])
            else:
                domain = str(
                    ipaddress.ip_address(await reader.readexactly(4 if head[3] == 1 else 16))
                ).encode()
            port = int.from_bytes(await reader.readexactly(2), "big")
            self.events.append(("connect", domain, port))
            writer.write(b"\x05" + (b"\x05" if self.reject else b"\x00") + b"\x00\x01" + bytes(6))
            await writer.drain()
            if not self.reject:
                await self.tunnel(reader, writer, port)

    async def start(self):
        self.http_port = await self.listen(self.origin)
        self.https_port = await self.listen(self.origin, ssl=self.ssl_context())
        self.proxy_port = await self.listen(
            self.proxy, **({"ssl": self.ssl_context()} if self.protocol == "https" else {})
        )
        return self

    async def close(self):
        for server in self.servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in self.servers))
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        assert not self.errors, self.errors


@asynccontextmanager
async def proxy_fixture(protocol, certs, **kwargs):
    fixture = ProxyFixture(protocol, certs, **kwargs)
    try:
        yield await fixture.start()
    finally:
        await fixture.close()
