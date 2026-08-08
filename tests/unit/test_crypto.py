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


def test_an_encrypted_value_with_no_key_configured_is_handed_back_as_it_is(monkeypatch):
    """Losing CPM_SECRET_KEY must not make every profile unreadable.

    The password is useless in this state, but the profile still opens and the
    log says what to set to get it back.
    """
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    token = crypto.encrypt("secret-pass")

    monkeypatch.delenv("CPM_SECRET_KEY")
    get_settings.cache_clear()

    assert crypto.decrypt(token) == token


def test_a_value_encrypted_under_another_key_is_handed_back_as_it_is(monkeypatch):
    """Same for a rotated key: one unreadable password, not a broken instance."""
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    token = crypto.encrypt("secret-pass")

    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    assert crypto.decrypt(token) == token


def teardown_module(_module):
    get_settings.cache_clear()
