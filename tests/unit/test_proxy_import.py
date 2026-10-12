"""Proxy import preserves credentials without exposing them in diagnostics."""

import pytest

from camoufox_pm.core.proxy_import import normalize_proxy_server, parse_proxy_import


def test_protocols_credentials_and_line_numbers():
    entries = parse_proxy_import(
        "\nEXAMPLE.org:8080\n\nsocks5://user:p%40ss%3Aword@[2001:db8::1]:1080"
    )
    assert [number for number, _ in entries] == [2, 4]
    assert entries[0][1].server == "example.org:8080"
    assert entries[0][1].type.value == "http"
    assert entries[1][1].password == "p@ss:word"
    assert entries[1][1].server == "[2001:db8::1]:1080"


@pytest.mark.parametrize(
    "server",
    [
        "host",
        "host:0",
        "host:65536",
        "user:pass@host:80",
        "host:80/path",
        "host:80?x",
        "host:80#x",
        "host:8 0",
        "2001:db8::1:80",
        "999.2.3.4:80",
        "-host:80",
        "[fe80::1%en0]:80",
        "[::1]suffix:80",
        "host:+80",
        "host:８０",
    ],
)
def test_invalid_servers(server):
    with pytest.raises(ValueError):
        normalize_proxy_server(server)


@pytest.mark.parametrize("tail", ["/path", "?query", "#fragment", "/", "?", "#"])
def test_url_errors_do_not_echo_credentials(tail):
    with pytest.raises(ValueError) as error:
        parse_proxy_import("good.test:80\nhttp://user:private-secret@host:80" + tail)
    assert "Line 2" in str(error.value)
    assert "private-secret" not in str(error.value)


@pytest.mark.parametrize("password", ["bad%", "bad%GG", "%FF", "%00", "%0A", "%7F"])
def test_invalid_encoded_credentials(password):
    with pytest.raises(ValueError):
        parse_proxy_import(f"socks5://user:{password}@host:1080")


def test_bounds_and_empty_import():
    for text in ("\n ", "host:80\n" * 101, "x" * 64001, "x" * 2049):
        with pytest.raises(ValueError):
            parse_proxy_import(text)
    assert len(parse_proxy_import("host:80\n" * 100)) == 100


def test_idna_and_ipv6_normalization():
    assert normalize_proxy_server("BÜCHER.example:443") == "xn--bcher-kva.example:443"
    assert normalize_proxy_server("[2001:0db8:0:0:0:0:0:1]:1080") == "[2001:db8::1]:1080"


@pytest.mark.parametrize("scheme", ["http", "https", "socks4", "socks5"])
def test_supported_protocols(scheme):
    assert parse_proxy_import(f"{scheme}://localhost:1080")[0][1].type.value == scheme
