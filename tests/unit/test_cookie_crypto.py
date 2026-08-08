"""The same ``v10`` tag means a different cipher on Windows than on macOS/Linux.

Reading the tag and picking one algorithm is what broke Chrome cookie migration
on Windows. Fixtures here are built with the real layouts, so every platform's
path is exercised from any machine.
"""

import hashlib
import os
import uuid

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from extras.chrome_migration.cookie_crypto import (
    CookieDecryptionError,
    decrypt_aes_cbc,
    decrypt_aes_gcm,
    decrypt_cookie_value,
)


def windows_blob(value: str, key: bytes, prefix: bytes = b"v10") -> bytes:
    """[v10|v11][nonce:12][ciphertext][tag:16], AES-256-GCM — Chrome on Windows."""
    nonce = os.urandom(12)
    return prefix + nonce + AESGCM(key).encrypt(nonce, value.encode(), None)


def cbc_blob(value: str, key: bytes, prefix: bytes = b"v10") -> bytes:
    """[v10|v11][ciphertext], AES-128-CBC, sixteen-space IV — Chrome elsewhere."""
    pad = 16 - len(value.encode()) % 16
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return prefix + encryptor.update(value.encode() + bytes([pad]) * pad) + encryptor.finalize()


@pytest.mark.parametrize("prefix", [b"v10", b"v11"])
def test_a_windows_cookie_round_trips(prefix):
    key = os.urandom(32)
    value = "session=abc123"

    assert decrypt_aes_gcm(windows_blob(value, key, prefix), key) == value


@pytest.mark.parametrize("length", [4, 20, 36, 52])
def test_windows_lengths_that_used_to_decrypt_to_rubbish(length):
    """The body is the nonce plus the ciphertext plus the tag, so a plaintext of
    length 4 mod 16 made it a whole number of AES blocks. CBC then "succeeded"
    and returned random bytes as the cookie value — one cookie in sixteen, and a
    36-character session UUID is exactly one of them."""
    key = os.urandom(32)
    value = str(uuid.uuid4()) if length == 36 else "s" * length
    blob = windows_blob(value, key)
    assert (len(blob) - 3) % 16 == 0, "this fixture must hit the aligned case"

    assert decrypt_aes_gcm(blob, key) == value


@pytest.mark.parametrize("prefix", [b"v10", b"v11"])
def test_a_cbc_cookie_round_trips(prefix):
    """v11 differs from v10 only in where the password comes from, never in the
    cipher. Treating it as "12-byte IV then CBC" meant v11 never decrypted on any
    platform, because CBC requires a 16-byte IV."""
    key = os.urandom(16)
    value = "session=abc123-real-cookie-value"

    assert decrypt_aes_cbc(cbc_blob(value, key, prefix), key) == value


def test_a_wrong_gcm_key_is_refused():
    """GCM authenticates, so the wrong key is caught outright."""
    with pytest.raises(CookieDecryptionError):
        decrypt_aes_gcm(windows_blob("session=abc", os.urandom(32)), os.urandom(32))


def test_a_wrong_cbc_key_is_refused_by_its_padding():
    """CBC has no tag, so the padding check is the only thing standing between a
    wrong key and a plausible-looking value being written to the profile."""
    encryptor = Cipher(algorithms.AES(os.urandom(16)), modes.CBC(b" " * 16)).encryptor()
    body = encryptor.update(b"y" * 32) + encryptor.finalize()

    with pytest.raises(CookieDecryptionError):
        decrypt_aes_cbc(b"v10" + body, os.urandom(16))


def test_bytes_that_are_not_utf8_are_refused():
    """A key that happens to survive the padding check must still not produce a
    mangled string: decoding strictly is the last integrity signal CBC has."""
    key = os.urandom(16)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    body = encryptor.update(b"\xff\xfe\xfd\xfc" + bytes([12]) * 12) + encryptor.finalize()

    with pytest.raises(CookieDecryptionError):
        decrypt_aes_cbc(b"v10" + body, key)


@pytest.mark.parametrize("prefix", [b"v20", b"v99", b"abc"])
def test_an_unsupported_version_is_refused(prefix):
    """v20 is App-Bound Encryption and belongs to another path entirely."""
    with pytest.raises(CookieDecryptionError):
        decrypt_aes_cbc(prefix + b"\x00" * 32, os.urandom(16))


def test_a_truncated_windows_value_is_refused():
    with pytest.raises(CookieDecryptionError):
        decrypt_aes_gcm(b"v10" + os.urandom(8), os.urandom(32))


def test_a_cbc_body_that_is_not_whole_blocks_is_refused():
    with pytest.raises(CookieDecryptionError):
        decrypt_aes_cbc(b"v10" + os.urandom(30), os.urandom(16))


def test_the_platform_chooses_the_cipher():
    """The dispatch itself: the same value, encrypted the way each platform does
    it, decrypts only when the system matches."""
    value = "session=abc123"
    win_key, cbc_key = os.urandom(32), os.urandom(16)

    assert decrypt_cookie_value(windows_blob(value, win_key), win_key, "windows") == value
    assert decrypt_cookie_value(cbc_blob(value, cbc_key), cbc_key, "darwin") == value
    assert decrypt_cookie_value(cbc_blob(value, cbc_key), cbc_key, "linux") == value

    with pytest.raises(CookieDecryptionError):
        decrypt_cookie_value(windows_blob(value, win_key), win_key, "darwin")


# --- the cookie-store schema, which is a second layer on top of the cipher ---


def v24_plaintext(value: str, host_key: str) -> bytes:
    """From schema 24 Chrome encrypts SHA256(host_key) || value, not the value."""
    return hashlib.sha256(host_key.encode()).digest() + value.encode()


@pytest.mark.parametrize("system", ["windows", "darwin", "linux"])
def test_a_schema_24_cookie_decrypts_on_every_platform(system):
    """The prefix belongs to the cookie store, not to any cipher, so it applies
    to GCM and CBC alike. Getting the cipher right and missing this recovered
    zero cookies from any Chrome newer than late 2024."""
    host_key, value = ".example.com", "SID=abcdefghijklmnop"
    key = os.urandom(32 if system == "windows" else 16)
    plaintext = v24_plaintext(value, host_key)

    if system == "windows":
        nonce = os.urandom(12)
        blob = b"v10" + nonce + AESGCM(key).encrypt(nonce, plaintext, None)
    else:
        pad = 16 - len(plaintext) % 16
        enc = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
        blob = b"v10" + enc.update(plaintext + bytes([pad]) * pad) + enc.finalize()

    assert decrypt_cookie_value(blob, key, system, host_key) == value


def test_the_domain_digest_is_verified_not_assumed():
    """Chrome drops a cookie whose digest does not match its own domain, and so
    does this: blindly stripping 32 bytes would hand back a truncated value."""
    key = os.urandom(32)
    plaintext = v24_plaintext("SID=abc", ".example.com")
    nonce = os.urandom(12)
    blob = b"v10" + nonce + AESGCM(key).encrypt(nonce, plaintext, None)

    with pytest.raises(CookieDecryptionError):
        decrypt_cookie_value(blob, key, "windows", ".attacker.example")


def test_a_pre_schema_24_cookie_keeps_its_whole_value():
    """Passing no host_key means "this store has no prefix". Stripping anyway
    would take 32 bytes off the front of every real value."""
    key = os.urandom(32)

    assert decrypt_cookie_value(windows_blob("SID=abc", key), key, "windows") == "SID=abc"


def test_the_digest_gives_cbc_the_integrity_check_it_lacks():
    """PKCS#7 padding validates by chance about once in 256 tries, and CBC has no
    tag. A digest over the row's own domain closes that gap."""
    plaintext = v24_plaintext("SID=abc", ".example.com")
    pad = 16 - len(plaintext) % 16
    enc = Cipher(algorithms.AES(os.urandom(16)), modes.CBC(b" " * 16)).encryptor()
    blob = b"v10" + enc.update(plaintext + bytes([pad]) * pad) + enc.finalize()

    with pytest.raises(CookieDecryptionError):
        decrypt_cookie_value(blob, os.urandom(16), "darwin", ".example.com")


@pytest.mark.parametrize("system", ["cygwin_nt-10.0", "freebsd", ""])
def test_an_unknown_platform_is_refused_rather_than_assumed_to_be_cbc(system):
    """Under Cygwin or MSYS platform.system() is not "windows", and silently
    treating a Windows profile as CBC would skip every cookie it has."""
    key = os.urandom(32)

    with pytest.raises(CookieDecryptionError):
        decrypt_cookie_value(windows_blob("SID=abc", key), key, system)


def test_a_cbc_key_of_the_wrong_length_is_refused_not_raised_raw():
    """The module's contract is CookieDecryptionError; a bare ValueError from the
    cipher would escape callers that catch only ours."""
    with pytest.raises(CookieDecryptionError):
        decrypt_aes_cbc(cbc_blob("SID=abc", os.urandom(16)), os.urandom(20))
