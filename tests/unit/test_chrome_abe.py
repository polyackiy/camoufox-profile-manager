"""
Unit tests for the pure parts of Chrome App-Bound Encryption (ABE) handling.

These exercise the parsing, master-key unwrap and cookie decryption in
``extras/chrome_migration/abe.py`` with synthetic fixtures built here to the
documented v20 format. They run on any platform: no Windows syscall, no real
Chrome data. The Windows-only DPAPI/CNG layer (``abe_windows.py``) is not covered
here because it needs an elevated Windows host; see the module docstring.
"""

import base64
import os
import struct

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from extras.chrome_migration import abe
from extras.chrome_migration.cookie_decryptor import ChromeCookieDecryptor


def _build_key_blob(
    flag: int,
    nonce: bytes,
    ciphertext: bytes,
    tag: bytes,
    header: bytes = b"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    encrypted_aes_key: bytes | None = None,
) -> bytes:
    """Assemble a plaintext key blob in the documented layout.

    ``[u32 header_len][header][u32 content_len][flag]<body>`` where body is the
    flag-specific tail. Mirrors the format parsed by ``abe.parse_key_blob``.
    """
    content = bytes([flag])
    if encrypted_aes_key is not None:
        content += encrypted_aes_key
    content += nonce + ciphertext + tag
    return struct.pack("<I", len(header)) + header + struct.pack("<I", len(content)) + content


def _wrap_master_key(aead, master_key: bytes) -> tuple[bytes, bytes]:
    """Encrypt a 32-byte master key, returning (ciphertext, tag) split."""
    nonce = os.urandom(12)
    ct_and_tag = aead.encrypt(nonce, master_key, None)
    return nonce, ct_and_tag


def _encrypt_v20_cookie(master_key: bytes, value: str) -> bytes:
    """Build a v20 encrypted_value: prefix + nonce + AES-256-GCM(prefix32 + value)."""
    plaintext = os.urandom(32) + value.encode("utf-8")
    nonce = os.urandom(12)
    ct_and_tag = AESGCM(master_key).encrypt(nonce, plaintext, None)
    return abe.V20_COOKIE_PREFIX + nonce + ct_and_tag


# --- decode_app_bound_key ---------------------------------------------------


def test_decode_app_bound_key_strips_appb_prefix():
    payload = b"wrapped-dpapi-blob"
    encoded = base64.b64encode(abe.APP_BOUND_KEY_PREFIX + payload).decode()
    assert abe.decode_app_bound_key(encoded) == payload


def test_decode_app_bound_key_rejects_wrong_prefix():
    encoded = base64.b64encode(b"XXXXsomething").decode()
    with pytest.raises(abe.V20DecryptionError):
        abe.decode_app_bound_key(encoded)


# --- parse_key_blob ---------------------------------------------------------


def test_parse_key_blob_flag1_roundtrip():
    nonce, ciphertext, tag = os.urandom(12), os.urandom(32), os.urandom(16)
    blob = _build_key_blob(1, nonce, ciphertext, tag)
    parsed = abe.parse_key_blob(blob)
    assert parsed.flag == 1
    assert parsed.nonce == nonce
    assert parsed.ciphertext == ciphertext
    assert parsed.tag == tag
    assert parsed.encrypted_aes_key is None


def test_parse_key_blob_flag3_has_encrypted_aes_key():
    enc_key = os.urandom(32)
    nonce, ciphertext, tag = os.urandom(12), os.urandom(32), os.urandom(16)
    blob = _build_key_blob(3, nonce, ciphertext, tag, encrypted_aes_key=enc_key)
    parsed = abe.parse_key_blob(blob)
    assert parsed.flag == 3
    assert parsed.encrypted_aes_key == enc_key
    assert parsed.nonce == nonce


def test_parse_key_blob_length_mismatch_rejected():
    blob = _build_key_blob(1, os.urandom(12), os.urandom(32), os.urandom(16))
    with pytest.raises(abe.V20DecryptionError):
        abe.parse_key_blob(blob + b"trailing")


def test_parse_key_blob_unsupported_flag_rejected():
    blob = _build_key_blob(9, os.urandom(12), os.urandom(32), os.urandom(16))
    with pytest.raises(abe.V20DecryptionError):
        abe.parse_key_blob(blob)


# --- unwrap_master_key ------------------------------------------------------


def test_unwrap_master_key_flag1_aes_gcm():
    master_key = os.urandom(32)
    nonce, ct_and_tag = _wrap_master_key(AESGCM(abe._FLAG1_AES_KEY), master_key)
    parsed = abe.ParsedKeyBlob(
        header=b"", flag=1, nonce=nonce, ciphertext=ct_and_tag[:32], tag=ct_and_tag[32:]
    )
    assert abe.unwrap_master_key(parsed) == master_key


def test_unwrap_master_key_flag2_chacha20():
    master_key = os.urandom(32)
    nonce, ct_and_tag = _wrap_master_key(ChaCha20Poly1305(abe._FLAG2_CHACHA20_KEY), master_key)
    parsed = abe.ParsedKeyBlob(
        header=b"", flag=2, nonce=nonce, ciphertext=ct_and_tag[:32], tag=ct_and_tag[32:]
    )
    assert abe.unwrap_master_key(parsed) == master_key


def test_unwrap_master_key_flag3_uses_injected_cng_and_xor():
    master_key = os.urandom(32)
    # The AES key is the CNG output XOR-ed with the fixed constant. Pick the
    # desired derived key, then make the fake CNG return derived XOR constant so
    # the module's XOR step reproduces it.
    derived_key = os.urandom(32)
    cng_output = bytes(a ^ b for a, b in zip(derived_key, abe._FLAG3_XOR_KEY, strict=True))

    nonce, ct_and_tag = _wrap_master_key(AESGCM(derived_key), master_key)
    parsed = abe.ParsedKeyBlob(
        header=b"",
        flag=3,
        nonce=nonce,
        ciphertext=ct_and_tag[:32],
        tag=ct_and_tag[32:],
        encrypted_aes_key=os.urandom(32),
    )
    result = abe.unwrap_master_key(parsed, cng_decrypt=lambda _: cng_output)
    assert result == master_key


def test_unwrap_master_key_flag3_without_cng_raises():
    parsed = abe.ParsedKeyBlob(
        header=b"",
        flag=3,
        nonce=os.urandom(12),
        ciphertext=os.urandom(32),
        tag=os.urandom(16),
        encrypted_aes_key=os.urandom(32),
    )
    with pytest.raises(abe.V20DecryptionError):
        abe.unwrap_master_key(parsed)


def test_unwrap_master_key_tampered_tag_raises():
    master_key = os.urandom(32)
    nonce, ct_and_tag = _wrap_master_key(AESGCM(abe._FLAG1_AES_KEY), master_key)
    bad_tag = bytes(b ^ 0xFF for b in ct_and_tag[32:])
    parsed = abe.ParsedKeyBlob(
        header=b"", flag=1, nonce=nonce, ciphertext=ct_and_tag[:32], tag=bad_tag
    )
    with pytest.raises(abe.V20DecryptionError):
        abe.unwrap_master_key(parsed)


# --- decrypt_v20_cookie_value ----------------------------------------------


def test_decrypt_v20_cookie_value_strips_32_byte_prefix():
    master_key = os.urandom(32)
    enc_value = _encrypt_v20_cookie(master_key, "session=abc123")
    assert abe.decrypt_v20_cookie_value(enc_value, master_key) == "session=abc123"


def test_decrypt_v20_cookie_value_rejects_wrong_prefix():
    master_key = os.urandom(32)
    with pytest.raises(abe.V20DecryptionError):
        abe.decrypt_v20_cookie_value(b"v10" + os.urandom(40), master_key)


def test_decrypt_v20_cookie_value_wrong_key_raises():
    enc_value = _encrypt_v20_cookie(os.urandom(32), "value")
    with pytest.raises(abe.V20DecryptionError):
        abe.decrypt_v20_cookie_value(enc_value, os.urandom(32))


def test_full_chain_flag1_blob_to_cookie():
    """End to end over the pure chain: key blob -> master key -> cookie value."""
    master_key = os.urandom(32)
    nonce, ct_and_tag = _wrap_master_key(AESGCM(abe._FLAG1_AES_KEY), master_key)
    blob = _build_key_blob(1, nonce, ct_and_tag[:32], ct_and_tag[32:])

    recovered = abe.unwrap_master_key(abe.parse_key_blob(blob))
    assert recovered == master_key

    enc_value = _encrypt_v20_cookie(recovered, "auth=xyz")
    assert abe.decrypt_v20_cookie_value(enc_value, recovered) == "auth=xyz"


# --- ChromeCookieDecryptor honest degradation ------------------------------


def test_decryptor_skips_v20_without_master_key():
    """A v20 cookie with no ABE key must return None (skip), never garbage."""
    decryptor = ChromeCookieDecryptor()
    assert decryptor.abe_master_key is None
    enc_value = _encrypt_v20_cookie(os.urandom(32), "value")
    # The classic key is irrelevant to v20; pass any 16 bytes.
    assert decryptor.decrypt_chrome_cookie_value(enc_value, os.urandom(16)) is None


def test_decryptor_decrypts_v20_with_master_key():
    decryptor = ChromeCookieDecryptor()
    master_key = os.urandom(32)
    decryptor.abe_master_key = master_key
    enc_value = _encrypt_v20_cookie(master_key, "logged_in=yes")
    assert decryptor.decrypt_chrome_cookie_value(enc_value, os.urandom(16)) == "logged_in=yes"
