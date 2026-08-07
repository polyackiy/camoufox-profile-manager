"""Tests for core data models."""

from camoufox_pm.core.models import ProxyConfig, ProxyType, generate_short_id


def test_short_id_shape_and_uniqueness():
    ids = {generate_short_id() for _ in range(1000)}
    assert len(ids) == 1000  # all unique
    for value in ids:
        assert len(value) == 8
        assert not (set(value) & set("0o1li"))  # no confusing characters


def test_proxy_to_camoufox_format():
    proxy = ProxyConfig(type=ProxyType.SOCKS5, server="1.2.3.4:1080", username="u", password="p")
    result = proxy.to_camoufox_format()
    assert result == {"server": "socks5://1.2.3.4:1080", "username": "u", "password": "p"}


def test_proxy_without_credentials_omits_them():
    proxy = ProxyConfig(type=ProxyType.HTTP, server="1.2.3.4:8080")
    assert proxy.to_camoufox_format() == {"server": "http://1.2.3.4:8080"}
