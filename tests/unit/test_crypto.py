"""Tests for proxy-credential encryption at rest."""

from cryptography.fernet import Fernet

from camoufox_pm.config import get_settings
from camoufox_pm.core import crypto


def test_roundtrip_with_key(monkeypatch):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    token = crypto.encrypt("secret-pass")
    assert token != "secret-pass"
    assert crypto.decrypt(token) == "secret-pass"


def test_identity_without_key(monkeypatch):
    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    assert crypto.encrypt("x") == "x"
    assert crypto.decrypt("x") == "x"


def test_decrypt_plaintext_is_tolerated(monkeypatch):
    """A value stored before a key was configured must remain readable."""
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    assert crypto.decrypt("legacy-plaintext") == "legacy-plaintext"


def teardown_module(_module):
    get_settings.cache_clear()
