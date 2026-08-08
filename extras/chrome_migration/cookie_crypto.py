"""How Chrome encrypts a cookie value, per platform.

The ``v10``/``v11`` prefix means different things on different operating systems,
which is the trap this module exists to close:

* **Windows** — AES-256-GCM. Layout ``[v10|v11][nonce:12][ciphertext][tag:16]``.
  The key is the DPAPI-unwrapped ``os_crypt.encrypted_key`` from ``Local State``.
* **macOS and Linux** — AES-128-CBC with an IV of sixteen spaces and PKCS#7
  padding. Layout ``[v10|v11][ciphertext]``. The key is PBKDF2 over a password
  from the Keychain (macOS) or the keyring (Linux); ``v11`` differs from ``v10``
  only in *where that password comes from*, never in the cipher.

Reading the version tag alone and picking one algorithm cannot work. Doing so
decrypted Windows cookies with CBC, which fails outright unless the body happens
to be a multiple of the block size — and then returns plausible-looking rubbish.

Everything here is pure and built on ``cryptography``, a core dependency, so the
whole file is testable on any platform without the ``chrome-migration`` extra.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SUPPORTED_PREFIXES = (b"v10", b"v11")

# Chrome's own constants for the CBC platforms.
_CBC_IV = b" " * 16
_AEAD_NONCE_LEN = 12
_AEAD_TAG_LEN = 16
_BLOCK = 16


class CookieDecryptionError(Exception):
    """A cookie value could not be decrypted, so it must be skipped."""


def _strip_prefix(encrypted_value: bytes) -> bytes:
    prefix = encrypted_value[:3]
    if prefix not in SUPPORTED_PREFIXES:
        raise CookieDecryptionError(f"unsupported cookie version {prefix!r}")
    return encrypted_value[3:]


def _decode(plaintext: bytes) -> str:
    """Decode strictly. CBC has no authentication tag, so a wrong key produces
    random bytes rather than an error; failing to decode is the only signal that
    anything went wrong, and a mangled value is worse than a skipped one."""
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CookieDecryptionError(f"decrypted bytes are not valid UTF-8: {exc}") from exc


def decrypt_aes_gcm(encrypted_value: bytes, key: bytes) -> str:
    """Decrypt a Windows ``v10``/``v11`` cookie value."""
    body = _strip_prefix(encrypted_value)
    if len(body) < _AEAD_NONCE_LEN + _AEAD_TAG_LEN:
        raise CookieDecryptionError("cookie value is too short to hold a nonce and a tag")

    nonce, ciphertext_and_tag = body[:_AEAD_NONCE_LEN], body[_AEAD_NONCE_LEN:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext_and_tag, None)
    except Exception as exc:  # InvalidTag, wrong key length, and friends
        raise CookieDecryptionError(f"AES-GCM decryption failed: {exc}") from exc
    return _decode(plaintext)


def decrypt_aes_cbc(encrypted_value: bytes, key: bytes) -> str:
    """Decrypt a macOS or Linux ``v10``/``v11`` cookie value."""
    body = _strip_prefix(encrypted_value)
    if not body or len(body) % _BLOCK:
        raise CookieDecryptionError("cookie value is not a whole number of AES blocks")

    decryptor = Cipher(algorithms.AES(key), modes.CBC(_CBC_IV)).decryptor()
    padded = decryptor.update(body) + decryptor.finalize()

    # Validate the padding rather than trusting the last byte. With the wrong key
    # the plaintext is random, and stripping "however many bytes the last one
    # says" would silently truncate or corrupt a value that should be skipped.
    pad = padded[-1]
    if not 1 <= pad <= _BLOCK or padded[-pad:] != bytes([pad]) * pad:
        raise CookieDecryptionError("PKCS#7 padding is invalid; the key is probably wrong")
    return _decode(padded[:-pad])


def decrypt_cookie_value(encrypted_value: bytes, key: bytes, system: str) -> str:
    """Decrypt a ``v10``/``v11`` cookie value for the platform that wrote it.

    ``system`` is ``platform.system().lower()``.
    """
    if system == "windows":
        return decrypt_aes_gcm(encrypted_value, key)
    return decrypt_aes_cbc(encrypted_value, key)
