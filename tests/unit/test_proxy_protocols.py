"""Wire-level transport and TLS acceptance using only synthetic local endpoints."""

import asyncio

import pytest

from camoufox_pm.core import proxy_check
from camoufox_pm.core.models import BrowserSettings, ProxyConfig, ProxyType
from camoufox_pm.core.proxy_import import parse_proxy_import
from camoufox_pm.core.socks_relay import SocksRelay
from tests.support.proxy_protocols import certificates, proxy_fixture


@pytest.fixture
def certs(tmp_path):
    return certificates(tmp_path)


def config(fixture):
    return ProxyConfig(
        type=fixture.protocol,
        server=f"127.0.0.1:{fixture.proxy_port}",
        username=fixture.username if fixture.auth else None,
        password=fixture.password if fixture.auth else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,auth",
    [
        ("http", False),
        ("http", True),
        ("https", False),
        ("https", True),
        ("socks4", False),
        ("socks5", False),
        ("socks5", True),
    ],
)
async def test_health_check_and_fresh_launch_ip_use_remote_destination_and_verified_tls(
    certs, monkeypatch, protocol, auth
):
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    async with proxy_fixture(protocol, certs, auth=auth) as fixture:
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"https://cpm-origin.invalid:{fixture.https_port}/ip",)
        )
        proxy = config(fixture)
        ip, _ = await proxy_check.resolve_exit_ip(proxy, timeout=2)
        assert ip == "203.0.113.19"
        options = {"geoip": True}
        await proxy_check.prepare_proxy_geoip(proxy, options)
        assert options["geoip"] == ip
        if protocol.startswith("socks"):
            assert ("connect", b"cpm-origin.invalid", fixture.https_port) in [
                event[:3] for event in fixture.events
            ]

    assert not fixture.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,auth",
    [("http", True), ("https", True), ("socks4", False), ("socks5", False), ("socks5", True)],
)
async def test_rejection_cannot_reach_an_origin_or_fall_back_directly(
    certs, monkeypatch, protocol, auth
):
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    async with proxy_fixture(protocol, certs, auth=auth, reject=True) as fixture:
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"https://cpm-origin.invalid:{fixture.https_port}/ip",)
        )
        with pytest.raises(ConnectionError) as caught:
            await proxy_check.resolve_exit_ip(config(fixture), timeout=1)
        assert fixture.password not in str(caught.value)
        assert not any(event[0] == "origin" for event in fixture.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "https", "socks4", "socks5"])
async def test_untrusted_destination_or_proxy_certificate_is_never_ignored(
    certs, monkeypatch, protocol
):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    async with proxy_fixture(protocol, certs) as fixture:
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"https://cpm-origin.invalid:{fixture.https_port}/ip",)
        )
        with pytest.raises(ConnectionError):
            await proxy_check.resolve_exit_ip(config(fixture), timeout=1)
        assert not any(event[0] == "origin" for event in fixture.events)
        options = {"geoip": True}
        with pytest.raises(ConnectionError, match="Check the proxy"):
            await proxy_check.prepare_proxy_geoip(config(fixture), options)
        assert options["geoip"] is True


@pytest.mark.asyncio
async def test_socks4_ipv4_and_unsupported_ipv6_are_bounded_and_no_dns_fallback(certs):
    async with proxy_fixture("socks4", certs) as fixture:
        relay = SocksRelay(f"socks4://127.0.0.1:{fixture.proxy_port}")
        await relay.start()
        try:
            for kind, address, success in [
                (1, bytes([203, 0, 113, 10]), True),
                (4, bytes.fromhex("20010db8000000000000000000000001"), False),
            ]:
                reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
                writer.write(b"\x05\x01\x00")
                await writer.drain()
                assert await reader.readexactly(2) == b"\x05\x00"
                writer.write(
                    b"\x05\x01\x00" + bytes([kind]) + address + fixture.http_port.to_bytes(2, "big")
                )
                await writer.drain()
                reply = await asyncio.wait_for(reader.readexactly(10), 1)
                assert (reply[1] == 0) is success
                writer.close()
                await writer.wait_closed()
        finally:
            await relay.close()
        assert ("connect", b"203.0.113.10", fixture.http_port, b"") in fixture.events
        assert not any(event[0] == "origin" for event in fixture.events)


@pytest.mark.asyncio
async def test_socks4_credentials_have_a_safe_actionable_error_without_connecting(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("Invalid credentials reached the network")

    monkeypatch.setattr(asyncio, "open_connection", unexpected)
    result = await proxy_check.check(
        ProxyConfig(
            type="socks4",
            server="proxy.invalid:1080",
            username="private-user",
            password="private-password",
        ),
        BrowserSettings(),
    )
    assert not result.reachable and "Use SOCKS5" in result.error
    assert "private-user" not in str(result) and "private-password" not in str(result)


def test_aliases_are_canonical_and_preserve_percent_encoded_credentials():
    values = parse_proxy_import(
        "socks5h://us%40er:pa%3Ass@proxy.example:1080\nsocks4a://proxy.example:1081"
    )
    assert (
        values[0][1].type is ProxyType.SOCKS5
        and values[0][1].username == "us@er"
        and values[0][1].password == "pa:ss"
    )
    assert values[1][1].type is ProxyType.SOCKS4
    assert [value.model_dump(mode="json")["type"] for _, value in values] == ["socks5", "socks4"]
    with pytest.raises(ValueError, match="SOCKS4 username/password"):
        parse_proxy_import("socks4a://private-user:private-password@proxy.example:1080")


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "https", "socks5"])
async def test_ipv6_destinations_are_forwarded_through_the_proxy(certs, monkeypatch, protocol):
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    async with proxy_fixture(protocol, certs) as fixture:
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"https://[2001:db8::19]:{fixture.https_port}/ip",)
        )
        assert (await proxy_check.resolve_exit_ip(config(fixture), timeout=2))[0] == "203.0.113.19"
        if protocol == "socks5":
            assert ("connect", b"2001:db8::19", fixture.https_port) in fixture.events


@pytest.mark.asyncio
async def test_cancelling_a_socks4_health_check_releases_the_temporary_listener(certs, monkeypatch):
    created = []
    original = proxy_check.SocksRelay

    def observed(*args, **kwargs):
        relay = original(*args, **kwargs)
        created.append(relay)
        return relay

    monkeypatch.setattr(proxy_check, "SocksRelay", observed)
    async with proxy_fixture("socks4", certs) as fixture:
        # A successful tunnel with no origin response lets cancellation happen
        # while the temporary listener and its upstream connection are active.
        connected = asyncio.Event()

        async def stall(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            connected.set()
            await reader.read()

        fixture.origin = stall
        # Existing listener closures bind self.origin at start, so replace only
        # this test's handler by starting a separate controlled origin.
        fixture.http_port = await fixture.listen(stall)
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"http://cpm-origin.invalid:{fixture.http_port}/ip",)
        )
        task = asyncio.create_task(proxy_check.resolve_exit_ip(config(fixture), timeout=5))
        await asyncio.wait_for(connected.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(created) == 1 and created[0]._closed and not created[0]._tasks
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", created[0].port)


@pytest.mark.asyncio
async def test_failed_geoip_on_explicit_coordinates_never_exposes_host_timezone(monkeypatch):
    async def refuse(*args, **kwargs):
        raise ConnectionError("private-provider-detail")

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", refuse)
    options = {"geoip": False, "config": {"geolocation:latitude": 12, "geolocation:longitude": 30}}
    with pytest.raises(ConnectionError, match="Check the proxy") as caught:
        await proxy_check.fill_what_geoip_would_have(
            ProxyConfig(type="http", server="proxy.example:8080"), options
        )
    assert "private-provider-detail" not in str(caught.value)
    assert "timezone" not in options["config"]


@pytest.mark.asyncio
async def test_missing_location_resources_block_proxied_coordinates_without_implicit_timezone(
    monkeypatch,
):
    async def answer(*args, **kwargs):
        return "203.0.113.19", 1

    monkeypatch.setattr(proxy_check, "resolve_exit_ip", answer)

    def unavailable(ip):
        raise proxy_check.LocationUnavailable("private-path")

    monkeypatch.setattr(proxy_check, "locate", unavailable)
    options = {"geoip": False, "config": {"geolocation:latitude": 12, "geolocation:longitude": 30}}
    with pytest.raises(ConnectionError, match="Install browser location resources"):
        await proxy_check.fill_what_geoip_would_have(
            ProxyConfig(type="socks4", server="proxy.example:1080"), options
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["identity", "gzip"])
async def test_ip_lookup_caps_raw_bytes_without_decompression_or_extra_reads(monkeypatch, encoding):
    import httpx

    closed = []
    reads = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            reads.append(True)
            yield b"x" * 1024
            pytest.fail("The oversized body must not be read further")

        async def aclose(self):
            closed.append(True)

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Encoding": encoding}, stream=Body())

    client_class = httpx.AsyncClient

    def client(**kwargs):
        return client_class(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(proxy_check.httpx, "AsyncClient", client)
    with pytest.raises(ConnectionError):
        await proxy_check.resolve_exit_ip(None)
    assert len(closed) == len(proxy_check.IP_ENDPOINTS)
    assert len(reads) == (len(proxy_check.IP_ENDPOINTS) if encoding == "identity" else 0)


@pytest.mark.asyncio
async def test_trusted_ca_does_not_bypass_hostname_validation(certs, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    async with proxy_fixture("http", certs) as fixture:
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"https://wrong-name.invalid:{fixture.https_port}/ip",)
        )
        with pytest.raises(ConnectionError, match="TLS certificate"):
            await proxy_check.resolve_exit_ip(config(fixture), timeout=2)
        assert not any(event[0] == "origin" for event in fixture.events)
