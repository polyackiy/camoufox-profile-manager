"""Build-time OpenSSL selection uses source dependencies, never arbitrary latest."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def selector():
    path = Path(__file__).resolve().parents[2] / "packaging" / "openssl_bundle.py"
    spec = importlib.util.spec_from_file_location("openssl_bundle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.select_openssl_pair


@pytest.fixture
def collected():
    return [
        ("cryptography/hazmat/bindings/_rust.abi3.so", "/bindings", "EXTENSION"),
        ("libssl.3.dylib", "/python/old/libssl.3.dylib", "BINARY"),
        ("libcrypto.3.dylib", "/python/old/libcrypto.3.dylib", "BINARY"),
    ]


def test_dynamic_pair_comes_from_rust_dependencies(selector, collected, tmp_path):
    ssl = tmp_path / "libssl.3.dylib"
    crypto = tmp_path / "libcrypto.3.dylib"
    ssl.touch()
    crypto.touch()
    selected = selector(collected, lambda path: [(ssl.name, str(ssl)), (crypto.name, str(crypto))])
    assert selected[0] == collected[0]
    assert selected[1][1] == str(ssl.resolve())
    assert selected[2][1] == str(crypto.resolve())
    assert collected[1][1] == "/python/old/libssl.3.dylib"


def test_static_cryptography_wheel_keeps_python_pair(selector, collected):
    assert selector(collected, lambda path: []) == collected


def test_missing_pair_and_cross_installation_pair_refused(selector, collected, tmp_path):
    ssl = tmp_path / "libssl.3.dylib"
    ssl.touch()
    with pytest.raises(RuntimeError, match="complete dynamic"):
        selector(collected, lambda path: [(ssl.name, str(ssl))])
    other = tmp_path / "other"
    other.mkdir()
    crypto = other / "libcrypto.3.dylib"
    crypto.touch()
    with pytest.raises(RuntimeError, match="different installations"):
        selector(collected, lambda path: [(ssl.name, str(ssl)), (crypto.name, str(crypto))])
